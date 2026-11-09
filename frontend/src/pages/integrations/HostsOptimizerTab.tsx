/**
 * 网络优化 Tab（应用集成）
 *
 * 通过 AdGuard Home 的 DNS 重写能力，把指定域名解析到实测最快的 IP，
 * 并按应用注入 DNS。支持手动触发与定时自动运行。
 */

import { useEffect, useMemo, useState } from "react";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { PasswordInput } from "@/components/ui/password-input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { RevealSecretDialog } from "@/components/RevealSecretDialog";
import { AppDeployDialog } from "@/pages/apps/AppDeployDialog";
import { AccelerationAppDialog } from "@/pages/integrations/AccelerationAppDialog";
import { useApps } from "@/hooks/useApps";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  useDeployHostsAdguard,
  useHostsAccelerationOverview,
  useHostsOptimizerConfig,
  useHostsOptimizerStatus,
  useRunHostsOptimizer,
  useRemoveHostsAdguard,
  useSyncHostsManagedRules,
  useTestHostsOptimizer,
  useToggleAppAcceleration,
  useUpdateHostsOptimizerConfig,
  usePortSuggestions,
  type HostsOptimizerConfig,
} from "@/hooks/useSettings";
import { useLicenseStatus } from "@/hooks/useLicense";
import {
  CheckCircle2,
  ChevronDown,
  Crown,
  ExternalLink,
  Globe2,
  Loader2,
  Network,
  Play,
  Plus,
  RefreshCw,
  Save,
  Server,
  ShieldCheck,
  Sparkles,
  Trash2,
  XCircle,
  Zap,
} from "lucide-react";
import type { AccelerationApp } from "@/api/settings";

const INPUT_CLASS =
  "h-8 rounded-md border-input bg-white px-2 text-sm shadow-none";
const TEXTAREA_CLASS =
  "min-h-[140px] w-full rounded-md border border-input bg-white px-2 py-1.5 text-sm shadow-none outline-none focus-visible:border-foreground/60";

/** OneTap 托管的 AdGuard 容器名；重建时应忽略它自己占用的端口。 */
const ADGUARD_CONTAINER_NAME = "onetap-adguardhome";

interface FormState {
  enabled: boolean;
  interval_minutes: number;
  top_n: number;
  domainsText: string;
  adguard_url: string;
  adguard_username: string;
  adguard_password: string;
}

const EMPTY_FORM: FormState = {
  enabled: false,
  interval_minutes: 360,
  top_n: 2,
  domainsText: "",
  adguard_url: "",
  adguard_username: "admin",
  adguard_password: "",
};

/** 开关即模式：开启后按间隔自动运行，关闭后只能手动触发。 */
function modeOf(enabled: boolean): "manual" | "auto" {
  return enabled ? "auto" : "manual";
}

function toForm(config: HostsOptimizerConfig): FormState {
  return {
    enabled: config.enabled,
    interval_minutes: config.interval_minutes,
    top_n: config.top_n,
    domainsText: config.domains.join("\n"),
    adguard_url: config.adguard_url ?? "",
    adguard_username: config.adguard_username,
    adguard_password: "",
  };
}

function toPayload(form: FormState): HostsOptimizerConfig {
  return {
    enabled: form.enabled,
    mode: modeOf(form.enabled),
    interval_minutes: form.interval_minutes,
    top_n: form.top_n,
    domains: form.domainsText
      .split(/[\n,]/)
      .map((item) => item.trim())
      .filter(Boolean),
    adguard_url: form.adguard_url.trim() || null,
    adguard_username: form.adguard_username.trim() || "admin",
    adguard_password: form.adguard_password,
  };
}

/** 只取出用户可编辑的字段，避免后端附加的只读状态让表单永远显示“未保存”。 */
function editableConfig(config: HostsOptimizerConfig): HostsOptimizerConfig {
  return {
    enabled: config.enabled,
    mode: modeOf(config.enabled),
    interval_minutes: config.interval_minutes,
    top_n: config.top_n,
    domains: config.domains,
    adguard_url: config.adguard_url,
    adguard_username: config.adguard_username,
    adguard_password: "",
  };
}

