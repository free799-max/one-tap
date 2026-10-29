/**
 * 基础配置 Tab
 *
 * 包含代理、统一服务账号和订阅授权。
 */

import { useEffect, useState } from "react";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { PasswordInput } from "@/components/ui/password-input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import { useToast } from "@/components/ui/toast";
import { useSystemConfig, useUpdateSystemConfig, useTestProxy } from "@/hooks/useSettings";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  Globe2,
  Info,
  KeyRound,
  Loader2,
  SlidersHorizontal,
} from "lucide-react";
import { cn } from "@/lib/utils";
import { LicenseTab } from "./LicenseTab";

/** 隐藏代理地址中的账号密码，避免在页面上明文展示 */
function maskProxy(proxy: string): string {
  return proxy.replace(/\/\/[^@/]+@/, "//***@");
}

interface BasicFormState {
  proxy_enabled: boolean;
  proxy_address: string;
  default_admin_username: string;
  default_admin_password: string;
  default_email_domain: string;
}

const EMAIL_APP_LABELS: Record<string, string> = {
  komga: "Komga",
  "ermao-library": "二毛读书",
};

/** 空表单 */
const EMPTY_FORM: BasicFormState = {
  proxy_enabled: false,
  proxy_address: "",
  default_admin_username: "admin",
  default_admin_password: "",
  default_email_domain: "",
};

