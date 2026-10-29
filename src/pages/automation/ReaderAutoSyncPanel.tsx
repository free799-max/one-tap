/**
 * Reader 自动同步面板
 *
 * 配置保存在实例组 app_configs["reader"].auto_sync 中，由后端定时读取
 * Reader 书架、缓存章节、导出 EPUB 到二毛书库并触发扫描；本面板只负责
 * 编辑配置、展示进度和手动触发一轮执行。
 */

import { useEffect, useRef, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import { cn } from "@/lib/utils";
import {
  useAppAutoSync,
  useRunAppAutoSync,
  useSaveAppAutoSync,
  type ReaderAutoSyncConfig,
  type ReaderAutoSyncPanel as PanelData,
} from "@/hooks/useOrchestrations";
import {
  AlertTriangle,
  BookOpen,
  CheckCircle2,
  Clock,
  Eye,
  EyeOff,
  FolderTree,
  Loader2,
  Play,
  RefreshCw,
  Save,
} from "lucide-react";

function ToggleButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <Button
      type="button"
      size="sm"
      variant={active ? "default" : "outline"}
      className="h-7 px-2.5 text-xs"
      onClick={onClick}
    >
      {children}
    </Button>
  );
}

function formatTime(value?: string | null) {
  if (!value) return "尚未运行";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN");
}

const STATUS_META: Record<string, { label: string; className: string }> = {
  ok: {
    label: "成功",
    className: "text-emerald-600 dark:text-emerald-400",
  },
  partial: {
    label: "部分失败",
    className: "text-amber-600 dark:text-amber-400",
  },
  error: {
    label: "失败",
    className: "text-destructive",
  },
};

function RunSummary({ data }: { data: PanelData }) {
  const last = data.last_run;
  const status = STATUS_META[last?.status ?? ""];

  return (
    <div className="space-y-2 rounded-lg border bg-muted/20 p-3 text-sm">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <Clock className="h-3.5 w-3.5 text-muted-foreground" />
        <span className="text-xs text-muted-foreground">最近运行</span>
        <span className="text-xs">{formatTime(last?.finished_at)}</span>
        {status && (
          <span className={cn("text-xs font-medium", status.className)}>
            {status.label}
          </span>
        )}
        {last?.trigger === "auto" && (
          <Badge variant="secondary" className="h-5 text-[11px]">
            定时
          </Badge>
        )}
      </div>
      {last?.message ? (
        <p className="text-xs text-muted-foreground">{last.message}</p>
      ) : (
        <p className="text-xs text-muted-foreground">
          启用后后端会按间隔读取书架，导出的 EPUB 直接写入二毛书库。
        </p>
      )}
      {last?.ermao && (
        <p
          className={cn(
            "text-xs",
            last.ermao.ok ? "text-emerald-600" : "text-amber-600"
          )}
        >
          二毛扫描：{last.ermao.message}
        </p>
      )}
      <div className="flex flex-wrap gap-3 text-[11px] text-muted-foreground">
        <span>已同步 {data.stats?.done ?? 0}</span>
        <span>待处理 {data.stats?.pending ?? 0}</span>
        <span>失败待重试 {data.stats?.failed ?? 0}</span>
      </div>
    </div>
  );
}

