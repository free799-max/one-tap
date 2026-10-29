/**
 * Hosts 优选 Tab
 *
 * 通过 AdGuard Home 的 DNS 重写能力，把指定域名解析到实测最快的 IP。
 * 支持手动触发与定时自动运行，配置与最近一次结果均保存在后端。
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
  useHostsOptimizerConfig,
  useHostsOptimizerStatus,
  useRunHostsOptimizer,
  useTestHostsOptimizer,
  useUpdateHostsOptimizerConfig,
  type HostsOptimizerConfig,
} from "@/hooks/useSettings";
import {
  Activity,
  CheckCircle2,
  ExternalLink,
  Globe2,
  Loader2,
  Play,
  Save,
  ShieldCheck,
  XCircle,
} from "lucide-react";

const INPUT_CLASS =
  "h-8 rounded-md border-input bg-white px-2 text-sm shadow-none";
const TEXTAREA_CLASS =
  "min-h-[140px] w-full rounded-md border border-input bg-white px-2 py-1.5 text-sm shadow-none outline-none focus-visible:border-foreground/60";

interface FormState {
  enabled: boolean;
  mode: "manual" | "auto";
  interval_minutes: number;
  top_n: number;
  domainsText: string;
  adguard_url: string;
  adguard_username: string;
  adguard_password: string;
}

const EMPTY_FORM: FormState = {
  enabled: false,
  mode: "manual",
  interval_minutes: 360,
  top_n: 2,
  domainsText: "",
  adguard_url: "",
  adguard_username: "admin",
  adguard_password: "",
};

function toForm(config: HostsOptimizerConfig): FormState {
  return {
    enabled: config.enabled,
    mode: config.mode,
    interval_minutes: config.interval_minutes,
    top_n: config.top_n,
    domainsText: config.domains.join("\n"),
    adguard_url: config.adguard_url ?? "",
    adguard_username: config.adguard_username,
    adguard_password: config.adguard_password,
  };
}

function toPayload(form: FormState): HostsOptimizerConfig {
  return {
    enabled: form.enabled,
    mode: form.mode,
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

function formatTime(value: string | null | undefined): string {
  if (!value) return "-";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function DomainStatusBadge({ status }: { status: string }) {
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

  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [initialized, setInitialized] = useState(false);

  useEffect(() => {
    if (config && !initialized) {
      setForm(toForm(config));
      setInitialized(true);
    }
  }, [config, initialized]);

  const payload = toPayload(form);
  const isDirty =
    !!config && JSON.stringify(payload) !== JSON.stringify(config);

  const running = status?.running || runOptimizer.isPending;
  const lastRun = runOptimizer.data ?? status?.last_run ?? null;
  const canRun =
    !isDirty &&
    !!config?.adguard_url &&
    !!config?.adguard_password &&
    !running;

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
      adguard_password: form.adguard_password,
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
          Hosts 优选
        </CardTitle>
        <CardDescription className="text-xs">
          NasDeck 后端直连 AdGuard Home 管理地址，通过 DNS 重写把指定域名解析到实测最快的
          IP。
        </CardDescription>
      </CardHeader>

      {/* AdGuard Home */}
      <CardHeader className="pb-0">
        <CardTitle className="flex items-center gap-2 text-base">
          <ShieldCheck className="h-4 w-4 text-muted-foreground" />
          AdGuard Home
        </CardTitle>
        <CardAction>
          {/^https?:\/\//i.test(form.adguard_url.trim()) && (
            <a
              href={form.adguard_url}
              target="_blank"
              rel="noreferrer"
              className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
            >
              打开管理
              <ExternalLink className="h-3 w-3" />
            </a>
          )}
        </CardAction>
      </CardHeader>
      <CardContent className="space-y-3">
        <div className="space-y-1.5">
          <Label htmlFor="adguard_url" className="text-xs font-medium">
            管理地址
          </Label>
          <div className="flex flex-wrap items-center gap-2">
            <Input
              id="adguard_url"
              value={form.adguard_url}
              onChange={(e) =>
                setForm((prev) => ({ ...prev, adguard_url: e.target.value }))
              }
              placeholder="http://192.168.1.23:8081"
              className={`${INPUT_CLASS} min-w-0 flex-1 max-w-md`}
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
                <Globe2 className="h-4 w-4" />
              )}
              测试连接
            </Button>
          </div>
          <p className="text-xs text-muted-foreground">
            AdGuard 部署在 192.168.1.23、管理端口映射为 8081 时，填写
            http://192.168.1.23:8081。
          </p>
          {testConnection.data && (
            <div
              className={`flex items-start gap-1.5 text-xs ${
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
          )}
        </div>

        <div className="grid grid-cols-1 gap-3 md:grid-cols-2">
          <div className="space-y-1.5">
            <Label htmlFor="adguard_username" className="text-xs font-medium">
              用户名
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
          <div className="space-y-1.5">
            <Label htmlFor="adguard_password" className="text-xs font-medium">
              密码
            </Label>
            <PasswordInput
              id="adguard_password"
              value={form.adguard_password}
              onChange={(e) =>
                setForm((prev) => ({
                  ...prev,
                  adguard_password: e.target.value,
                }))
              }
              placeholder="AdGuard 管理员密码"
              className={INPUT_CLASS}
            />
          </div>
        </div>
      </CardContent>

      <div className="border-t border-border" />

      {/* 优选规则 */}
      <CardHeader className="pb-0">
        <CardTitle className="flex items-center gap-2 text-base">
          <Globe2 className="h-4 w-4 text-muted-foreground" />
          优选规则
        </CardTitle>
        <CardDescription className="text-xs">
          自动模式按间隔在后台运行，手动模式只在点击「立即优选」时运行。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        <div className="flex flex-wrap items-center gap-x-5 gap-y-3">
          <div className="flex items-center gap-2">
            <Label htmlFor="hosts_enabled" className="text-sm font-medium">
              启用 Hosts 优选
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
            <Label htmlFor="hosts_mode" className="text-sm font-medium">
              运行模式
            </Label>
            <select
              id="hosts_mode"
              value={form.mode}
              onChange={(e) =>
                setForm((prev) => ({
                  ...prev,
                  mode: e.target.value as "manual" | "auto",
                }))
              }
              className="h-8 rounded-md border border-input bg-white px-2 text-sm shadow-none outline-none focus-visible:border-foreground/60"
            >
              <option value="manual">手动</option>
              <option value="auto">自动</option>
            </select>
          </div>
          <div className="flex items-center gap-2">
            <Label htmlFor="hosts_interval" className="text-sm font-medium">
              自动间隔（分钟）
            </Label>
            <Input
              id="hosts_interval"
              type="number"
              min={5}
              max={10080}
              value={form.interval_minutes}
              disabled={form.mode !== "auto"}
              onChange={(e) =>
                setForm((prev) => ({
                  ...prev,
                  interval_minutes:
                    Number.parseInt(e.target.value, 10) || 360,
                }))
              }
              className={`${INPUT_CLASS} w-24`}
            />
          </div>
          <div className="flex items-center gap-2">
            <Label htmlFor="hosts_top_n" className="text-sm font-medium">
              每域名保留 IP 数
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
          </div>
        </div>

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
      </CardContent>

      <div className="border-t border-border" />

      {/* 运行状态 */}
      <CardHeader className="pb-0">
        <CardTitle className="flex items-center gap-2 text-base">
          <Activity className="h-4 w-4 text-muted-foreground" />
          运行状态
        </CardTitle>
        <CardDescription className="text-xs">
          最近一次优选结果；自动模式下会显示下次运行时间。
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
      <CardContent className="space-y-4">
        {isDirty && (
          <p className="text-xs text-amber-600">
            配置有未保存的修改，保存后才能运行优选。
          </p>
        )}

        <div className="grid grid-cols-1 gap-3 text-sm md:grid-cols-3">
          <div>
            <div className="text-xs text-muted-foreground">最近运行</div>
            <div>{formatTime(lastRun?.finished_at)}</div>
          </div>
          <div>
            <div className="text-xs text-muted-foreground">耗时</div>
            <div>
              {lastRun ? `${(lastRun.duration_ms / 1000).toFixed(1)} 秒` : "-"}
            </div>
          </div>
          <div>
            <div className="text-xs text-muted-foreground">下次自动运行</div>
            <div>
              {status?.next_run_at ? formatTime(status.next_run_at) : "-"}
            </div>
          </div>
        </div>

        {lastRun && (
          <>
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

            {Object.keys(lastRun.domains).length > 0 ? (
              <div className="rounded-lg border">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>域名</TableHead>
                      <TableHead>状态</TableHead>
                      <TableHead>优选 IP</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {Object.entries(lastRun.domains).map(([domain, result]) => (
                      <TableRow key={domain}>
                        <TableCell className="font-mono text-xs">
                          {domain}
                        </TableCell>
                        <TableCell>
                          <DomainStatusBadge status={result.status} />
                        </TableCell>
                        <TableCell>
                          {result.ips.length > 0 ? (
                            <div className="flex flex-wrap gap-1.5">
                              {result.ips.map((item) => (
                                <Badge
                                  key={item.ip}
                                  variant="secondary"
                                  className="font-mono"
                                >
                                  {item.ip}
                                  <span className="text-muted-foreground">
                                    {Math.round(item.latency_ms)}ms
                                  </span>
                                </Badge>
                              ))}
                            </div>
                          ) : (
                            <span className="text-xs text-muted-foreground">
                              无可用 IP
                            </span>
                          )}
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">暂无域名结果</p>
            )}
          </>
        )}

        {!lastRun && (
          <p className="text-sm text-muted-foreground">
            还没有运行记录，保存配置后点击「立即优选」。
          </p>
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
    </Card>
  );
}
