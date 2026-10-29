/**
 * 迷你拉取进度条（搜索框右侧）
 *
 * 紧凑展示当前活跃拉取任务的合并进度，Popover 内展示完整下载历史：
 * - 迷你进度条（仅统计 pulling 状态的任务）
 * - 右上角活跃任务数量小徽章
 * - 失败时进度条变红，全部完成时变绿
 * - 点击后 Popover 展开，显示每个历史任务的详细进度卡片
 * - 最新下载排在最上方，先下载的排在下方
 *
 * 历史记录与进度状态均来自全局内存态 store（zustand），
 * 多组件共享同一份状态与同一条 SSE 连接。
 */

import { useAllPullProgress } from "@/hooks/useDocker";
import { Popover as PopoverPrimitive } from "@base-ui/react/popover";
import { Popover, PopoverTrigger } from "@/components/ui/popover";
import { usePullProgressStore } from "@/stores/pull-progress";
import { PullTaskCard } from "./PullTaskCard";
import { cn } from "@/lib/utils";
import { Download, X } from "lucide-react";

export function PullProgressMini() {
  const history = usePullProgressStore((s) => s.history);

  const pullingTasks = history.filter((item) => item.status === "pulling");
  const taskIds = pullingTasks.map((t) => t.taskId);
  const { states: progressStates } = useAllPullProgress(taskIds);

  // 计算合并进度与状态（仅统计 pulling 任务；无 pulling 但有历史记录时显示 100% 完成）
  let totalPercentage = 0;
  let progressCount = 0;
  let hasFailed = false;
  let allCompleted = true;

  taskIds.forEach((id) => {
    const s = progressStates[id];
    if (s?.progress) {
      totalPercentage += s.progress.percentage;
      progressCount += 1;
    }
    if (s?.status === "failed") hasFailed = true;
    if (s?.status !== "completed") allCompleted = false;
  });

  const hasPullingTasks = progressCount > 0;
  const hasFailedHistory = history.some((item) => item.status === "failed");
  const allHistoryCompleted =
    history.length > 0 &&
    history.every((item) => item.status === "completed");

  const percentage = hasPullingTasks
    ? Math.round(totalPercentage / progressCount)
    : history.length > 0
    ? 100
    : 0;
  const allCompletedActive = hasPullingTasks ? allCompleted : allHistoryCompleted;

  // 进度条颜色：活跃任务优先；无活跃任务时按历史状态着色
  const barColor = hasFailed
    ? "bg-red-500"
    : allCompletedActive
    ? "bg-green-500"
    : hasFailedHistory
    ? "bg-red-500"
    : "bg-primary";

  // 徽标颜色
  const badgeColor = hasFailed
    ? "bg-red-500 text-white"
    : allCompletedActive
    ? "bg-green-500 text-white"
    : hasFailedHistory
    ? "bg-red-500 text-white"
    : "bg-primary text-primary-foreground";

  if (history.length === 0) {
    return null;
  }

  return (
    <Popover>
      <PopoverTrigger className="relative shrink-0">
        <div className="flex items-center gap-1.5 px-2 py-1 rounded-md bg-muted/60 hover:bg-muted transition-colors cursor-pointer">
          <Download className="h-3.5 w-3.5 text-muted-foreground" />
          {/* 迷你进度条 */}
          <div className="w-24 h-1.5 rounded-full bg-muted overflow-hidden">
            <div
              className={cn("h-full rounded-full transition-all duration-300", barColor)}
              style={{ width: `${percentage}%` }}
            />
          </div>
          <span className="text-[10px] text-muted-foreground w-7 text-right">
            {percentage}%
          </span>
          {/* 数量徽章：仅存在活跃任务时显示 */}
          {pullingTasks.length > 0 && (
            <span
              className={cn(
                "absolute -top-1.5 -right-1.5 min-w-[14px] h-[14px] flex items-center justify-center rounded-full text-[9px] font-medium px-0.5",
                badgeColor
              )}
            >
              {pullingTasks.length}
            </span>
          )}
        </div>
      </PopoverTrigger>

      <PopoverPrimitive.Portal>
        <PopoverPrimitive.Positioner sideOffset={12} align="center">
          <PopoverPrimitive.Popup
            className={cn(
              "z-50 w-[28rem] max-w-[calc(100vw-2rem)] rounded-xl border bg-popover p-3 text-popover-foreground shadow-lg outline-none origin-top transition-[transform,opacity] duration-150 data-[starting-style]:scale-95 data-[starting-style]:opacity-0 data-[ending-style]:scale-95 data-[ending-style]:opacity-0"
            )}
          >
            <div className="flex items-center justify-between mb-2 px-0.5">
              <div className="flex items-center gap-2">
                <Download className="h-4 w-4 text-primary" />
                <h4 className="text-sm font-medium">拉取任务</h4>
                <span className="text-xs text-muted-foreground">
                  ({history.length} 个)
                </span>
              </div>
              <button
                onClick={() => usePullProgressStore.getState().clearHistory()}
                className="text-[11px] text-muted-foreground hover:text-foreground transition-colors flex items-center gap-0.5"
                title="清除全部"
              >
                <X className="h-3 w-3" />
                清除全部
              </button>
            </div>

            <div className="space-y-2 max-h-[60vh] overflow-y-auto scrollbar-thin pr-0.5">
              {history.map((task, index) => {
                const s = progressStates[task.taskId];
                return (
                  <PullTaskCard
                    key={task.taskId}
                    image={task.image}
                    progress={s?.progress ?? task.finalProgress}
                    finalProgress={task.finalProgress}
                    status={s?.status ?? task.status}
                    error={s?.error ?? task.error}
                    createdAt={task.createdAt}
                    lastActivityAt={task.lastActivityAt}
                    defaultExpanded={index === 0}
                    onRemove={() =>
                      usePullProgressStore.getState().removeHistoryItem(task.taskId)
                    }
                  />
                );
              })}
            </div>
          </PopoverPrimitive.Popup>
        </PopoverPrimitive.Positioner>
      </PopoverPrimitive.Portal>
    </Popover>
  );
}
