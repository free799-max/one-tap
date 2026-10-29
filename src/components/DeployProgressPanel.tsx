/**
 * 部署任务进度面板
 *
 * 以类终端形式展示一个或多个部署任务的实时进度，
 * 包含阶段徽章、进度条、累计日志流和错误信息。
 */

import { useEffect, useMemo, useRef } from "react";
import {
  useDeployTasksEvents,
  type DeployTaskState,
} from "@/hooks/useDeployTasks";
import { Progress } from "@/components/ui/progress";
import { Badge } from "@/components/ui/badge";
import { AppIcon } from "@/pages/apps/AppIcon";
import { cn } from "@/lib/utils";
import { CheckCircle2, XCircle, Loader2, Package, Terminal, AlertTriangle } from "lucide-react";

interface DeployProgressPanelProps {
  taskIds: string[];
  onComplete?: (taskId: string, state: DeployTaskState) => void;
  onError?: (taskId: string, state: DeployTaskState) => void;
  /** 任务已被后端清理（404）：从跟踪列表移除，不做失败展示 */
  onExpired?: (taskId: string) => void;
}

const stageMap: Record<string, string> = {
  preparing: "准备中",
  creating_project: "创建项目",
  writing_compose: "写入配置",
  copying_assets: "拷贝离线数据",
  pulling_images: "拉取镜像",
  preparing_packages: "准备依赖",
  starting_services: "启动服务",
  syncing_status: "同步状态",
  stopping_services: "停止容器",
  backing_up_data: "备份数据",
  cleaning_records: "清理记录",
  waiting_services: "等待部署",
  provisioning: "配置账号",
  wiring: "自动装配",
  completed: "部署完成",
  failed: "部署失败",
};

const statusVariantMap: Record<
  string,
  { variant: "default" | "secondary" | "destructive" | "outline"; icon: React.ReactNode }
> = {
  deploying: { variant: "outline", icon: <Loader2 className="h-3 w-3 animate-spin" /> },
  completed: { variant: "default", icon: <CheckCircle2 className="h-3 w-3" /> },
  failed: { variant: "destructive", icon: <XCircle className="h-3 w-3" /> },
};

function TaskTitle({ state }: { state: DeployTaskState }) {
  const meta = state.meta || {};
  const isDelete = meta.operation === "delete";
  const isProvision = meta.operation === "provision";
  const title = isDelete
    ? `删除 ${(meta.instance_name as string) || "实例组"}`
    : isProvision
      ? "自动配置与装配"
      : (meta.app_name as string) ||
        (meta.project_name as string) ||
        (meta.instance_name as string) ||
        "部署任务";
  const subtitleCandidates = [
    meta.instance_name as string | undefined,
    meta.description as string | undefined,
    state.taskId.slice(0, 8),
  ];
  const subtitle = subtitleCandidates.find((s) => s && s !== title) ?? state.taskId.slice(0, 8);

  return (
    <div className="min-w-0">
      <p className="text-sm font-semibold truncate">{title}</p>
      <p className="text-xs text-muted-foreground truncate">{subtitle}</p>
    </div>
  );
}

function LogTerminal({ logs, isFailed }: { logs: string[]; isFailed: boolean }) {
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [logs.length]);

  return (
    <div
      className={cn(
        "mt-3 rounded-md border bg-slate-950 text-slate-100 p-3 font-mono text-[11px] leading-snug overflow-y-auto max-h-64 min-h-[120px]",
        isFailed && "border-destructive/50"
      )}
    >
      {logs.length === 0 ? (
        <span className="text-slate-500">等待日志输出…</span>
      ) : (
        logs.map((line, idx) => {
          // 升级流程用空行分隔各个应用，空字符串行渲染为一段留白
          if (line === "") {
            return <div key={idx} className="h-2" aria-hidden="true" />;
          }
          const isStageLine = line.startsWith("[");
          const isErrorLine = /error|fail/i.test(line);
          return (
            <div
              key={idx}
              className={cn(
                "whitespace-pre-wrap break-words",
                isErrorLine && "text-red-400",
                !isErrorLine && (isStageLine ? "text-slate-100" : "text-slate-300")
              )}
            >
              {line}
            </div>
          );
        })
      )}
      <div ref={bottomRef} />
    </div>
  );
}

