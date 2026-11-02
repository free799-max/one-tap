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
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  useSyncVaultwarden,
  useTestVaultwarden,
  useUpdateVaultwarden,
  useVaultwardenApps,
  useVaultwardenConfig,
} from "@/hooks/useIntegrations";
import {
  CheckCircle2,
  ExternalLink,
  KeyRound,
  Loader2,
  RefreshCw,
  ShieldCheck,
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
  const { data: apps = [], isLoading: appsLoading } = useVaultwardenApps();
  const updateConfig = useUpdateVaultwarden();
  const testConnection = useTestVaultwarden();
  const sync = useSyncVaultwarden();

  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [initialized, setInitialized] = useState(false);

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
            <ShieldCheck className="h-4 w-4 text-muted-foreground" />
            Vaultwarden
          </CardTitle>
          <CardDescription className="text-xs">
            使用 Vaultwarden 的主密码登录，在浏览器端完成条目加密后写入密码库。
          </CardDescription>
          <CardAction>
            {/^https?:\/\//i.test(form.url.trim()) && (
              <a
                href={form.url.trim()}
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
              >
                打开 Vaultwarden
                <ExternalLink className="h-3 w-3" />
              </a>
            )}
          </CardAction>
        </CardHeader>

        <CardContent className="space-y-4 pt-4">
          <div className="flex items-center justify-between gap-4">
            <div>
              <Label htmlFor="vaultwarden_enabled" className="text-sm font-medium">
                启用认证集成
              </Label>
              <p className="mt-0.5 text-xs text-muted-foreground">
                启用后可在下方同步已部署应用的账号密码。
              </p>
            </div>
            <Switch
              id="vaultwarden_enabled"
              checked={form.enabled}
              onCheckedChange={(checked) =>
                setForm((prev) => ({ ...prev, enabled: checked }))
              }
            />
          </div>

          <div className="grid gap-4 xl:grid-cols-2">
            <div className="space-y-1.5">
              <Label htmlFor="vaultwarden_url" className="text-xs font-medium">
                服务地址
              </Label>
              <div className="flex flex-wrap items-center gap-2">
                <Input
                  id="vaultwarden_url"
                  value={form.url}
                  onChange={(e) =>
                    setForm((prev) => ({ ...prev, url: e.target.value }))
                  }
                  placeholder="http://192.168.1.23:8080"
                  className={`${INPUT_CLASS} min-w-0 flex-1`}
                />
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={handleTest}
                  disabled={testConnection.isPending}
                >
                  {testConnection.isPending ? (
                    <Loader2 className="h-4 w-4 animate-spin" />
                  ) : (
                    <KeyRound className="h-4 w-4" />
                  )}
                  测试连接
                </Button>
              </div>
            </div>

            <div className="space-y-1.5">
              <Label htmlFor="vaultwarden_email" className="text-xs font-medium">
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

            <div className="space-y-1.5">
              <Label htmlFor="vaultwarden_password" className="text-xs font-medium">
                主密码
              </Label>
              <PasswordInput
                id="vaultwarden_password"
                value={form.password}
                onChange={(e) =>
                  setForm((prev) => ({ ...prev, password: e.target.value }))
                }
                placeholder={
                  config?.password_set ? "已保存，留空则不修改" : "Vaultwarden 主密码"
                }
                className={INPUT_CLASS}
              />
              <p className="text-xs text-muted-foreground">
                主密码仅用于登录并派生加密密钥，不会出现在同步的条目中。
                暂不支持开启两步验证或使用 Argon2id KDF 的账号。
              </p>
            </div>
          </div>

          {testConnection.data && (
            <div
              className={`flex items-start gap-1.5 text-xs ${
                testConnection.data.ok ? "text-emerald-600" : "text-destructive"
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
          )}

          {config?.status.message && (
            <div className="flex items-start gap-1.5 text-xs text-muted-foreground">
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

          <div className="flex items-center justify-between border-t border-border pt-4">
            <p className="text-xs text-muted-foreground">
              已同步 {config?.synced_count ?? 0} 个应用
            </p>
            <Button
              type="button"
              onClick={handleSave}
              disabled={updateConfig.isPending || !isDirty}
              className="rounded-md"
            >
              {updateConfig.isPending ? "保存中..." : "保存配置"}
            </Button>
          </div>
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
    </div>
  );
}
