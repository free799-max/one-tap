/**
 * 网络加速 Tab（应用集成）
 *
 * 规则与节点由管理端统一下发，客户端只保存订阅地址并把订阅同步到本机
 * Mihomo；命中规则的域名走加速节点，其余请求保持直连。
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
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  CheckCircle2,
  Loader2,
  Network,
  Plus,
  RefreshCw,
  Save,
  Server,
  Trash2,
  XCircle,
  Zap,
} from "lucide-react";
import { formatRelativeTime } from "@/lib/utils";
import {
  useAccelApps,
  useAccelManaged,
  useDeployAccelMihomo,
  useRemoveAccelMihomo,
  useSetAccelAppProxy,
  useSyncAccelManaged,
  useUpdateAccelSyncSettings,
} from "@/hooks/useAcceleration";
import { AccelerationAppDialog } from "@/pages/integrations/AccelerationAppDialog";
import { AppDeployDialog } from "@/pages/apps/AppDeployDialog";
import { useApps } from "@/hooks/useApps";
import type { AccelProxyApp } from "@/api/acceleration";

const SUPPORTED_APPS = [
  { name: "moviepilot", displayName: "MoviePilot" },
  { name: "mediary-scout", displayName: "巡影 Mediary Scout" },
  { name: "emby", displayName: "Emby" },
  { name: "jellyfin", displayName: "Jellyfin" },
  { name: "plex", displayName: "Plex" },
  { name: "qbittorrent", displayName: "qBittorrent" },
  { name: "transmission", displayName: "Transmission" },
];

function Stat({ label, value, hint }: { label: string; value: string; hint?: string }) {
  return (
    <div className="rounded-lg border border-border px-3 py-2">
      <div className="text-xs text-muted-foreground">{label}</div>
      <div className="mt-1 text-lg font-semibold">{value}</div>
      {hint ? <div className="mt-0.5 text-xs text-muted-foreground">{hint}</div> : null}
    </div>
  );
}

export function AccelerationTab() {
  const managed = useAccelManaged();
  const sync = useSyncAccelManaged();
  const updateSyncSettings = useUpdateAccelSyncSettings();
  const deploy = useDeployAccelMihomo();
  const remove = useRemoveAccelMihomo();
  const appsQuery = useAccelApps();
  const { data: marketApps, isLoading: marketAppsLoading } = useApps();
  const mihomoApp = marketApps?.find((app) => app.name === "mihomo") ?? null;
  const supportedApps = useMemo(
    () =>
      SUPPORTED_APPS.map(({ name, displayName }) => {
        const marketApp = marketApps?.find((app) => app.name === name);
        return {
          name,
          display_name: marketApp?.display_name ?? displayName,
          icon: marketApp?.icon ?? null,
        };
      }),
    [marketApps]
  );
  const setProxy = useSetAccelAppProxy();
  const [addDialogOpen, setAddDialogOpen] = useState(false);
  const [deployDialogOpen, setDeployDialogOpen] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [pendingDisable, setPendingDisable] = useState<AccelProxyApp | null>(null);
  const [autoSyncForm, setAutoSyncForm] = useState({
    enabled: true,
    interval_minutes: 30,
  });
  const [batchProgress, setBatchProgress] = useState<{
    done: number;
    total: number;
  } | null>(null);

  const data = managed.data;
  const mihomo = data?.mihomo;
  const apps = appsQuery.data ?? [];
  const enabledApps = useMemo(() => apps.filter((app) => app.proxy_enabled), [apps]);
  const lastResult = data?.last_sync_result;
  const autoSyncDirty = data
    ? autoSyncForm.enabled !== data.auto_sync_enabled ||
      autoSyncForm.interval_minutes !== data.auto_sync_interval_minutes
    : false;

  useEffect(() => {
    if (data) {
      setAutoSyncForm({
        enabled: data.auto_sync_enabled,
        interval_minutes: data.auto_sync_interval_minutes,
      });
    }
  }, [data?.auto_sync_enabled, data?.auto_sync_interval_minutes]);

  const addApps = async (instanceIds: number[]) => {
    setAddDialogOpen(false);
    if (!instanceIds.length) return;
    setBatchProgress({ done: 0, total: instanceIds.length });
    try {
      for (let index = 0; index < instanceIds.length; index += 1) {
        await setProxy.mutateAsync({
          instanceId: instanceIds[index],
          enabled: true,
        });
        setBatchProgress({ done: index + 1, total: instanceIds.length });
      }
    } finally {
      setBatchProgress(null);
    }
  };

  return (
    <div className="space-y-4">
      <Card className="rounded-xl">
        <CardHeader className="border-b">
          <CardTitle className="flex items-center gap-2 text-base">
            <Network className="h-4 w-4 text-muted-foreground" />
            网络加速
          </CardTitle>
          <CardAction>
            <Button
              size="sm"
              onClick={() => sync.mutate()}
              disabled={sync.isPending || !data?.configured}
            >
              {sync.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <RefreshCw className="h-4 w-4" />
              )}
              同步订阅
            </Button>
          </CardAction>
        </CardHeader>
        <CardContent>
          <div className="grid gap-2 sm:grid-cols-3">
            <Stat
              label="订阅状态"
              value={data?.configured ? "已配置" : "未配置"}
              hint={
                data?.last_sync_at
                  ? `最近同步 ${formatRelativeTime(data.last_sync_at)}`
                  : "等待首次同步"
              }
            />
            <Stat
              label="本机 Mihomo"
              value={mihomo?.running ? "运行中" : mihomo?.installed ? "已停止" : "未部署"}
              hint={mihomo?.version || undefined}
            />
            <Stat
              label="加速应用"
              value={appsQuery.data ? String(enabledApps.length) : "-"}
              hint={apps.length ? `共 ${apps.length} 个支持的应用` : "支持 MoviePilot 等"}
            />
          </div>
          {data?.last_sync_error ? (
            <p className="mt-3 rounded-md border border-destructive/30 bg-destructive/5 px-3 py-2 text-xs text-destructive">
              {data.last_sync_error}
            </p>
          ) : null}
        </CardContent>

      <CardHeader className="pb-0">
          <CardTitle className="flex items-center gap-2 text-base">
            <Network className="h-4 w-4 text-muted-foreground" />
            Mihomo
            {mihomo?.installed ? (
              <Badge
                className={
                  mihomo.running
                    ? "bg-emerald-500/10 text-emerald-700"
                    : "bg-amber-500/10 text-amber-700"
                }
              >
                {mihomo.running ? "运行中" : "未运行"}
              </Badge>
            ) : null}
          </CardTitle>
          <CardDescription className="text-xs">
            由 OneTap 托管，自动创建实例并完成配置，为 MoviePilot、Jellyfin、巡影
            等应用提供 HTTP 代理入口，不需要填写任何配置。
          </CardDescription>
          <CardAction>
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                variant="outline"
                onClick={() => {
                  if (mihomoApp) setDeployDialogOpen(true);
                  else deploy.mutate({});
                }}
                disabled={
                  deploy.isPending || remove.isPending || marketAppsLoading
                }
              >
                {deploy.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : mihomo?.installed ? (
                  <RefreshCw className="h-4 w-4" />
                ) : (
                  <Server className="h-4 w-4" />
                )}
                部署
              </Button>
              {mihomo?.installed ? (
                <Button
                  size="sm"
                  variant="outline"
                  className="text-destructive hover:text-destructive"
                  onClick={() => setConfirmRemove(true)}
                  disabled={deploy.isPending || remove.isPending}
                >
                  {remove.isPending ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <Trash2 className="h-4 w-4" />
                  )}
                  删除
                </Button>
              ) : null}
            </div>
          </CardAction>
        </CardHeader>
        <CardContent className="space-y-3">
          {!mihomo?.installed ? (
            <div className="rounded-lg border border-dashed p-3">
              <div className="text-sm font-medium">尚未部署 Mihomo</div>
              <p className="mt-1 text-xs text-muted-foreground">
                直接点右上角「部署 Mihomo」，OneTap 会自动创建专用实例并接管订阅，
                不需要接外部服务。
              </p>
            </div>
          ) : (
            <div className="grid gap-2 sm:grid-cols-3">
              <Stat
                label="容器"
                value={
                  mihomo.running ? "运行中" : mihomo.installed ? "已停止" : "未部署"
                }
              />
              <Stat label="版本" value={mihomo.version || "-"} />
              <Stat
                label="代理端口"
                value={String(mihomo.mixed_port ?? 7890)}
              />
            </div>
          )}
        </CardContent>

      <CardHeader className="pb-0">
          <CardTitle className="flex items-center gap-2 text-base">
            <RefreshCw className="h-4 w-4 text-muted-foreground" />
            自动同步
          </CardTitle>
          <CardDescription className="text-xs">
            按设定间隔拉取管理端订阅，内容变化时才应用并重启 Mihomo。
          </CardDescription>
          <CardAction>
            <Button
              size="sm"
              variant="outline"
              onClick={() => updateSyncSettings.mutate(autoSyncForm)}
              disabled={
                !autoSyncDirty ||
                updateSyncSettings.isPending ||
                autoSyncForm.interval_minutes < 5 ||
                autoSyncForm.interval_minutes > 1440
              }
            >
              {updateSyncSettings.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Save className="h-4 w-4" />
              )}
              保存配置
            </Button>
          </CardAction>
        </CardHeader>
        <CardContent className="space-y-3">
          <div className="flex flex-wrap items-center gap-x-5 gap-y-3">
            <div className="flex items-center gap-2">
              <Label htmlFor="accel_auto_sync" className="text-sm font-medium">
                自动同步
              </Label>
              <Switch
                id="accel_auto_sync"
                checked={autoSyncForm.enabled}
                onCheckedChange={(enabled) =>
                  setAutoSyncForm((current) => ({ ...current, enabled }))
                }
              />
            </div>
            <div className="flex items-center gap-2">
              <Label htmlFor="accel_sync_interval" className="text-sm font-medium">
                同步间隔
              </Label>
              <Input
                id="accel_sync_interval"
                type="number"
                min={5}
                max={1440}
                value={autoSyncForm.interval_minutes}
                disabled={!autoSyncForm.enabled}
                onChange={(event) =>
                  setAutoSyncForm((current) => ({
                    ...current,
                    interval_minutes:
                      Number.parseInt(event.target.value, 10) || 30,
                  }))
                }
                className="h-8 w-24 rounded-md bg-white px-2 text-sm shadow-none"
              />
              <span className="text-xs text-muted-foreground">分钟（5-1440）</span>
            </div>
          </div>

          <div className="rounded-lg border px-3 py-3">
            <div className="flex flex-wrap items-center gap-2">
              {lastResult?.status === "error" ? (
                <XCircle className="h-4 w-4 text-destructive" />
              ) : (
                <CheckCircle2 className="h-4 w-4 text-muted-foreground" />
              )}
              <span className="text-sm font-medium">最近同步结果</span>
              {lastResult ? (
                <Badge
                  variant={lastResult.status === "error" ? "destructive" : "secondary"}
                >
                  {lastResult.status === "success"
                    ? "已更新"
                    : lastResult.status === "unchanged"
                      ? "无变化"
                      : lastResult.status === "error"
                        ? "失败"
                        : "已完成"}
                </Badge>
              ) : null}
            </div>
            <p
              className={`mt-2 text-sm ${
                lastResult?.status === "error" ? "text-destructive" : ""
              }`}
            >
              {lastResult?.message || "尚未执行同步"}
            </p>
            {lastResult ? (
              <div className="mt-1 flex flex-wrap gap-x-3 gap-y-1 text-xs text-muted-foreground">
                <span>
                  {lastResult.synced_at
                    ? formatRelativeTime(lastResult.synced_at)
                    : "时间未知"}
                </span>
                <span>{lastResult.trigger === "auto" ? "自动同步" : "手动同步"}</span>
                {lastResult.changed !== null ? (
                  <span>{lastResult.changed ? "已应用新配置" : "未重启 Mihomo"}</span>
                ) : null}
                {lastResult.revision ? <span>版本 r{lastResult.revision}</span> : null}
              </div>
            ) : null}
          </div>
        </CardContent>

      <CardHeader className="pb-0">
          <CardTitle className="flex items-center gap-2 text-base">
            <Zap className="h-4 w-4 text-muted-foreground" />
            应用加速
          </CardTitle>
          <CardDescription className="text-xs">
            添加后只对这个应用生效，可随时关闭。
          </CardDescription>
          <CardAction>
            <Button
              size="sm"
              variant="outline"
              onClick={() => setAddDialogOpen(true)}
              disabled={batchProgress !== null || !appsQuery.data}
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
          {!appsQuery.data ? (
            <p className="text-sm text-muted-foreground">加载应用列表...</p>
          ) : apps.length === 0 ? (
            <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed py-8">
              <Zap className="h-7 w-7 text-muted-foreground/50" />
              <p className="text-sm text-muted-foreground">还没有支持的应用</p>
              <p className="text-xs text-muted-foreground">
                添加后只对这个应用生效，可随时关闭。
              </p>
              <Button
                size="sm"
                variant="outline"
                onClick={() => setAddDialogOpen(true)}
                disabled={batchProgress !== null}
              >
                <Plus className="h-4 w-4" />
                添加应用
              </Button>
            </div>
          ) : enabledApps.length === 0 ? (
            <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed py-8">
              <Zap className="h-7 w-7 text-muted-foreground/50" />
              <p className="text-sm text-muted-foreground">还没有应用开启加速</p>
              <Button
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
                    <TableHead>Compose 名</TableHead>
                    <TableHead>状态</TableHead>
                    <TableHead className="w-[110px] text-right">操作</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {enabledApps.map((app) => {
                    const pending =
                      setProxy.isPending &&
                      setProxy.variables?.instanceId === app.instance_id;
                    return (
                      <TableRow key={app.instance_id}>
                        <TableCell>
                          <div className="font-medium">{app.display_name}</div>
                          {app.orchestration_name ? (
                            <div className="text-[11px] text-muted-foreground">
                              {app.orchestration_name}
                              {app.orchestration_group_name
                                ? ` · ${app.orchestration_group_name}`
                                : ""}
                            </div>
                          ) : null}
                        </TableCell>
                        <TableCell>
                          <span className="font-mono text-xs">
                            {app.project_name ?? "-"}
                          </span>
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
      </Card>

      <AppDeployDialog
        app={mihomoApp}
        open={deployDialogOpen}
        onOpenChange={setDeployDialogOpen}
        onDeploy={(data) => {
          deploy.mutate(
            { config: data.config },
            { onSuccess: () => setDeployDialogOpen(false) }
          );
        }}
        isDeploying={deploy.isPending}
        initialInstanceName="onetap-mihomo"
        lockInstanceName
      />
      <AccelerationAppDialog
        open={addDialogOpen}
        onOpenChange={setAddDialogOpen}
        apps={apps.filter((app) => !app.proxy_enabled)}
        supportedApps={supportedApps}
        isPending={batchProgress !== null}
        onConfirm={addApps}
        title="添加应用加速"
        description="只给选中的应用注入 HTTP 代理并重启该应用，使其走本机 Mihomo；不会修改宿主机或其他应用。"
      />
      <ConfirmDialog
        open={confirmRemove}
        onOpenChange={setConfirmRemove}
        title="删除 Mihomo"
        description={
          enabledApps.length > 0
            ? `删除前会先关闭 ${enabledApps.length} 个应用的加速并恢复原网络配置，然后删除 Mihomo Compose 项目，并把数据备份到应用数据目录的 _backups 下。授权和自动同步设置会保留。`
            : "将删除 Mihomo Compose 项目，并把数据备份到应用数据目录的 _backups 下。授权和自动同步设置会保留。"
        }
        confirmText="删除"
        destructive
        isPending={remove.isPending}
        onConfirm={() => {
          remove.mutate(undefined, {
            onSuccess: () => setConfirmRemove(false),
          });
        }}
      />
      <ConfirmDialog
        open={pendingDisable !== null}
        onOpenChange={(open) => !open && setPendingDisable(null)}
        title="关闭应用加速"
        description={`确定关闭「${pendingDisable?.display_name ?? ""}」的代理接入吗？会移除该应用的代理环境变量并重启。`}
        confirmText="关闭"
        destructive
        isPending={setProxy.isPending}
        onConfirm={() => {
          if (pendingDisable) {
            setProxy.mutate({
              instanceId: pendingDisable.instance_id,
              enabled: false,
            });
            setPendingDisable(null);
          }
        }}
      />
    </div>
  );
}
