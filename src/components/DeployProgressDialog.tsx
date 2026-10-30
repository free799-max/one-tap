/**
 * 部署任务进度弹窗
 *
 * 全局统一的浮层，展示所有进行中的部署任务进度。
 * 关闭后可通过顶部栏的动态入口重新打开。
 */

import { useCallback, useRef } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { DeployProgressPanel } from "@/components/DeployProgressPanel";
import { useDeployTasks } from "@/hooks/useDeployTasks";
import { useToast } from "@/components/ui/toast";

export function DeployProgressDialog() {
  const { activeTaskIds, isPanelOpen, closePanel, removeTask } = useDeployTasks();
  const toast = useToast();
  const queryClient = useQueryClient();
  // 已结束（完成/失败/停止）的任务：卡片保留展示，关闭弹窗时才清理
  const finishedRef = useRef<Set<string>>(new Set());

  // 任务结束（无论完成、失败或停止）后刷新编排实例、Compose 项目/Stack 状态与应用卡片
  // Compose 启停/重启是异步任务，完成时主动失效缓存，避免界面等下一次轮询
  const invalidateInstances = useCallback(() => {
    queryClient.invalidateQueries({ queryKey: ["orchestration-instances"] });
    queryClient.invalidateQueries({ queryKey: ["apps", "instances"] });
    queryClient.invalidateQueries({ queryKey: ["orchestration-backups"] });
    queryClient.invalidateQueries({ queryKey: ["dashboard", "overview"] });
    queryClient.invalidateQueries({ queryKey: ["compose"] });
    queryClient.invalidateQueries({ queryKey: ["docker", "containers"] });
  }, [queryClient]);

  const handleComplete = useCallback(
    (taskId: string) => {
      // SSE 断线重连后历史回放会重复触发终态事件，需要去重
      if (finishedRef.current.has(taskId)) return;
      finishedRef.current.add(taskId);
      // 弹窗内已完成卡片已有提示，不再弹全局 toast
      invalidateInstances();
    },
    [invalidateInstances]
  );

  const handleError = useCallback(
    (taskId: string, state: { error?: string | null }) => {
      if (finishedRef.current.has(taskId)) return;
      finishedRef.current.add(taskId);
      toast.error(state.error || "操作失败");
      invalidateInstances();
    },
    [toast, invalidateInstances]
  );

  const handleOpenChange = useCallback(
    (open: boolean) => {
      if (open) return;
      closePanel();
      // 关闭弹窗时清理已结束的任务，进行中的任务保留跟踪
      finishedRef.current.forEach((id) => removeTask(id));
      finishedRef.current.clear();
    },
    [closePanel, removeTask]
  );

  // 任务已被后端清理（正常完成超过 TTL、旧任务残留或服务重启）：
  // 直接从跟踪列表移除，避免把“任务不存在”渲染成红色的“部署失败”
  const handleExpired = useCallback(
    (taskId: string) => {
      finishedRef.current.delete(taskId);
      removeTask(taskId);
      invalidateInstances();
    },
    [removeTask, invalidateInstances]
  );

  if (activeTaskIds.length === 0) {
    return null;
  }

  return (
    <Dialog open={isPanelOpen} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-3xl max-h-[85vh] flex flex-col p-0 overflow-hidden">
        <DialogHeader className="px-5 pt-5 pb-3 border-b shrink-0">
          <DialogTitle>任务进度</DialogTitle>
        </DialogHeader>
        <div className="px-5 py-4 overflow-y-auto">
          <DeployProgressPanel
            taskIds={activeTaskIds}
            onComplete={handleComplete}
            onError={handleError}
            onExpired={handleExpired}
          />
        </div>
      </DialogContent>
    </Dialog>
  );
}
