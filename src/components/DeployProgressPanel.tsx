/**
 * 部署任务进度面板
 *
 * 以类终端形式展示一个或多个部署任务的实时进度，
 * 包含阶段徽章、进度条、累计日志流和错误信息。
 */

import { useEffect, useMemo, useRef, useState } from "react";
import {
  useDeployTasksEvents,
  type DeployTaskState,
} from "@/hooks/useDeployTasks";
import { Progress } from "@/components/ui/progress";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { AppIcon } from "@/pages/apps/AppIcon";
import { useToast } from "@/components/ui/toast";
import api from "@/lib/api";
import { cn } from "@/lib/utils";
import { CheckCircle2, XCircle, Loader2, Package, Square, Terminal, AlertTriangle } from "lucide-react";

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

/** Compose 项目操作动词（与后端 compose action 一致） */
const composeActionVerbMap: Record<string, string> = {
  up: "启动",
  down: "停止",
  restart: "重启",
};

/** 从任务 meta 中取 Compose 操作动词，未知 action 回退为「操作」 */
function composeActionVerb(meta: Record<string, unknown>): string {
  return composeActionVerbMap[String(meta.action || "")] || "操作";
}

/** 任务标题：按操作类型区分部署、升级、启停、重启、删除 */
function taskTitleOf(meta: Record<string, unknown>): string | null {
  const target =
    (meta.app_name as string) ||
    (meta.project_name as string) ||
    (meta.instance_name as string) ||
    "实例";
  switch (meta.operation) {
    case "delete":
      return `删除 ${(meta.instance_name as string) || "实例组"}`;
    case "provision":
      return "自动配置与装配";
    case "upgrade":
      return `升级 ${target}`;
    case "compose_action":
      return `${composeActionVerb(meta)} ${target}`;
    default:
      return null;
  }
}

/** 结束态文案：部署完成 / 升级完成 / 停止完成 等 */
function finishedLabel(meta: Record<string, unknown>, failed: boolean): string {
  switch (meta.operation) {
    case "delete":
      return failed ? "删除失败" : "删除完成";
    case "provision":
      return failed ? "自动配置失败" : "自动配置完成";
    case "upgrade":
      return failed ? "升级失败" : "升级完成";
    case "compose_action":
      return `${composeActionVerb(meta)}${failed ? "失败" : "完成"}`;
    default:
      return failed ? "部署失败" : "部署完成";
  }
}

/** 完成态摘要文案 */
function successSummary(meta: Record<string, unknown>): {
  title: string;
  hint: string;
} {
  switch (meta.operation) {
    case "upgrade":
      return { title: "升级完成，镜像已更新", hint: "配置与数据保持不变" };
    case "compose_action":
      if (meta.action === "down") {
        return {
          title: "已停止，容器已移除",
          hint: "可在概览的应用详情中重新启动",
        };
      }
      if (meta.action === "restart") {
        return {
          title: "重启完成，服务已恢复",
          hint: "可在实例列表中查看访问地址与运行状态",
        };
      }
      return {
        title: "启动完成，服务已运行",
        hint: "可在实例列表中查看访问地址与运行状态",
      };
    default:
      return {
        title: "部署完成，服务已启动",
        hint: "可在实例列表中查看访问地址与运行状态",
      };
  }
}

const statusVariantMap: Record<
  string,
  { variant: "default" | "secondary" | "destructive" | "outline"; icon: React.ReactNode }
> = {
  deploying: { variant: "outline", icon: <Loader2 className="h-3 w-3 animate-spin" /> },
  completed: { variant: "default", icon: <CheckCircle2 className="h-3 w-3" /> },
  failed: { variant: "destructive", icon: <XCircle className="h-3 w-3" /> },
  cancelled: { variant: "secondary", icon: <Square className="h-3 w-3" /> },
};

function TaskTitle({ state }: { state: DeployTaskState }) {
  const meta = state.meta || {};
  const title =
    taskTitleOf(meta) ||
    (meta.app_name as string) ||
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
  const terminalRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const terminal = terminalRef.current;
    if (terminal) {
      terminal.scrollTop = terminal.scrollHeight;
    }
  }, [logs.length]);

  return (
    <div
      ref={terminalRef}
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
    </div>
  );
}

