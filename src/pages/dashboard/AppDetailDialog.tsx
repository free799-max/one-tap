/**
 * 应用详情弹窗
 *
 * 展示单个已部署应用的容器基本信息与实时监控指标，
 * 打开期间每 5 秒自动刷新一次。
 */

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Progress } from "@/components/ui/progress";
import { AppIcon } from "@/pages/apps/AppIcon";
import { categoryLabel } from "@/lib/categories";
import { cn, formatBytes, formatDate, formatUptime } from "@/lib/utils";
import { useOverviewAppDetail } from "@/hooks/useDashboard";
import { useCategories } from "@/hooks/useApps";
import { ExternalLink, RefreshCw } from "lucide-react";

const STATUS_META: Record<string, { label: string; className: string }> = {
  running: {
    label: "运行中",
    className: "border-emerald-200 bg-emerald-50 text-emerald-700",
  },
  deploying: {
    label: "部署中",
    className: "border-amber-200 bg-amber-50 text-amber-700",
  },
  stopped: {
    label: "已停止",
    className: "border-border bg-muted text-muted-foreground",
  },
  error: {
    label: "异常",
    className: "border-red-200 bg-red-50 text-red-700",
  },
};

function MetricCard({
  label,
  value,
  percent,
}: {
  label: string;
  value: string;
  percent?: number;
}) {
  return (
    <div className="rounded-lg border border-border/60 bg-muted/30 p-3">
      <p className="text-[11px] text-muted-foreground">{label}</p>
      <p className="mt-1 truncate text-sm font-semibold text-foreground">
        {value}
      </p>
      {percent != null && (
        <Progress value={percent} className="mt-2 h-1.5" />
      )}
    </div>
  );
}

function InfoRow({
  label,
  value,
  mono,
  href,
}: {
  label: string;
  value: string;
  mono?: boolean;
  href?: string | null;
}) {
  return (
    <div className="min-w-0">
      <p className="text-[11px] text-muted-foreground">{label}</p>
      {href ? (
        <a
          href={href}
          target="_blank"
          rel="noreferrer noopener"
          className="mt-0.5 block truncate text-sm text-primary hover:underline"
          title={value}
        >
          {value}
        </a>
      ) : (
        <p
          className={cn(
            "mt-0.5 truncate text-sm text-foreground",
            mono && "font-mono text-xs"
          )}
          title={value}
        >
          {value}
        </p>
      )}
    </div>
  );
}