function formatTime(value: string | null | undefined): string {
  if (!value) return "-";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function DomainStatusBadge({ status }: { status: string }) {
  if (status === "managed") {
    return (
      <Badge className="bg-sky-500/10 text-sky-700">管理端指定</Badge>
    );
  }
  if (status === "ok") {
    return (
      <Badge className="bg-emerald-500/10 text-emerald-700">可用</Badge>
    );
  }
  return (
    <Badge className="bg-amber-500/10 text-amber-700">不可达</Badge>
  );
}

export function HostsOptimizerTab() {
  const { data: config, isLoading, error } = useHostsOptimizerConfig();
  const { data: status } = useHostsOptimizerStatus();
  const updateConfig = useUpdateHostsOptimizerConfig();
  const testConnection = useTestHostsOptimizer();
  const runOptimizer = useRunHostsOptimizer();
  const { data: acceleration } = useHostsAccelerationOverview();
  const { data: marketApps, isLoading: marketAppsLoading } = useApps();
  const adguardApp =
    marketApps?.find((app) => app.name === "adguardhome") ?? null;
  const toggleAcceleration = useToggleAppAcceleration();
  const deployAdguard = useDeployHostsAdguard();
  const removeAdguard = useRemoveHostsAdguard();
  const syncRules = useSyncHostsManagedRules();
  const { data: license } = useLicenseStatus();

  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [initialized, setInitialized] = useState(false);
  const [showRules, setShowRules] = useState(false);
  const [showResults, setShowResults] = useState(false);
  const [showManualAdguard, setShowManualAdguard] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [revealDialogOpen, setRevealDialogOpen] = useState(false);
  const [deployDialogOpen, setDeployDialogOpen] = useState(false);
  const [addDialogOpen, setAddDialogOpen] = useState(false);
  const [pendingDisable, setPendingDisable] = useState<AccelerationApp | null>(
    null
  );
  const [batchProgress, setBatchProgress] = useState<{
    done: number;
    total: number;
  } | null>(null);

  useEffect(() => {
    if (config && !initialized) {
      setForm(toForm(config));
      setInitialized(true);
    }
  }, [config, initialized]);

  // 自动部署 AdGuard 后配置会在后台更新；表单从未填写过地址时自动带入，
  // 避免用户还要手动复制地址和密码。
  useEffect(() => {
    if (!config?.adguard_url) return;
    setForm((prev) =>
      prev.adguard_url
        ? prev
        : {
            ...prev,
            adguard_url: config.adguard_url ?? "",
            adguard_username: config.adguard_username,
            adguard_password: "",
          }
    );
  }, [config]);

  const formPayload = toPayload(form);
  // 托管实例的服务地址由后端维护；保存时始终沿用服务端最新值，
  // 避免宿主机 IP 变化后表单里的旧地址被写回。
  const payload =
    config?.adguard_managed
      ? { ...formPayload, adguard_url: config.adguard_url }
      : formPayload;
  const isDirty =
    !!config &&
    JSON.stringify(payload) !== JSON.stringify(editableConfig(config));

  const running = status?.running || runOptimizer.isPending;
  // 状态接口是唯一来源；运行结果只做兜底，避免两份数据不一致。
  const lastRun = status?.last_run ?? runOptimizer.data ?? null;
  const enabledApps = useMemo(
    () => acceleration?.apps.filter((app) => app.acceleration_enabled) ?? [],
    [acceleration]
  );
  const availableApps = useMemo(
    () => acceleration?.apps.filter((app) => !app.acceleration_enabled) ?? [],
    [acceleration]
  );
  const subscriptionRuleCount = useMemo(
    () =>
      acceleration?.managed_rules.rules.filter(
        (rule) => rule.tier === "subscription"
      ).length ?? 0,
    [acceleration]
  );
  const purchaseUrl = license?.purchase_url ?? null;
  const acceleratedCount = enabledApps.length;
  const canRun =
    !isDirty &&
    !!config?.adguard_url &&
    !!config?.adguard_password_set &&
    !running;

  // 重建时优先沿用 AdGuard 当前端口；端口被其他服务占用时自动换空闲端口。
  const adguardPreferredPorts = useMemo(() => {
    const setup = acceleration?.adguard.setup_port;
    const web = acceleration?.adguard.web_port;
    if (!setup || !web) return [];
    return [setup, web];
  }, [acceleration?.adguard.setup_port, acceleration?.adguard.web_port]);
  const {
    data: adguardPortSuggestions,
    isLoading: adguardPortsLoading,
  } = usePortSuggestions(2, {
    preferred: adguardPreferredPorts,
    excludeContainer: ADGUARD_CONTAINER_NAME,
  });
  const adguardSetupPort =
    adguardPortSuggestions?.[0] ?? adguardPreferredPorts[0] ?? 3000;
  const adguardWebPort =
    adguardPortSuggestions?.[1] ?? adguardPreferredPorts[1] ?? 8080;
  // 托管实例的浏览器访问地址由后端维护；配置表单里只读展示。
  const managedWebUrl =
    acceleration?.adguard.web_url ?? config?.adguard_url ?? "";

  // 应用市场安装弹窗的预填配置：只保留初始化和管理两个端口，不映射 53。
  const adguardInitialConfig = useMemo<Record<string, unknown>>(
    () => ({
      container_images: {
        adguardhome: { repository: "adguard/adguardhome", tag: "v0.107.79" },
      },
      ports: [
        {
          protocol: "tcp",
          container_port: 3000,
          local_port: adguardSetupPort,
        },
        {
          protocol: "tcp",
          container_port: 80,
          local_port: adguardWebPort,
        },
      ],
      privileged: false,
    }),
    [adguardSetupPort, adguardWebPort]
  );

  const startDeployAdguard = () => {
    // 应用市场里有 AdGuard 时走标准安装弹窗；目录缺失时回退到后端自动配置。
    if (adguardApp) setDeployDialogOpen(true);
    else deployAdguard.mutate(undefined);
  };

  const handleAdguardDeploy = (data: {
    instance_name: string;
    config: Record<string, unknown>;
  }) => {
    deployAdguard.mutate(
      { instance_name: data.instance_name, config: data.config },
      { onSuccess: () => setDeployDialogOpen(false) }
    );
  };

  const handleAddApps = async (instanceIds: number[]) => {
    setAddDialogOpen(false);
    if (instanceIds.length === 0) return;
    setBatchProgress({ done: 0, total: instanceIds.length });
    try {
      for (const [index, instanceId] of instanceIds.entries()) {
        try {
          await toggleAcceleration.mutateAsync({
            instanceId,
            enabled: true,
          });
        } catch {
          // 单个应用失败时已由 mutation 弹出提示，继续处理其余应用。
        }
        setBatchProgress({ done: index + 1, total: instanceIds.length });
      }
    } finally {
      setBatchProgress(null);
    }
  };

  const handleSave = () => {
    updateConfig.mutate(payload, {
      onSuccess: (data) => {
        setForm(toForm(data));
        setInitialized(true);
      },
    });
  };

  const handleTest = () => {
    testConnection.mutate({
      adguard_url: form.adguard_url.trim() || null,
      adguard_username: form.adguard_username.trim() || "admin",
      adguard_password: form.adguard_password || undefined,
    });
  };

  if (isLoading) {
    return <div className="text-sm text-muted-foreground">加载配置中...</div>;
  }
  if (error) {
    return (
      <div className="text-sm text-destructive">
        加载失败：{error instanceof Error ? error.message : "未知错误"}
      </div>
    );
  }

  return (
    <Card className="rounded-xl">
      <CardHeader className="border-b">
        <CardTitle className="flex items-center gap-2 text-base">
          <Globe2 className="h-4 w-4 text-muted-foreground" />
          网络优化
          {subscriptionRuleCount > 0 ? (
            <Badge className="bg-violet-500/10 text-violet-700">
              专属加速 · {subscriptionRuleCount} 个域名
            </Badge>
          ) : acceleration?.managed_rules.configured ? (
            <Badge variant="secondary">免费线路</Badge>
          ) : null}
        </CardTitle>
        <CardDescription className="text-xs">
          OneTap 可以自动部署专用 AdGuard Home，通过 DNS 重写把域名解析到指定 IP，
          或优选到实测最快的 IP。加速只作用于选中的应用。
        </CardDescription>
      </CardHeader>

      {/* AdGuard Home */}
      <CardHeader className="pb-0">
        <CardTitle className="flex items-center gap-2 text-base">
          <ShieldCheck className="h-4 w-4 text-muted-foreground" />
          AdGuard Home
          {config?.adguard_managed && (
            <Badge
              className={
                acceleration?.adguard.running
                  ? "bg-emerald-500/10 text-emerald-700"
                  : "bg-amber-500/10 text-amber-700"
              }
            >
              {acceleration?.adguard.running ? "运行中" : "未运行"}
            </Badge>
          )}
        </CardTitle>
        <CardDescription className="text-xs">
          {config?.adguard_managed
            ? "由 OneTap 托管，账号密码保存后自动同步到实例。"
            : "填写已有 AdGuard Home 的地址和登录凭据。"}
        </CardDescription>
        <CardAction>
          <div className="flex items-center gap-2">
            {config?.adguard_managed && (
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="text-destructive hover:text-destructive"
                onClick={() => setConfirmRemove(true)}
                disabled={removeAdguard.isPending || deployAdguard.isPending}
              >
                {removeAdguard.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Trash2 className="h-4 w-4" />
                )}
                关闭并删除
              </Button>
            )}
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={startDeployAdguard}
              disabled={
                deployAdguard.isPending ||
                removeAdguard.isPending ||
                marketAppsLoading ||
                adguardPortsLoading
              }
              title={
                adguardPortsLoading ? "正在检测可用端口" : undefined
              }
            >
              {deployAdguard.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : config?.adguard_managed ? (
                <RefreshCw className="h-4 w-4" />
              ) : (
                <Server className="h-4 w-4" />
              )}
              {config?.adguard_managed ? "重新部署" : "部署 AdGuard"}
            </Button>
          </div>
        </CardAction>
      </CardHeader>
      <CardContent className="space-y-3">
        {!config?.adguard_managed && !config?.adguard_url && !showManualAdguard ? (
          <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-dashed p-3">
            <div className="space-y-1">
              <div className="text-sm font-medium">尚未配置 AdGuard Home</div>
              <p className="text-xs text-muted-foreground">
                推荐直接点右上角「部署 AdGuard」，OneTap 会创建专用实例并自动完成初始化，
                不需要填写地址和账号密码。
              </p>
            </div>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={() => setShowManualAdguard(true)}
            >
              使用已有 AdGuard
            </Button>
          </div>
        ) : (
          <div className="space-y-3 rounded-lg border p-3">
            <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,0.9fr)_minmax(0,0.9fr)_minmax(0,1.1fr)]">
              <div className="space-y-1">
                <Label htmlFor="adguard_url" className="text-xs">
                  服务地址
                </Label>
                <Input
                  id="adguard_url"
                  value={
                    config?.adguard_managed
                      ? managedWebUrl
                      : form.adguard_url
                  }
                  onChange={(e) =>
                    setForm((prev) => ({
                      ...prev,
                      adguard_url: e.target.value,
                    }))
                  }
                  disabled={config?.adguard_managed}
                  placeholder="http://192.168.1.23:8081"
                  className={INPUT_CLASS}
                />
              </div>

              <div className="space-y-1">
                <Label htmlFor="adguard_username" className="text-xs">
                  登录账号
                </Label>
                <Input
                  id="adguard_username"
                  value={form.adguard_username}
                  onChange={(e) =>
                    setForm((prev) => ({
                      ...prev,
                      adguard_username: e.target.value,
                    }))
                  }
                  placeholder="admin"
                  className={INPUT_CLASS}
                />
              </div>

              <div className="space-y-1">
                <Label htmlFor="adguard_password" className="text-xs">
                  登录密码
                </Label>
                <div className="flex min-w-0 items-center gap-2">
                  <PasswordInput
                    id="adguard_password"
                    value={form.adguard_password}
                    onChange={(e) =>
                      setForm((prev) => ({
                        ...prev,
                        adguard_password: e.target.value,
                      }))
                    }
                    placeholder={
                      config?.adguard_password_set
                        ? "••••••••"
                        : "AdGuard 管理员密码"
                    }
                    onToggleClick={() => {
                      if (
                        !form.adguard_password &&
                        config?.adguard_password_set
                      ) {
                        setRevealDialogOpen(true);
                        return true;
                      }
                      return false;
                    }}
                    containerClassName="min-w-0 flex-1"
                    className={INPUT_CLASS}
                  />
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={handleTest}
                    disabled={testConnection.isPending}
                    className="h-8 shrink-0"
                  >
                    {testConnection.isPending ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <Globe2 className="h-3.5 w-3.5" />
                    )}
                    测试
                  </Button>
                </div>
              </div>

              <div className="flex min-w-0 items-end justify-start">
                {testConnection.data ? (
                  <div
                    className={`flex items-start gap-1.5 pb-2 text-[11px] ${
                      testConnection.data.ok
                        ? "text-emerald-600"
                        : "text-destructive"
                    }`}
                  >
                    {testConnection.data.ok ? (
                      <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                    ) : (
                      <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                    )}
                    <span>
                      {testConnection.data.message}
                      {testConnection.data.version
                        ? `（${testConnection.data.version}）`
                        : ""}
                    </span>
                  </div>
                ) : (
                  <span className="pb-2 text-left text-[11px] text-muted-foreground">
                    尚未测试连接
                  </span>
                )}
              </div>
            </div>

            <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border pt-2.5">
              <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-xs text-muted-foreground">
                <span>
                  DNS 地址：
                  <code className="ml-1 rounded bg-muted px-1 py-0.5">
                    {acceleration?.adguard.dns_ip
                      ? `${acceleration.adguard.dns_ip}:53`
                      : "-"}
                  </code>
                </span>
                {acceleration?.adguard.fallback_dns?.length ? (
                  <span>
                    备用 DNS：
                    <code className="ml-1 rounded bg-muted px-1 py-0.5">
                      {acceleration.adguard.fallback_dns.join(" / ")}
                    </code>
                  </span>
                ) : null}
                <span>
                  加速网络：
                  <code className="ml-1 rounded bg-muted px-1 py-0.5">
                    {acceleration?.adguard.network ?? "onetap-accel"}
                  </code>
                </span>
                {acceleration?.adguard.web_url && (
                  <a
                    href={acceleration.adguard.web_url}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1 text-primary hover:underline"
                  >
                    AdGuard 管理界面
                    <ExternalLink className="h-3 w-3" />
                  </a>
                )}
              </div>
              <div className="flex items-center gap-2">
                {!config?.adguard_url && (
                  <button
                    type="button"
                    onClick={() => setShowManualAdguard(false)}
                    className="text-xs text-muted-foreground hover:text-foreground"
                  >
                    取消，改用自动部署
                  </button>
                )}
                <Button
                  type="button"
                  size="sm"
                  onClick={handleSave}
                  disabled={!isDirty || updateConfig.isPending}
                  className="rounded-md"
                >
                  {updateConfig.isPending ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Save className="h-4 w-4" />
                  )}
                  {updateConfig.isPending ? "保存中..." : "保存配置"}
                </Button>
              </div>
            </div>
          </div>
        )}
      </CardContent>

      <div className="border-t border-border" />

      {/* 应用加速 */}
      <CardHeader className="pb-0">
        <CardTitle className="flex items-center gap-2 text-base">
          <Zap className="h-4 w-4 text-muted-foreground" />
          应用加速
        </CardTitle>
        <CardDescription className="text-xs">
          只给添加的应用注入 DNS，不改宿主机或其他应用。列表只显示已开启加速的应用，
          需要时再按应用添加。容器内 53 无需映射；给局域网用可在安装弹窗加「宿主端口 → 53」。
        </CardDescription>
        <CardAction>
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => setAddDialogOpen(true)}
            disabled={!acceleration || batchProgress !== null}
          >
            {batchProgress ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Plus className="h-4 w-4" />
            )}
            {batchProgress
              ? `配置中 ${batchProgress.done}/${batchProgress.total}`
              : "添加应用"}
          </Button>
        </CardAction>
      </CardHeader>
      <CardContent className="space-y-3">
        {!acceleration ? (
          <p className="text-sm text-muted-foreground">加载应用列表...</p>
        ) : acceleration.apps.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            还没有已部署的应用。先从应用市场部署应用，再回来添加加速。
          </p>
        ) : enabledApps.length === 0 ? (
          <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed py-8">
            <Zap className="h-7 w-7 text-muted-foreground/50" />
            <p className="text-sm text-muted-foreground">还没有应用开启加速</p>
            <p className="text-xs text-muted-foreground">
              添加后只对这个应用生效，可随时关闭。
            </p>
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() => setAddDialogOpen(true)}
              disabled={batchProgress !== null}
            >
              <Plus className="h-4 w-4" />
              添加应用
            </Button>
          </div>
        ) : (
          <div className="rounded-lg border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>应用</TableHead>
                  <TableHead>Compose 服务</TableHead>
                  <TableHead>状态</TableHead>
                  <TableHead className="w-[110px] text-right">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {enabledApps.map((app) => {
                  const pending =
                    toggleAcceleration.isPending &&
                    toggleAcceleration.variables?.instanceId ===
                      app.instance_id;
                  return (
                    <TableRow key={app.instance_id}>
                      <TableCell>
                        <div className="font-medium">{app.display_name}</div>
                        <div className="text-xs text-muted-foreground">
                          {app.project_name ?? app.instance_name}
                        </div>
                        {app.orchestration_name && (
                          <div className="text-[11px] text-muted-foreground">
                            {app.orchestration_name}
                            {app.orchestration_group_name
                              ? ` · ${app.orchestration_group_name}`
                              : ""}
                          </div>
                        )}
                      </TableCell>
                      <TableCell>
                        {app.services.length > 0 ? (
                          <div className="flex flex-wrap gap-1">
                            {app.services.slice(0, 3).map((service) => (
                              <Badge
                                key={service}
                                variant="secondary"
                                className="font-mono text-[11px]"
                              >
                                {service}
                              </Badge>
                            ))}
                            {app.services.length > 3 && (
                              <Badge variant="secondary" className="text-[11px]">
                                +{app.services.length - 3}
                              </Badge>
                            )}
                          </div>
                        ) : (
                          <span className="text-xs text-muted-foreground">-</span>
                        )}
                      </TableCell>
                      <TableCell>
                        <Badge
                          className={
                            app.status === "running"
                              ? "bg-emerald-500/10 text-emerald-700"
                              : "bg-amber-500/10 text-amber-700"
                          }
                        >
                          {app.status === "running"
                            ? "运行中"
                            : app.status === "stopped"
                              ? "已停止"
                              : app.status}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-right">
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          className="text-destructive hover:text-destructive"
                          disabled={pending || batchProgress !== null}
                          onClick={() => setPendingDisable(app)}
                        >
                          {pending ? (
                            <Loader2 className="h-3.5 w-3.5 animate-spin" />
                          ) : (
                            <XCircle className="h-3.5 w-3.5" />
                          )}
                          关闭
                        </Button>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
        )}
      </CardContent>

      <div className="border-t border-border" />

      {/* 加速规则 */}
      <CardHeader className="pb-0">
        <CardTitle className="flex items-center gap-2 text-base">
          <Network className="h-4 w-4 text-muted-foreground" />
          加速规则
        </CardTitle>
        <CardDescription className="text-xs">
          {acceleration?.managed_rules.configured
            ? "域名和解析结果由配套管理端统一维护，同步后写入 AdGuard 的 DNS 重写。"
            : "未配置 MANAGE_BASE_URL，当前使用下方本地优选域名；配置管理端后规则会改为统一下发。"}
        </CardDescription>
        <CardAction>
          {acceleration?.managed_rules.configured && (
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() => syncRules.mutate()}
              disabled={syncRules.isPending}
            >
              {syncRules.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <RefreshCw className="h-4 w-4" />
              )}
              同步规则
            </Button>
          )}
        </CardAction>
      </CardHeader>
      <CardContent className="space-y-3">
        {acceleration && acceleration.managed_rules.rules.length > 0 ? (
          <>
            <div className="flex items-center justify-between gap-2">
              <p className="text-xs text-muted-foreground">
                共 {acceleration.managed_rules.rules.length} 个域名
                {acceleration.managed_rules.synced_at
                  ? ` · 最近同步 ${formatTime(acceleration.managed_rules.synced_at)}`
                  : ""}
              </p>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                className="h-7 px-2 text-xs"
                onClick={() => setShowRules((value) => !value)}
              >
                {showRules ? "收起" : "展开"}
                <ChevronDown
                  className={`h-3.5 w-3.5 transition-transform ${showRules ? "rotate-180" : ""}`}
                />
              </Button>
            </div>
            {acceleration.managed_rules.configured &&
              (subscriptionRuleCount > 0 ? (
                <div className="flex items-start gap-3 rounded-lg border border-violet-200 bg-violet-50 px-3 py-2.5">
                  <Crown className="mt-0.5 h-4 w-4 shrink-0 text-violet-600" />
                  <div className="flex-1 space-y-0.5">
                    <div className="flex flex-wrap items-center gap-2 text-sm font-semibold text-violet-900">
                      {acceleration.network_effective === true
                        ? "专属加速已生效"
                        : acceleration.network_effective === null
                          ? "专属加速状态未知"
                          : "专属加速未生效"}
                      <Badge className="bg-violet-600 text-white">
                        {subscriptionRuleCount} 个专属域名
                      </Badge>
                    </div>
                    <p className="text-xs text-violet-800/80">
                      {acceleration.network_message}
                    </p>
                  </div>
                </div>
              ) : (
                <div className="flex items-start gap-3 rounded-lg border border-amber-200 bg-amber-50 px-3 py-2.5">
                  <Sparkles className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
                  <div className="flex-1 space-y-0.5">
                    <div className="flex flex-wrap items-center gap-2 text-sm font-semibold text-amber-900">
                      免费线路
                      <Badge className="bg-amber-500/15 text-amber-700">
                        本机优选
                      </Badge>
                    </div>
                  </div>
                  {purchaseUrl && (
                    <Button
                      type="button"
                      size="sm"
                      className="shrink-0 bg-violet-600 text-white hover:bg-violet-700"
                      onClick={() =>
                        window.open(
                          purchaseUrl,
                          "_blank",
                          "noopener,noreferrer"
                        )
                      }
                    >
                      购买
                    </Button>
                  )}
                </div>
              ))}
            {showRules && (
              <div className="max-h-72 overflow-auto rounded-lg border">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>域名</TableHead>
                      <TableHead>线路</TableHead>
                      <TableHead>说明</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {acceleration.managed_rules.rules.map((rule) => (
                      <TableRow key={rule.domain}>
                        <TableCell className="font-mono text-xs">
                          {rule.domain}
                        </TableCell>
                        <TableCell>
                          {rule.tier === "subscription" ? (
                            <Badge className="bg-violet-500/10 text-violet-700">
                              专属
                            </Badge>
                          ) : (
                            <Badge variant="secondary">免费</Badge>
                          )}
                        </TableCell>
                        <TableCell className="max-w-[260px] text-xs text-muted-foreground">
                          {rule.description ?? "-"}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            )}
          </>
        ) : (
          <p className="text-sm text-muted-foreground">
            {acceleration?.managed_rules.configured
              ? "暂无加速规则，点「同步规则」从管理端拉取。"
              : "暂无加速规则，先在下方填写优选域名并保存。"}
          </p>
        )}
      </CardContent>

      <div className="border-t border-border" />

      {/* 优选规则 */}
      <CardHeader className="pb-0">
        <CardTitle className="flex items-center gap-2 text-base">
          <Globe2 className="h-4 w-4 text-muted-foreground" />
          优选规则
        </CardTitle>
        <CardDescription className="text-xs">
          本机探测最快 IP 后写入 AdGuard；开启自动优选会按间隔在后台刷新。
        </CardDescription>
        <CardAction>
          <Button
            type="button"
            size="sm"
            onClick={() => runOptimizer.mutate()}
            disabled={!canRun}
            title={isDirty ? "请先保存配置" : undefined}
          >
            {running ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <Play className="h-4 w-4" />
            )}
            {running ? "优选进行中..." : "立即优选"}
          </Button>
        </CardAction>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="flex flex-wrap items-center gap-x-5 gap-y-3">
          <div className="flex items-center gap-2">
            <Label htmlFor="hosts_enabled" className="text-sm font-medium">
              自动优选
            </Label>
            <Switch
              id="hosts_enabled"
              checked={form.enabled}
              onCheckedChange={(checked) =>
                setForm((prev) => ({ ...prev, enabled: checked }))
              }
            />
          </div>
          <div className="flex items-center gap-2">
            <Label htmlFor="hosts_interval" className="text-sm font-medium">
              间隔
            </Label>
            <Input
              id="hosts_interval"
              type="number"
              min={5}
              max={10080}
              value={form.interval_minutes}
              disabled={!form.enabled}
              onChange={(e) =>
                setForm((prev) => ({
                  ...prev,
                  interval_minutes:
                    Number.parseInt(e.target.value, 10) || 360,
                }))
              }
              className={`${INPUT_CLASS} w-24`}
            />
            <span className="text-xs text-muted-foreground">分钟</span>
          </div>
          <div className="flex items-center gap-2">
            <Label htmlFor="hosts_top_n" className="text-sm font-medium">
              每域名保留
            </Label>
            <Input
              id="hosts_top_n"
              type="number"
              min={1}
              max={5}
              value={form.top_n}
              onChange={(e) =>
                setForm((prev) => ({
                  ...prev,
                  top_n: Number.parseInt(e.target.value, 10) || 2,
                }))
              }
              className={`${INPUT_CLASS} w-20`}
            />
            <span className="text-xs text-muted-foreground">个 IP</span>
          </div>
        </div>

        {isDirty && (
          <p className="text-xs text-amber-600">
            配置有未保存的修改，保存后才能运行优选。
          </p>
        )}

        <p className="text-xs text-muted-foreground">
          最近运行 {formatTime(lastRun?.finished_at)}
          {lastRun ? ` · 耗时 ${(lastRun.duration_ms / 1000).toFixed(1)} 秒` : ""}
          {" · "}下次自动运行{" "}
          {status?.next_run_at ? formatTime(status.next_run_at) : "-"}
        </p>

        {lastRun ? (
          <>
            <div className="flex flex-wrap items-center justify-between gap-2">
              <div className="flex flex-wrap items-center gap-2 text-sm">
                <span className="text-xs text-muted-foreground">
                  AdGuard 同步
                </span>
                {lastRun.adguard === "ok" ? (
                  <Badge className="bg-emerald-500/10 text-emerald-700">
                    成功
                  </Badge>
                ) : (
                  <Badge variant="destructive">失败</Badge>
                )}
                {lastRun.errors.length > 0 && (
                  <span className="text-xs text-destructive">
                    {lastRun.errors.join("；")}
                  </span>
                )}
              </div>
              {Object.keys(lastRun.domains).length > 0 && (
                <div className="flex items-center gap-2">
                  <span className="text-xs text-muted-foreground">
                    {Object.keys(lastRun.domains).length} 个域名 ·{" "}
                    {
                      Object.values(lastRun.domains).filter(
                        (result) => result.status !== "unreachable"
                      ).length
                    }{" "}
                    个可用
                  </span>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    className="h-7 px-2 text-xs"
                    onClick={() => setShowResults((value) => !value)}
                  >
                    {showResults ? "收起" : "展开"}
                    <ChevronDown
                      className={`h-3.5 w-3.5 transition-transform ${showResults ? "rotate-180" : ""}`}
                    />
                  </Button>
                </div>
              )}
            </div>

            {Object.keys(lastRun.domains).length > 0 ? (
              showResults && (
                <div className="max-h-72 overflow-auto rounded-lg border">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>域名</TableHead>
                        <TableHead>状态</TableHead>
                        <TableHead>优选结果</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {Object.entries(lastRun.domains).map(
                        ([domain, result]) => (
                          <TableRow key={domain}>
                            <TableCell className="font-mono text-xs">
                              {domain}
                            </TableCell>
                            <TableCell>
                              <DomainStatusBadge status={result.status} />
                            </TableCell>
                            <TableCell>
                              {result.ip_count ? (
                                <span className="text-xs text-muted-foreground">
                                  {result.ip_count} 个 IP
                                  {result.best_latency_ms != null
                                    ? ` · 最快 ${Math.round(result.best_latency_ms)}ms`
                                    : ""}
                                </span>
                              ) : (
                                <span className="text-xs text-muted-foreground">
                                  无可用 IP
                                </span>
                              )}
                            </TableCell>
                          </TableRow>
                        )
                      )}
                    </TableBody>
                  </Table>
                </div>
              )
            ) : (
              <p className="text-sm text-muted-foreground">暂无域名结果</p>
            )}
          </>
        ) : (
          <p className="text-sm text-muted-foreground">
            还没有运行记录，保存配置后点击「立即优选」。
          </p>
        )}

        {!acceleration?.managed_rules.configured && (
          <div className="space-y-1.5">
            <Label htmlFor="hosts_domains" className="text-xs font-medium">
              优选域名
            </Label>
            <textarea
              id="hosts_domains"
              value={form.domainsText}
              onChange={(e) =>
                setForm((prev) => ({ ...prev, domainsText: e.target.value }))
              }
              placeholder={"api.themoviedb.org\nimage.tmdb.org"}
              className={TEXTAREA_CLASS}
            />
            <p className="text-xs text-muted-foreground">
              每行一个域名，也可用英文逗号分隔，保存时自动去重并校验格式；从列表移除域名不会删除
              AdGuard 中已有的重写。
            </p>
          </div>
        )}
      </CardContent>

      <div className="border-t border-border" />

      <div className="flex justify-end px-4">
        <Button
          type="button"
          onClick={handleSave}
          disabled={!isDirty || updateConfig.isPending}
          className="rounded-md"
        >
          {updateConfig.isPending ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Save className="h-4 w-4" />
          )}
          {updateConfig.isPending ? "保存中..." : "保存配置"}
        </Button>
      </div>

      <ConfirmDialog
        open={confirmRemove}
        onOpenChange={setConfirmRemove}
        title="删除 AdGuard Home"
        description={
          acceleratedCount > 0
            ? `删除前会先关闭 ${acceleratedCount} 个应用的加速并恢复原网络配置，然后把 AdGuard 数据备份到应用数据目录的 _backups 下。重新部署时会从空数据开始。`
            : "将停止并移除 OneTap 托管的 AdGuard Home，并把数据备份到应用数据目录的 _backups 下。重新部署时会从空数据开始。"
        }
        confirmText="删除"
        destructive
        isPending={removeAdguard.isPending}
        onConfirm={() => {
          removeAdguard.mutate(undefined, {
            onSuccess: () => {
              setForm((prev) => ({
                ...prev,
                adguard_url: "",
                adguard_username: "admin",
                adguard_password: "",
              }));
              setShowManualAdguard(false);
              setConfirmRemove(false);
            },
          });
        }}
      />

      <ConfirmDialog
        open={pendingDisable !== null}
        onOpenChange={(open) => {
          if (!open) setPendingDisable(null);
        }}
        title="关闭应用加速"
        description={
          pendingDisable
            ? `将移除 ${pendingDisable.display_name} 的 DNS 配置并重启容器，恢复原网络配置。`
            : ""
        }
        confirmText="关闭加速"
        destructive
        isPending={toggleAcceleration.isPending}
        onConfirm={() => {
          if (!pendingDisable) return;
          toggleAcceleration.mutate(
            { instanceId: pendingDisable.instance_id, enabled: false },
            { onSuccess: () => setPendingDisable(null) }
          );
        }}
      />

      <AccelerationAppDialog
        open={addDialogOpen}
        onOpenChange={setAddDialogOpen}
        apps={availableApps}
        onConfirm={handleAddApps}
      />

      <AppDeployDialog
        app={adguardApp}
        open={deployDialogOpen}
        onOpenChange={setDeployDialogOpen}
        onDeploy={handleAdguardDeploy}
        isDeploying={deployAdguard.isPending}
        initialInstanceName="onetap-adguard"
        initialConfig={adguardInitialConfig}
        lockInstanceName
      />

      <RevealSecretDialog
        open={revealDialogOpen}
        onOpenChange={setRevealDialogOpen}
        provider="adguard"
        title="查看 AdGuard 登录密码"
      />
    </Card>
  );
}