export function ReaderAutoSyncPanel({
  instanceId,
  appName,
}: {
  instanceId: number;
  appName: string;
}) {
  const { data, isLoading, refetch, isFetching } = useAppAutoSync(
    instanceId,
    appName
  );
  const saveMutation = useSaveAppAutoSync();
  const runMutation = useRunAppAutoSync();
  const [draft, setDraft] = useState<ReaderAutoSyncConfig | null>(null);
  const [showPassword, setShowPassword] = useState(false);
  const initialized = useRef(false);

  useEffect(() => {
    if (!data?.config || initialized.current) return;
    setDraft(data.config);
    initialized.current = true;
  }, [data?.config]);

  if (isLoading || !draft) {
    return (
      <div className="flex items-center gap-2 py-8 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        正在读取自动同步配置…
      </div>
    );
  }

  if (!data?.supported) {
    return (
      <div className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">
        该应用暂不支持自动同步，目前仅 Reader 可用。
      </div>
    );
  }

  const update = (patch: Partial<ReaderAutoSyncConfig>) =>
    setDraft((prev) => (prev ? { ...prev, ...patch } : prev));

  const handleSave = () =>
    saveMutation.mutate(
      { instanceId, appName, config: draft },
      { onSuccess: (panel) => setDraft(panel.config) }
    );

  // 手动执行前先保存当前表单，避免运行的仍是旧地址/旧目录
  const handleRun = () =>
    saveMutation.mutate(
      { instanceId, appName, config: draft },
      {
        onSuccess: () => runMutation.mutate({ instanceId, appName }),
      }
    );

  const fillDetected = () => {
    if (!data?.detected) return;
    update({
      reader_url: data.detected.reader_url || draft.reader_url,
      reader_username:
        data.detected.reader_username || draft.reader_username,
      reader_password:
        data.detected.reader_password || draft.reader_password,
      reader_token: data.detected.reader_token || draft.reader_token,
      ermao_url: data.detected.ermao_url || draft.ermao_url,
      library_dir: data.detected.library_dir || draft.library_dir,
      library_host_dir:
        data.detected.library_host_dir || draft.library_host_dir,
      ermao_library_dir:
        data.detected.ermao_library_dir || draft.ermao_library_dir,
      ermao_email: data.detected.ermao_email || draft.ermao_email,
      ermao_password:
        data.detected.ermao_password || draft.ermao_password,
    });
  };

  const total = data.progress?.total ?? 0;
  const processed = data.progress?.processed ?? 0;
  const progressPercent = total > 0 ? Math.round((processed / total) * 100) : 0;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <BookOpen className="h-4 w-4 text-primary" />
            <h3 className="text-sm font-medium">Reader 自动同步</h3>
            <Badge
              variant={draft.enabled ? "default" : "secondary"}
              className="h-5 text-[11px]"
            >
              {draft.enabled ? "已启用" : "未启用"}
            </Badge>
          </div>
          <p className="text-xs text-muted-foreground">
            后端读取 Reader 书架、补齐章节缓存，把 EPUB 直接写入二毛书库并触发扫描。
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            variant="outline"
            className="gap-1.5"
            onClick={() => refetch()}
            disabled={isFetching}
          >
            <RefreshCw
              className={cn("h-3.5 w-3.5", isFetching && "animate-spin")}
            />
            刷新
          </Button>
          <Button
            size="sm"
            variant="outline"
            className="gap-1.5"
            onClick={handleRun}
            disabled={
              saveMutation.isPending || runMutation.isPending || data.running
            }
          >
            {data.running || runMutation.isPending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Play className="h-3.5 w-3.5" />
            )}
            立即同步
          </Button>
          <Button
            size="sm"
            className="gap-1.5"
            onClick={handleSave}
            disabled={saveMutation.isPending}
          >
            {saveMutation.isPending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Save className="h-3.5 w-3.5" />
            )}
            保存配置
          </Button>
        </div>
      </div>

      {(data.running || data.progress?.message) && (
        <div className="space-y-2 rounded-lg border bg-card p-3">
          <div className="flex items-center justify-between gap-2 text-xs">
            <span className="flex min-w-0 items-center gap-1.5 font-medium">
              {data.running && (
                <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-primary" />
              )}
              <span className="truncate">
                {data.progress?.message || "等待执行"}
              </span>
            </span>
            {total > 0 && (
              <span className="shrink-0 text-muted-foreground">
                {processed}/{total}
              </span>
            )}
          </div>
          <Progress value={progressPercent} className="h-1.5" />
        </div>
      )}

      <RunSummary data={data} />

      <div className="grid gap-4 lg:grid-cols-2">
        <div className="space-y-3 rounded-lg border p-3">
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium">运行方式</span>
            <ToggleButton
              active={draft.enabled}
              onClick={() => update({ enabled: !draft.enabled })}
            >
              {draft.enabled ? "已启用" : "启用自动同步"}
            </ToggleButton>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="reader-interval">检查间隔（分钟）</Label>
              <Input
                id="reader-interval"
                type="number"
                min={1}
                max={1440}
                value={draft.interval_minutes}
                onChange={(e) =>
                  update({ interval_minutes: Number(e.target.value) || 1 })
                }
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="reader-max-seconds">单轮时间上限（秒）</Label>
              <Input
                id="reader-max-seconds"
                type="number"
                min={60}
                max={21600}
                value={draft.max_seconds}
                onChange={(e) =>
                  update({ max_seconds: Number(e.target.value) || 60 })
                }
              />
            </div>
          </div>
          <div className="flex items-center justify-between border-t pt-3">
            <div>
              <p className="text-xs font-medium">补齐缺失章节</p>
              <p className="text-[11px] text-muted-foreground">
                导出前逐章调用 Reader 缓存，关闭后只导出已有内容
              </p>
            </div>
            <ToggleButton
              active={draft.cache_missing_chapters}
              onClick={() =>
                update({
                  cache_missing_chapters: !draft.cache_missing_chapters,
                })
              }
            >
              {draft.cache_missing_chapters ? "已开启" : "已关闭"}
            </ToggleButton>
          </div>
          <div className="flex items-center justify-between border-t pt-3">
            <div>
              <p className="text-xs font-medium">导出后触发二毛扫描</p>
              <p className="text-[11px] text-muted-foreground">
                二毛自带目录监听时也可以关闭，避免重复扫描
              </p>
            </div>
            <ToggleButton
              active={draft.scan_enabled}
              onClick={() => update({ scan_enabled: !draft.scan_enabled })}
            >
              {draft.scan_enabled ? "已开启" : "已关闭"}
            </ToggleButton>
          </div>
        </div>

        <div className="space-y-3 rounded-lg border p-3">
          <div className="flex items-center gap-1.5">
            <FolderTree className="h-4 w-4 text-muted-foreground" />
            <span className="text-sm font-medium">目录</span>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="reader-library-dir">Reader 容器内书库目录</Label>
            <Input
              id="reader-library-dir"
              className="font-mono text-xs"
              value={draft.library_dir}
              onChange={(e) => update({ library_dir: e.target.value })}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="reader-library-host-dir">后端宿主机书库目录</Label>
            <Input
              id="reader-library-host-dir"
              className="font-mono text-xs"
              value={draft.library_host_dir}
              onChange={(e) => update({ library_host_dir: e.target.value })}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="reader-ermao-dir">二毛容器内书库目录</Label>
            <Input
              id="reader-ermao-dir"
              className="font-mono text-xs"
              value={draft.ermao_library_dir}
              onChange={(e) => update({ ermao_library_dir: e.target.value })}
            />
          </div>
          {data.detected && (
            <div className="flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
              <span>
                从应用实际挂载检测到（
                {data.detected.source === "docker" ? "运行中容器" : "数据库配置"}）：
              </span>
              <button
                type="button"
                className="max-w-full truncate rounded bg-muted px-1.5 py-0.5 font-mono hover:bg-muted/70"
                onClick={fillDetected}
                title="点击填入检测到的地址与目录"
              >
                {data.detected.library_host_dir || "未检测到"} →{" "}
                {data.detected.library_dir || "未检测到"}
              </button>
            </div>
          )}
          <p className="text-[11px] text-muted-foreground">
            三个目录指向同一份书库数据；后端通过宿主机路径写入 EPUB，Reader
            与二毛各自从自己的挂载点读取。
          </p>
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <div className="space-y-3 rounded-lg border p-3">
          <div>
            <span className="text-sm font-medium">Reader 接口</span>
            <p className="text-[11px] text-muted-foreground">
              账号由部署后的自动配置写入；开启 secure 模式时优先使用
              accessToken。
            </p>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="reader-url">访问地址</Label>
            <Input
              id="reader-url"
              className="font-mono text-xs"
              value={draft.reader_url}
              onChange={(e) => update({ reader_url: e.target.value })}
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="reader-username">登录用户名</Label>
              <Input
                id="reader-username"
                className="text-xs"
                value={draft.reader_username}
                onChange={(e) => update({ reader_username: e.target.value })}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="reader-password">登录密码</Label>
              <Input
                id="reader-password"
                type={showPassword ? "text" : "password"}
                className="text-xs"
                value={draft.reader_password}
                onChange={(e) => update({ reader_password: e.target.value })}
              />
            </div>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="reader-token">访问 Token（自动获取，可留空）</Label>
            <Input
              id="reader-token"
              className="font-mono text-xs"
              value={draft.reader_token}
              onChange={(e) => update({ reader_token: e.target.value })}
            />
          </div>
        </div>

        <div className="space-y-3 rounded-lg border p-3">
          <div>
            <span className="text-sm font-medium">二毛读书</span>
            <p className="text-[11px] text-muted-foreground">
              首次运行会在二毛未初始化时自动创建账号与书库；已有账号时请填写原密码。
            </p>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="reader-ermao-url">访问地址</Label>
            <Input
              id="reader-ermao-url"
              className="font-mono text-xs"
              value={draft.ermao_url}
              onChange={(e) => update({ ermao_url: e.target.value })}
            />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="reader-ermao-email">登录邮箱</Label>
              <Input
                id="reader-ermao-email"
                type="email"
                className="text-xs"
                value={draft.ermao_email}
                onChange={(e) => update({ ermao_email: e.target.value })}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="reader-ermao-password">登录密码</Label>
              <div className="flex items-center gap-1">
                <Input
                  id="reader-ermao-password"
                  type={showPassword ? "text" : "password"}
                  className="text-xs"
                  value={draft.ermao_password}
                  onChange={(e) =>
                    update({ ermao_password: e.target.value })
                  }
                />
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  className="h-9 w-9 shrink-0 px-0"
                  aria-label={showPassword ? "隐藏密码" : "显示密码"}
                  onClick={() => setShowPassword((value) => !value)}
                >
                  {showPassword ? (
                    <EyeOff className="h-3.5 w-3.5" />
                  ) : (
                    <Eye className="h-3.5 w-3.5" />
                  )}
                </Button>
              </div>
            </div>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="reader-ermao-lib">书库 ID（可留空自动匹配）</Label>
              <Input
                id="reader-ermao-lib"
                className="font-mono text-xs"
                value={draft.ermao_lib_id}
                onChange={(e) => update({ ermao_lib_id: e.target.value })}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="reader-scan-wait">扫描等待上限（秒）</Label>
              <Input
                id="reader-scan-wait"
                type="number"
                min={10}
                max={3600}
                value={draft.scan_wait_seconds}
                onChange={(e) =>
                  update({ scan_wait_seconds: Number(e.target.value) || 10 })
                }
              />
            </div>
          </div>
          <div className="flex items-center justify-between border-t pt-3">
            <div>
              <p className="text-xs font-medium">自动初始化二毛账号与书库</p>
              <p className="text-[11px] text-muted-foreground">
                仅在二毛还没有任何用户/书库时生效
              </p>
            </div>
            <ToggleButton
              active={draft.auto_setup}
              onClick={() => update({ auto_setup: !draft.auto_setup })}
            >
              {draft.auto_setup ? "已开启" : "已关闭"}
            </ToggleButton>
          </div>
        </div>
      </div>

      <div className="rounded-lg border bg-muted/20 p-3 text-[11px] text-muted-foreground">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
          <span className="flex items-center gap-1">
            <CheckCircle2 className="h-3 w-3" />
            配置保存在自动化页的应用配置中，不修改 Reader 镜像
          </span>
          <span className="flex items-center gap-1">
            <AlertTriangle className="h-3 w-3" />
            失败书籍最多重试 5 次，间隔逐步延长
          </span>
        </div>
      </div>
    </div>
  );
}