function DeployTaskCard({ state, logs }: { state: DeployTaskState; logs: string[] }) {
  const isCompleted = state.status === "completed";
  const isFailed = state.status === "failed";
  const statusConfig = statusVariantMap[state.status] || statusVariantMap.deploying;
  const meta = state.meta || {};
  const appName = meta.app_name as string | undefined;
  const appIcon = meta.icon as string | undefined;
  const isDelete = meta.operation === "delete";
  const isProvision = meta.operation === "provision";
  const stageLabel =
    state.stage === "completed"
      ? isDelete
        ? "删除完成"
        : isProvision
          ? "自动配置完成"
          : "部署完成"
      : state.stage === "failed"
        ? isDelete
          ? "删除失败"
          : isProvision
            ? "自动配置失败"
            : "部署失败"
        : stageMap[state.stage] || state.stage;

  return (
    <div
      className={cn(
        "rounded-xl border bg-card p-4 shadow-sm transition-colors",
        isCompleted && "border-green-500/40",
        isFailed && "border-destructive/50 bg-destructive/5"
      )}
    >
      <div className="flex items-center gap-3">
        {appName ? (
          <AppIcon
            app={{ name: appName, icon: appIcon ?? null }}
            className="h-10 w-10 shrink-0 border shadow-sm"
          />
        ) : (
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-[22%] border bg-primary/10 shadow-sm">
            <Package className="h-5 w-5 text-primary" aria-hidden="true" />
          </div>
        )}
        <TaskTitle state={state} />
        <div className="ml-auto flex items-center gap-2 shrink-0">
          <Badge variant={statusConfig.variant} className="text-[10px] h-5 px-1.5 gap-1">
            {statusConfig.icon}
            {stageLabel}
          </Badge>
          <span className="text-xs text-muted-foreground w-9 text-right tabular-nums">
            {state.progress.percentage}%
          </span>
        </div>
      </div>

      {!isCompleted && (
        <div className="mt-3">
          <Progress
            value={state.progress.percentage}
            className={cn("h-1.5", isFailed && "[&_>div]:bg-destructive")}
          />
        </div>
      )}

      {!isCompleted && !isFailed && (
        <div className="mt-3 flex items-center gap-2 text-xs text-muted-foreground">
          <Terminal className="h-3.5 w-3.5" aria-hidden="true" />
          <span className="truncate">{state.progress.message}</span>
        </div>
      )}

      {isCompleted && state.warning ? (
        <div className="mt-3 flex items-start gap-2.5 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2.5">
          <AlertTriangle
            className="mt-0.5 h-4 w-4 shrink-0 text-amber-600"
            aria-hidden="true"
          />
          <div className="min-w-0">
            <p className="text-xs font-medium text-amber-700">
              {state.progress.message || stageLabel}
            </p>
            <p className="mt-1 whitespace-pre-wrap break-words text-[11px] text-amber-700/80">
              {state.warning}
            </p>
          </div>
        </div>
      ) : isCompleted ? (
        <div className="mt-3 flex items-center gap-2.5 rounded-lg border border-green-500/25 bg-green-500/10 px-3 py-2.5">
          <CheckCircle2 className="h-4 w-4 shrink-0 text-green-600" aria-hidden="true" />
          <div className="min-w-0">
            <p className="text-xs font-medium text-green-700">
              {isDelete || isProvision
                ? state.progress.message
                : "部署完成，服务已启动"}
            </p>
            <p className="text-[11px] text-green-600/80 truncate">
              {isDelete
                ? "可在「恢复」中找回应用数据"
                : isProvision
                  ? "配置与装配结果已写入实例详情"
                  : "可在实例列表中查看访问地址与运行状态"}
            </p>
          </div>
        </div>
      ) : (
        <LogTerminal logs={logs} isFailed={isFailed} />
      )}

      {state.error && (
        <div className="mt-3 rounded-md bg-destructive/10 border border-destructive/20 p-3">
          <p className="text-xs font-semibold text-destructive mb-1">
            {isDelete ? "删除失败" : isProvision ? "自动配置失败" : "部署失败"}
          </p>
          <p className="text-xs text-destructive/90 whitespace-pre-wrap break-all">
            {state.error}
          </p>
        </div>
      )}
    </div>
  );
}

/** 部署进度面板 */
export function DeployProgressPanel({
  taskIds,
  onComplete,
  onError,
  onExpired,
}: DeployProgressPanelProps) {
  const activeTaskIds = useMemo(() => taskIds.filter(Boolean), [taskIds]);
  const states = useDeployTasksEvents(
    activeTaskIds,
    onComplete,
    onError,
    onExpired
  );

  // 已完成的任务排在前面，让用户一眼看到哪些部署完了；进行中/失败的保持在后
  const orderedTaskIds = useMemo(() => {
    const rank = (id: string) => {
      const status = states[id]?.status;
      return status === "completed" ? 0 : status === "failed" ? 1 : 2;
    };
    return [...activeTaskIds].sort((a, b) => rank(a) - rank(b));
  }, [activeTaskIds, states]);

  if (activeTaskIds.length === 0) {
    return null;
  }

  return (
    <div className="space-y-4">
      {orderedTaskIds.map((taskId) => {
        const state = states[taskId];
        if (!state) {
          return (
            <div
              key={taskId}
              className="rounded-xl border bg-card p-4 shadow-sm"
            >
              <div className="flex items-center gap-2">
                <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" />
                <span className="text-sm text-muted-foreground">连接任务…</span>
              </div>
            </div>
          );
        }
        return (
          <DeployTaskCard
            key={taskId}
            state={state}
            logs={state.logs || []}
          />
        );
      })}
    </div>
  );
}