export function BasicSettingsTab() {
  const { data: config, isLoading } = useSystemConfig();
  const updateConfig = useUpdateSystemConfig();
  const testProxy = useTestProxy();
  const toast = useToast();

  const [form, setForm] = useState<BasicFormState>(EMPTY_FORM);
  const [initialized, setInitialized] = useState(false);

  // 首次加载完成后回填表单；后续 refetch 不再覆盖用户未保存的修改
  useEffect(() => {
    if (config && !initialized) {
      setForm({
        proxy_enabled: config.proxy_enabled,
        proxy_address: config.https_proxy || config.http_proxy || "",
        default_admin_username: config.default_admin_username || "admin",
        default_admin_password: config.default_admin_password || "",
        default_email_domain: config.default_email_domain || "",
      });
      setInitialized(true);
    }
  }, [config, initialized]);

  const emailAppNames = (config?.email_username_apps ?? [])
    .map((name) => EMAIL_APP_LABELS[name] ?? name)
    .join("、");

  // 脏检查：与已加载配置一致时禁用保存，避免无意义提交
  const isDirty =
    !config ||
    form.proxy_enabled !== config.proxy_enabled ||
    form.proxy_address !== (config.https_proxy || config.http_proxy || "") ||
    form.default_admin_username !== (config.default_admin_username || "admin") ||
    form.default_admin_password !== (config.default_admin_password || "") ||
    form.default_email_domain !== (config.default_email_domain || "");

  const proxyResult = testProxy.data;

  /** 用当前填写的代理检测，无需先保存 */
  const handleTestProxy = () => {
    const address = form.proxy_address.trim();
    testProxy.mutate({ http_proxy: address, https_proxy: address });
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();

    const address = form.proxy_address.trim();
    if (form.proxy_enabled && !address) {
      toast.error("启用代理时请填写代理地址");
      return;
    }
    if (address && !/^https?:\/\//i.test(address)) {
      toast.error("代理地址必须以 http:// 或 https:// 开头");
      return;
    }

    const emailDomain = form.default_email_domain
      .trim()
      .toLowerCase()
      .replace(/^@+/, "")
      .replace(/\.+$/, "");
    if (emailDomain) {
      const labels = emailDomain.split(".");
      const valid = labels.every((label) =>
        /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(label)
      );
      if (!valid) {
        toast.error("邮箱后缀格式不正确，例如 example.com");
        return;
      }
    }

    updateConfig.mutate(
      {
        proxy_enabled: form.proxy_enabled,
        http_proxy: address || null,
        https_proxy: address || null,
        service_port_start: config?.service_port_start ?? 10000,
        default_admin_username: form.default_admin_username.trim() || "admin",
        default_admin_password: form.default_admin_password.trim(),
        default_email_domain: emailDomain,
      },
      {
        onSuccess: (data) => {
          // 后端会规范化大小写与 @ 前缀，回填避免表单继续保持脏状态
          setForm((prev) => ({
            ...prev,
            default_email_domain: data.default_email_domain ?? "",
          }));
        },
      }
    );
  };

  if (isLoading) {
    return <div className="text-sm text-muted-foreground">加载配置中...</div>;
  }

  return (
    <div className="space-y-5">
      <form
        onSubmit={handleSubmit}
        className="grid grid-cols-1 items-start gap-5 xl:grid-cols-2"
      >
      {/* 基础配置 */}
      <Card className="rounded-xl xl:col-span-2">
        <CardHeader className="border-b">
          <CardTitle className="text-base flex items-center gap-2">
            <SlidersHorizontal className="h-4 w-4 text-muted-foreground" />
            基础配置
          </CardTitle>
        </CardHeader>

        <CardHeader>
          <CardTitle className="text-base flex items-center gap-2">
            <Globe2 className="h-4 w-4 text-muted-foreground" />
            代理配置
          </CardTitle>
        </CardHeader>
        <CardContent>
          <div className="flex flex-wrap items-center gap-x-4 gap-y-3">
            <div className="flex items-center gap-2">
              <Label htmlFor="proxy_enabled" className="text-sm font-medium">
                启用代理
              </Label>
              <Switch
                id="proxy_enabled"
                checked={form.proxy_enabled}
                onCheckedChange={(checked) => {
                  testProxy.reset();
                  setForm((prev) => ({ ...prev, proxy_enabled: checked }));
                }}
              />
            </div>
            <div className="flex min-w-0 flex-1 flex-wrap items-center gap-2">
              <Label htmlFor="proxy_address" className="shrink-0 text-sm font-medium">
                代理地址
              </Label>
              <Input
                id="proxy_address"
                value={form.proxy_address}
                disabled={!form.proxy_enabled}
                onChange={(e) => {
                  testProxy.reset();
                  setForm((prev) => ({ ...prev, proxy_address: e.target.value }));
                }}
                placeholder="http://127.0.0.1:7890"
                className="h-8 max-w-64 flex-1 rounded-md border-input bg-white px-2 text-sm shadow-none"
              />
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="shrink-0"
                onClick={handleTestProxy}
                disabled={!form.proxy_enabled || testProxy.isPending}
                title={form.proxy_enabled ? undefined : "请先启用代理"}
              >
                {testProxy.isPending ? (
                  <Loader2 className="h-4 w-4 animate-spin" />
                ) : (
                  <Globe2 className="h-4 w-4" />
                )}
                {testProxy.isPending ? "检测中..." : "检测"}
              </Button>
              {proxyResult && (
                <div className="flex flex-wrap items-center gap-1.5">
                  {proxyResult.results.map((site) => (
                    <span
                      key={site.name}
                      title={`${site.name} · ${
                        proxyResult.proxy
                          ? `代理 ${maskProxy(proxyResult.proxy)}`
                          : "直连"
                      } · ${
                        site.ok
                          ? `${site.latency_ms} ms · HTTP ${site.status_code}`
                          : site.error ?? "连接失败"
                      }`}
                      className={cn(
                        "inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs",
                        site.ok
                          ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400"
                          : "border-destructive/30 bg-destructive/10 text-destructive"
                      )}
                    >
                      <span
                        className={cn(
                          "h-1.5 w-1.5 shrink-0 rounded-full",
                          site.ok ? "bg-emerald-500" : "bg-destructive"
                        )}
                      />
                      {site.name}
                      <span className="font-normal tabular-nums text-muted-foreground">
                        {site.ok ? `${site.latency_ms}ms` : "失败"}
                      </span>
                    </span>
                  ))}
                </div>
              )}
            </div>
          </div>
        </CardContent>

        <div className="border-t border-border" />

        {/* 统一服务账号 */}
        <CardHeader className="pb-0">
          <CardTitle className="text-base flex items-center gap-2">
            <KeyRound className="h-4 w-4 text-muted-foreground" />
            统一服务账号
            <Tooltip>
              <TooltipTrigger
                delay={150}
                render={
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-xs"
                    className="text-muted-foreground hover:text-foreground"
                    aria-label="统一服务账号说明"
                  >
                    <Info className="h-4 w-4" />
                  </Button>
                }
              />
              <TooltipContent
                side="bottom"
                align="start"
                className="max-w-72 whitespace-normal font-normal leading-relaxed"
              >
                只针对自动化部署方案中才会自动配置各应用的用户名密码，如影视、漫画、音乐自动化。
              </TooltipContent>
            </Tooltip>
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="grid grid-cols-1 items-start gap-4 md:grid-cols-2">
            <div className="flex items-start gap-2">
              <Label
                htmlFor="default_admin_username"
                className="mt-1.5 w-24 shrink-0 text-sm font-medium"
              >
                管理员用户名
              </Label>
              <div className="min-w-0 flex-1 space-y-1.5">
                <div className="flex h-8 w-full max-w-md items-center rounded-md border border-input bg-white text-sm shadow-none transition-colors focus-within:border-foreground/60">
                  <Input
                    id="default_admin_username"
                    value={form.default_admin_username}
                    onChange={(e) =>
                      setForm((prev) => ({
                        ...prev,
                        default_admin_username: e.target.value,
                      }))
                    }
                    placeholder="admin"
                    className="h-full min-w-0 flex-1 rounded-none border-0 bg-transparent px-2.5 shadow-none focus-visible:border-0 dark:bg-transparent"
                  />
                  <span
                    aria-hidden="true"
                    className="shrink-0 select-none pl-1 text-xs text-muted-foreground"
                  >
                    @
                  </span>
                  <Input
                    id="default_email_domain"
                    value={form.default_email_domain}
                    onChange={(e) =>
                      setForm((prev) => ({
                        ...prev,
                        default_email_domain: e.target.value,
                      }))
                    }
                    placeholder="example.com"
                    aria-label="邮箱后缀"
                    title="需要邮箱登录的应用使用此后缀"
                    className="h-full w-32 shrink-0 rounded-none border-0 bg-transparent px-1.5 text-sm shadow-none placeholder:text-muted-foreground/70 focus-visible:border-0 dark:bg-transparent"
                  />
                </div>
                <p className="text-[11px] text-muted-foreground">
                  邮箱后缀仅用于必须邮箱登录的应用
                  {emailAppNames ? `（${emailAppNames}）` : ""}。
                </p>
              </div>
            </div>
            <div className="flex items-center gap-2">
              <Label
                htmlFor="default_admin_password"
                className="w-24 shrink-0 text-sm font-medium"
              >
                管理员密码
              </Label>
              <PasswordInput
                id="default_admin_password"
                value={form.default_admin_password}
                onChange={(e) =>
                  setForm((prev) => ({
                    ...prev,
                    default_admin_password: e.target.value,
                  }))
                }
                placeholder="留空则每次部署随机生成"
                className="h-8 min-w-0 max-w-md flex-1 rounded-md border-input bg-white px-2 text-sm shadow-none"
              />
            </div>
          </div>
        </CardContent>

        <div className="border-t border-border" />

        {/* 保存按钮 */}
        <div className="flex justify-end px-4">
          <Button
            type="submit"
            disabled={updateConfig.isPending || !isDirty}
            className="rounded-md"
          >
            {updateConfig.isPending ? "保存中..." : "保存配置"}
          </Button>
        </div>
      </Card>
      </form>

      <LicenseTab />
    </div>
  );
}
