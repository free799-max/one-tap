/**
 * 认证集成 Tab
 *
 * 连接 Vaultwarden 后，把已部署应用的账号密码写入其密码库。
 */

import { useEffect, useState } from "react";
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
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  useDeployVaultwarden,
  useRemoveVaultwarden,
  useSyncVaultwarden,
  useTestVaultwarden,
  useUpdateVaultwarden,
  useVaultwardenApps,
  useVaultwardenConfig,
} from "@/hooks/useIntegrations";
import { useSystemConfig } from "@/hooks/useSettings";
import {
  CheckCircle2,
  ExternalLink,
  Globe2,
  KeyRound,
  Loader2,
  RefreshCw,
  Server,
  ShieldCheck,
  Trash2,
  XCircle,
} from "lucide-react";

const INPUT_CLASS =
  "h-8 rounded-md border-input bg-white px-2 text-sm shadow-none";

interface FormState {
  enabled: boolean;
  url: string;
  email: string;
  password: string;
}

const EMPTY_FORM: FormState = {
  enabled: false,
  url: "",
  email: "",
  password: "",
};

export function AuthIntegrationTab() {
  const { data: config, isLoading, error } = useVaultwardenConfig();
  const { data: systemConfig } = useSystemConfig();
  const { data: apps = [], isLoading: appsLoading } = useVaultwardenApps();
  const updateConfig = useUpdateVaultwarden();
  const deploy = useDeployVaultwarden();
  const remove = useRemoveVaultwarden();
  const testConnection = useTestVaultwarden();
  const sync = useSyncVaultwarden();

  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [initialized, setInitialized] = useState(false);
  const [showManual, setShowManual] = useState(false);
  const [deployDialogOpen, setDeployDialogOpen] = useState(false);
  const [confirmRemove, setConfirmRemove] = useState(false);
  const [revealDialogOpen, setRevealDialogOpen] = useState(false);

  useEffect(() => {
    if (config && !initialized) {
      setForm({
        enabled: config.enabled,
        url: config.url,
        email: config.email,
        password: "",
      });
      setInitialized(true);
    }
  }, [config, initialized]);

  useEffect(() => {
    if (!config?.managed || !config.url) return;
    setForm((prev) => ({
      ...prev,
      enabled: config.enabled,
      url: config.url,
      email: prev.email || config.email,
    }));
  }, [config]);

  const isDirty =
    !!config &&
    (form.enabled !== config.enabled ||
      form.url.trim() !== config.url ||
      form.email.trim() !== config.email ||
      form.password.length > 0);

  const handleSave = () => {
    updateConfig.mutate(
      {
        enabled: form.enabled,
        url: form.url.trim(),
        email: form.email.trim(),
        password: form.password || undefined,
      },
      {
        onSuccess: (data) => {
          setForm({
            enabled: data.enabled,
            url: data.url,
            email: data.email,
            password: "",
          });
        },
      }
    );
  };

  const unifiedUsername = systemConfig?.default_admin_username.trim() || "admin";
  const unifiedEmail = unifiedUsername.includes("@")
    ? unifiedUsername
    : systemConfig?.default_email_domain
      ? `${unifiedUsername}@${systemConfig.default_email_domain}`
      : "";
  const unifiedPasswordConfigured =
    systemConfig?.default_admin_password_set ?? false;
  const canUseUnifiedCredentials = !!(
    unifiedEmail && unifiedPasswordConfigured
  );
  const hasDeployCredentials =
    (!!form.email.trim() &&
      (!!form.password ||
        !!config?.password_set ||
        unifiedPasswordConfigured)) ||
    canUseUnifiedCredentials;

  const handleDeploy = () => {
    if (!hasDeployCredentials) {
      setForm((prev) => ({ ...prev, email: prev.email || unifiedEmail }));
      setDeployDialogOpen(true);
      return;
    }
    deploy.mutate(
      {
        email: form.email.trim() || undefined,
        password: form.password || undefined,
      },
      { onSuccess: () => setDeployDialogOpen(false) }
    );
  };

  const handleTest = () => {
    testConnection.mutate({
      url: form.url.trim() || null,
      email: form.email.trim() || null,
      password: form.password || null,
    });
  };

  const syncableApps = apps.filter((app) => app.has_credentials);
  const canSync =
    !!config?.enabled && !!config?.configured && syncableApps.length > 0;
  const canDeploy = !deploy.isPending && !remove.isPending;

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
    <div className="space-y-4">
      <Card className="rounded-xl">
        <CardHeader className="border-b">
          <CardTitle className="flex items-center gap-2 text-base">
            <KeyRound className="h-4 w-4 text-muted-foreground" />
            认证集成
            <Badge variant="secondary">免费</Badge>
          </CardTitle>
          <CardDescription className="text-xs">
            OneTap 可以自动部署专用 Vaultwarden，把已部署应用的账号密码加密写入密码库。
          </CardDescription>
        </CardHeader>

        <CardHeader className="pb-0">
          <CardTitle className="flex items-center gap-2 text-base">
            <ShieldCheck className="h-4 w-4 text-muted-foreground" />
            Vaultwarden
            {config?.managed && (
              <Badge
                className={
                  config.running
                    ? "bg-emerald-500/10 text-emerald-700"
                    : "bg-amber-500/10 text-amber-700"
                }
              >
                {config.running ? "运行中" : "未运行"}
              </Badge>
            )}
          </CardTitle>
          <CardDescription className="text-xs">
            {config?.managed
              ? "由 OneTap 托管，自动初始化账号并维护服务地址。"
              : "自动部署专用实例，或填写已有 Vaultwarden 的地址和登录凭据。"}
          </CardDescription>
          <CardAction>
            <div className="flex items-center gap-2">
              {config?.managed && (
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  className="text-destructive hover:text-destructive"
                  onClick={() => setConfirmRemove(true)}
                  disabled={remove.isPending || deploy.isPending}
                >
                  {remove.isPending ? (
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
                onClick={handleDeploy}
                disabled={!canDeploy}
              >
                {deploy.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : config?.managed ? (
                  <RefreshCw className="h-4 w-4" />
                ) : (
                  <Server className="h-4 w-4" />
                )}
                {config?.managed ? "重新部署" : "部署 Vaultwarden"}
              </Button>
            </div>
          </CardAction>
        </CardHeader>

        <CardContent className="space-y-3">
          {!config?.managed && !config?.configured && !showManual ? (
            <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-dashed p-3">
              <div className="space-y-1">
                <div className="text-sm font-medium">尚未配置 Vaultwarden</div>
                <p className="text-xs text-muted-foreground">
                  {canUseUnifiedCredentials
                    ? "推荐直接点右上角「部署 Vaultwarden」，OneTap 会使用系统统一账号密码创建实例并完成接入，不需要重复填写。"
                    : "推荐直接点右上角「部署 Vaultwarden」，补充首次登录邮箱和主密码后，OneTap 会创建专用实例并完成接入。"}
                </p>
              </div>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                onClick={() => setShowManual(true)}
              >
                使用已有 Vaultwarden
              </Button>
            </div>
          ) : (
            <div className="space-y-3 rounded-lg border p-3">
              <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)_minmax(0,1fr)_minmax(0,1fr)]">
                <div className="space-y-1">
                  <Label htmlFor="vaultwarden_url" className="text-xs">
                    服务地址
                  </Label>
                  <Input
                    id="vaultwarden_url"
                    value={form.url}
                    onChange={(e) =>
                      setForm((prev) => ({ ...prev, url: e.target.value }))
                    }
                    disabled={config?.managed}
                    placeholder="http://192.168.1.23:8080"
                    className={INPUT_CLASS}
                  />
                </div>

                <div className="space-y-1">
                  <Label htmlFor="vaultwarden_email" className="text-xs">
                    登录邮箱
                  </Label>
                  <Input
                    id="vaultwarden_email"
                    type="email"
                    value={form.email}
                    onChange={(e) =>
                      setForm((prev) => ({ ...prev, email: e.target.value }))
                    }
                    placeholder="admin@example.com"
                    className={INPUT_CLASS}
                  />
                </div>

                <div className="space-y-1">
                  <Label htmlFor="vaultwarden_password" className="text-xs">
                    主密码
                  </Label>
                  <div className="flex min-w-0 items-center gap-2">
                    <PasswordInput
                      id="vaultwarden_password"
                      value={form.password}
                      onChange={(e) =>
                        setForm((prev) => ({ ...prev, password: e.target.value }))
                      }
                      placeholder={
                        config?.password_set
                          ? "••••••••"
                          : "Vaultwarden 主密码"
                      }
                      onToggleClick={() => {
                        if (!form.password && config?.password_set) {
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

              {config?.status.message && (
                <div className="flex items-start gap-1.5 text-[11px] text-muted-foreground">
                  <ShieldCheck className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                  <span>
                    最近状态：{config.status.message}
                    {config.status.last_synced_at
                      ? ` · 上次同步 ${new Date(
                          config.status.last_synced_at
                        ).toLocaleString()}`
                      : ""}
                  </span>
                </div>
              )}

              <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border pt-2.5">
                <div className="flex flex-wrap items-center gap-x-5 gap-y-2 text-xs text-muted-foreground">
                  <div className="flex items-center gap-2">
                    <Switch
                      id="vaultwarden_enabled"
                      checked={form.enabled}
                      onCheckedChange={(checked) =>
                        setForm((prev) => ({ ...prev, enabled: checked }))
                      }
                    />
                    <Label htmlFor="vaultwarden_enabled" className="text-xs">
                      启用认证集成
                    </Label>
                  </div>
                  <span>已同步 {config?.synced_count ?? 0} 个应用</span>
                  {config?.web_url && (
                    <a
                      href={config.web_url}
                      target="_blank"
                      rel="noreferrer"
                      className="inline-flex items-center gap-1 text-primary hover:underline"
                    >
                      Vaultwarden 管理界面
                      <ExternalLink className="h-3 w-3" />
                    </a>
                  )}
                </div>
                <div className="flex items-center gap-2">
                  {!config?.configured && (
                    <button
                      type="button"
                      onClick={() => setShowManual(false)}
                      className="text-xs text-muted-foreground hover:text-foreground"
                    >
                      取消，改用自动部署
                    </button>
                  )}
                  <Button
                    type="button"
                    size="sm"
                    onClick={handleSave}
                    disabled={updateConfig.isPending || !isDirty}
                    className="rounded-md"
                  >
                    {updateConfig.isPending ? "保存中..." : "保存配置"}
                  </Button>
                </div>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      <Card className="rounded-xl">
        <CardHeader className="border-b">
          <CardTitle className="flex items-center gap-2 text-base">
            <KeyRound className="h-4 w-4 text-muted-foreground" />
            已部署应用
          </CardTitle>
          <CardDescription className="text-xs">
            同步后条目会以「OneTap · 应用名」命名，重复同步会更新原条目。
          </CardDescription>
          <CardAction>
            <Button
              type="button"
              size="sm"
              onClick={() => sync.mutate(null)}
              disabled={!canSync || sync.isPending}
            >
              {sync.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <RefreshCw className="h-4 w-4" />
              )}
              同步全部
            </Button>
          </CardAction>
        </CardHeader>

        <CardContent className="pt-4">
          {sync.data && sync.data.items.some((item) => !item.ok) && (
            <div className="mb-3 rounded-lg border border-destructive/20 bg-destructive/5 px-3 py-2 text-xs text-destructive">
              {sync.data.items
                .filter((item) => !item.ok)
                .map((item) => `${item.display_name}：${item.message}`)
                .join("；")}
            </div>
          )}
          {appsLoading ? (
            <div className="py-8 text-center text-sm text-muted-foreground">
              加载应用列表中...
            </div>
          ) : apps.length === 0 ? (
            <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed py-10">
              <KeyRound className="h-7 w-7 text-muted-foreground/50" />
              <p className="text-sm text-muted-foreground">还没有已部署的应用</p>
              <p className="text-xs text-muted-foreground/80">
                前往应用市场或自动化页面部署应用后即可同步凭据。
              </p>
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>应用</TableHead>
                  <TableHead>访问地址</TableHead>
                  <TableHead>账号</TableHead>
                  <TableHead>密码</TableHead>
                  <TableHead>状态</TableHead>
                  <TableHead className="text-right">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {apps.map((app) => (
                  <TableRow key={app.instance_id}>
                    <TableCell>
                      <div className="flex flex-col">
                        <span className="text-sm font-medium text-foreground">
                          {app.display_name}
                        </span>
                        <span className="text-xs text-muted-foreground">
                          {app.instance_name}
                        </span>
                      </div>
                    </TableCell>
                    <TableCell className="max-w-[260px] truncate text-xs text-muted-foreground">
                      {app.url ?? "-"}
                    </TableCell>
                    <TableCell className="text-xs">
                      {app.username || "-"}
                    </TableCell>
                    <TableCell>
                      {app.password_set ? (
                        <Badge className="bg-emerald-500/10 text-emerald-700">
                          已保存
                        </Badge>
                      ) : (
                        <Badge variant="secondary">无</Badge>
                      )}
                    </TableCell>
                    <TableCell>
                      {app.synced ? (
                        <Badge className="bg-primary/10 text-primary">
                          已同步
                        </Badge>
                      ) : (
                        <Badge variant="outline">未同步</Badge>
                      )}
                    </TableCell>
                    <TableCell className="text-right">
                      <Button
                        type="button"
                        variant="outline"
                        size="sm"
                        onClick={() => sync.mutate([app.instance_id])}
                        disabled={!app.has_credentials || !config?.enabled || !config?.configured || sync.isPending}
                      >
                        同步
                      </Button>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <Dialog open={deployDialogOpen} onOpenChange={setDeployDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>部署 Vaultwarden</DialogTitle>
            <DialogDescription>
              系统统一账号的邮箱后缀或密码尚未配置完整，请补充本次部署使用的登录信息。
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1.5">
              <Label htmlFor="deploy_vaultwarden_email" className="text-xs">
                登录邮箱
              </Label>
              <Input
                id="deploy_vaultwarden_email"
                type="email"
                value={form.email}
                onChange={(e) =>
                  setForm((prev) => ({ ...prev, email: e.target.value }))
                }
                placeholder="admin@example.com"
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="deploy_vaultwarden_password" className="text-xs">
                主密码
              </Label>
              <PasswordInput
                id="deploy_vaultwarden_password"
                value={form.password}
                onChange={(e) =>
                  setForm((prev) => ({ ...prev, password: e.target.value }))
                }
                placeholder="用于首次创建的 Vaultwarden 账号"
              />
            </div>
            <div className="flex justify-end gap-2 pt-2">
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => setDeployDialogOpen(false)}
                disabled={deploy.isPending}
              >
                取消
              </Button>
              <Button
                type="button"
                size="sm"
                onClick={handleDeploy}
                disabled={!hasDeployCredentials || deploy.isPending}
              >
                {deploy.isPending && (
                  <Loader2 className="h-4 w-4 animate-spin" />
                )}
                开始部署
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      <ConfirmDialog
        open={confirmRemove}
        onOpenChange={setConfirmRemove}
        title="删除 Vaultwarden"
        description="将停止并移除 OneTap 托管的 Vaultwarden，认证集成会停用，已同步记录会清空。密码库数据会备份到应用数据目录的 _backups 下。"
        confirmText="删除"
        destructive
        isPending={remove.isPending}
        onConfirm={() => {
          remove.mutate(undefined, {
            onSuccess: () => {
              setConfirmRemove(false);
              setForm((prev) => ({
                ...prev,
                enabled: false,
                url: "",
                password: "",
              }));
            },
          });
        }}
      />

      <RevealSecretDialog
        open={revealDialogOpen}
        onOpenChange={setRevealDialogOpen}
        provider="vaultwarden"
        title="查看 Vaultwarden 主密码"
      />
    </div>
  );
}