function DeployTaskCard({
  state,
  logs,
  onStop,
  isStopping = false,
}: {
  state: DeployTaskState;
  logs: string[];
  onStop?: (taskId: string) => void;
  isStopping?: boolean;
}) {
  const isCompleted = state.status === "completed";
  const isFailed = state.status === "failed";
  const isCancelled = state.status === "cancelled";
  const statusConfig = statusVariantMap[state.status] || statusVariantMap.deploying;
  const meta = state.meta || {};
  const appName = meta.app_name as string | undefined;
  const appIcon = meta.icon as string | undefined;
  const canStop = state.status === "deploying" && meta.operation === "provision";
  const stageLabel =
    state.stage === "completed"
      ? finishedLabel(meta, false)
      : state.stage === "failed"
        ? finishedLabel(meta, true)
        : state.stage === "cancelled"
          ? "已停止"
          : meta.operation === "compose_action" &&
              state.stage === "starting_services"
            ? `${composeActionVerb(meta)}服务`
            : stageMap[state.stage] || state.stage;
  const summary = successSummary(meta);

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
          {canStop && (
            <Button
              variant="outline"
              size="xs"
              disabled={isStopping}
              onClick={() => onStop?.(state.taskId)}
            >
              {isStopping ? (
                <Loader2 className="h-3 w-3 animate-spin" aria-hidden="true" />
              ) : (
                <Square className="h-3 w-3" aria-hidden="true" />
              )}
              {isStopping ? "停止中" : "停止装配"}
            </Button>
          )}
          <Badge variant={statusConfig.variant} className="text-[10px] h-5 px-1.5 gap-1">
            {statusConfig.icon}
            {stageLabel}
          </Badge>
          <span className="text-xs text-muted-foreground w-9 text-right tabular-nums">
            {state.progress.percentage}%
          </span>
        </div>
      </div>

      {!isCompleted && !isCancelled && (
        <div className="mt-3">
          <Progress
            value={state.progress.percentage}
            className={cn("h-1.5", isFailed && "[&_>div]:bg-destructive")}
          />
        </div>
      )}

      {state.status === "deploying" && (
        <div className="mt-3 flex items-center gap-2 text-xs text-muted-foreground">
          <Terminal className="h-3.5 w-3.5" aria-hidden="true" />
          <span className="truncate">{state.progress.message}</span>
        </div>
      )}

      {isCancelled ? (
        <div className="mt-3 flex items-center gap-2.5 rounded-lg border bg-muted/50 px-3 py-2.5">
          <Square className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
          <div className="min-w-0">
            <p className="text-xs font-medium">{state.progress.message}</p>
            <p className="mt-1 text-[11px] text-muted-foreground">
              需要时可在应用详情中重新装配
            </p>
          </div>
        </div>
      ) : isCompleted && state.warning ? (
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
              {meta.operation === "delete" || meta.operation === "provision"
                ? state.progress.message
                : summary.title}
            </p>
            <p className="text-[11px] text-green-600/80 truncate">
              {meta.operation === "delete"
                ? "可在「恢复」中找回应用数据"
                : meta.operation === "provision"
                  ? "配置与装配结果已写入实例详情"
                  : summary.hint}
            </p>
          </div>
        </div>
      ) : (
        <LogTerminal logs={logs} isFailed={isFailed} />
      )}

      {state.error && (
        <div className="mt-3 rounded-md bg-destructive/10 border border-destructive/20 p-3">
          <p className="text-xs font-semibold text-destructive mb-1">
            {finishedLabel(meta, true)}
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
  const toast = useToast();
  const [stoppingTaskId, setStoppingTaskId] = useState<string | null>(null);
  const activeTaskIds = useMemo(() => taskIds.filter(Boolean), [taskIds]);
  const states = useDeployTasksEvents(
    activeTaskIds,
    onComplete,
    onError,
    onExpired
  );

  const handleStop = async (taskId: string) => {
    setStoppingTaskId(taskId);
    try {
      await api.post(`/deploy-tasks/${taskId}/cancel`);
      toast.success("已停止自动配置与装配，应用容器保持运行");
    } catch (error) {
      const message = (error as { displayMessage?: string }).displayMessage;
      toast.error(message || "停止装配失败，请重试");
    } finally {
      setStoppingTaskId(null);
    }
  };

  // 已结束的任务排在前面，让用户一眼看到哪些任务不需要继续等待
  const orderedTaskIds = useMemo(() => {
    const rank = (id: string) => {
      const status = states[id]?.status;
      return status === "completed" || status === "cancelled"
        ? 0
        : status === "failed"
          ? 1
          : 2;
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
            onStop={handleStop}
            isStopping={stoppingTaskId === taskId}
          />
        );
      })}
    </div>
  );
}