export function AppDetailDialog({
  instanceId,
  open,
  onOpenChange,
}: {
  instanceId: number | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { data, isLoading, isFetching, isError, refetch } =
    useOverviewAppDetail(open ? instanceId : null);
  const { data: categoryOptions = [] } = useCategories();

  const app = data?.app;
  const status = app
    ? STATUS_META[app.status] ?? {
        label: app.status,
        className: "border-border bg-muted text-muted-foreground",
      }
    : null;
  const metrics = data?.metrics;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-h-[88vh] gap-0 overflow-hidden p-0 sm:max-w-2xl">
        {isLoading || !data || !app ? (
          <div className="flex h-64 items-center justify-center">
            {isError ? (
              <div className="text-center">
                <p className="text-sm text-muted-foreground">应用详情加载失败</p>
                <Button
                  variant="outline"
                  size="sm"
                  className="mt-3"
                  onClick={() => refetch()}
                >
                  重试
                </Button>
              </div>
            ) : (
              <RefreshCw className="h-5 w-5 animate-spin text-muted-foreground" />
            )}
          </div>
        ) : (
          <>
            <div className="border-b bg-gradient-to-br from-primary/5 to-transparent p-5 pr-11">
              <div className="flex items-start gap-4">
                <AppIcon
                  app={{ name: app.app_name, icon: app.icon }}
                  className="h-14 w-14 shrink-0 rounded-xl border border-border/60 text-2xl"
                />
                <div className="min-w-0 flex-1">
                  <DialogTitle className="truncate text-lg font-semibold">
                    {app.display_name}
                  </DialogTitle>
                  <DialogDescription className="mt-1 line-clamp-2 text-sm text-muted-foreground">
                    {app.description || app.instance_name}
                  </DialogDescription>
                  <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
                    <Badge
                      variant="outline"
                      className={cn("text-[11px]", status?.className)}
                    >
                      {status?.label}
                    </Badge>
                    <Badge variant="outline" className="text-[11px]">
                      {categoryLabel(categoryOptions, app.category)}
                    </Badge>
                    <span className="text-[11px] text-muted-foreground">
                      {app.instance_name}
                    </span>
                  </div>
                </div>
                {app.url && (
                  <Button
                    size="sm"
                    className="shrink-0"
                    render={
                      <a
                        href={app.url}
                        target="_blank"
                        rel="noreferrer noopener"
                      />
                    }
                  >
                    打开应用
                    <ExternalLink />
                  </Button>
                )}
              </div>
            </div>

            <div className="max-h-[calc(88vh-120px)] space-y-5 overflow-y-auto p-5">
              {data.error && (
                <div className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-700">
                  {data.error}
                </div>
              )}

              <section className="space-y-2.5">
                <div className="flex items-center justify-between">
                  <h3 className="text-sm font-semibold text-foreground">
                    实时监控
                  </h3>
                  <span className="flex items-center gap-1 text-[11px] text-muted-foreground">
                    <RefreshCw
                      className={cn("h-3 w-3", isFetching && "animate-spin")}
                    />
                    每 5 秒刷新
                  </span>
                </div>
                {metrics ? (
                  <div className="grid grid-cols-2 gap-2.5 sm:grid-cols-4">
                    <MetricCard
                      label="CPU"
                      value={`${metrics.cpu_percent.toFixed(1)}%`}
                      percent={metrics.cpu_percent}
                    />
                    <MetricCard
                      label="内存"
                      value={`${formatBytes(metrics.memory_used)} / ${formatBytes(
                        metrics.memory_limit
                      )}`}
                      percent={metrics.memory_percent}
                    />
                    <MetricCard
                      label="网络"
                      value={`↓ ${formatBytes(metrics.network_rx)}  ↑ ${formatBytes(
                        metrics.network_tx
                      )}`}
                    />
                    <MetricCard
                      label="磁盘 I/O"
                      value={`读 ${formatBytes(
                        metrics.block_read
                      )}  写 ${formatBytes(metrics.block_write)}`}
                    />
                  </div>
                ) : (
                  <div className="rounded-lg border border-dashed border-border px-3 py-4 text-center text-xs text-muted-foreground">
                    {app.status === "running"
                      ? "暂时无法获取监控数据"
                      : "容器未运行，暂无监控数据"}
                  </div>
                )}
              </section>

              <section className="space-y-2.5">
                <h3 className="text-sm font-semibold text-foreground">
                  基本信息
                </h3>
                <div className="grid grid-cols-1 gap-x-6 gap-y-3 rounded-lg border border-border/60 bg-muted/20 p-3 sm:grid-cols-2">
                  <InfoRow label="应用标识" value={app.app_name} />
                  <InfoRow label="实例名称" value={app.instance_name} />
                  <InfoRow
                    label="访问地址"
                    value={app.url || "—"}
                    href={app.url}
                  />
                  <InfoRow
                    label="运行状态"
                    value={status?.label ?? app.status}
                  />
                  <InfoRow
                    label="容器名称"
                    value={data.container_name || "—"}
                  />
                  <InfoRow
                    label="容器 ID"
                    value={data.container_id || "—"}
                    mono
                  />
                  <InfoRow label="镜像" value={data.image || "—"} mono />
                  <InfoRow
                    label="Compose 项目"
                    value={data.project_name || "—"}
                  />
                  <InfoRow
                    label="重启策略"
                    value={data.restart_policy || "—"}
                  />
                  <InfoRow
                    label="启动时间"
                    value={
                      data.started_at ? formatDate(data.started_at) : "—"
                    }
                  />
                  <InfoRow
                    label="运行时长"
                    value={
                      data.uptime_seconds != null
                        ? formatUptime(data.uptime_seconds)
                        : "—"
                    }
                  />
                  <InfoRow
                    label="进程数"
                    value={metrics ? String(metrics.pids) : "—"}
                  />
                </div>
              </section>

              {data.ports.length > 0 && (
                <section className="space-y-2.5">
                  <h3 className="text-sm font-semibold text-foreground">
                    端口映射
                  </h3>
                  <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                    {data.ports.map((port, index) => (
                      <div
                        key={`${port.container_port}-${port.host_port}-${index}`}
                        className="flex items-center justify-between rounded-lg border border-border/60 bg-muted/20 px-3 py-2 text-xs"
                      >
                        <span className="text-muted-foreground">
                          {port.container_port}
                        </span>
                        <span className="font-mono text-foreground">
                          {port.host_ip || "0.0.0.0"}:
                          {port.host_port || "—"}
                        </span>
                      </div>
                    ))}
                  </div>
                </section>
              )}
            </div>
          </>
        )}
      </DialogContent>
    </Dialog>
  );
}
