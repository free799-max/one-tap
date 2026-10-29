/**
 * Docker 镜像拉取进度 hooks
 *
 * 状态与 SSE 连接由 @/stores/pull-progress 全局管理，
 * 多个组件订阅同一任务时共享一条 SSE 连接。
 */

import { useEffect } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useToast } from "@/components/ui/toast";
import {
  reconnectPullTask,
  registerPullTerminalHandlers,
  subscribePullTask,
  usePullProgressStore,
} from "@/stores/pull-progress";

/**
 * 多任务拉取进度 Hook
 *
 * 返回每个任务的进度状态映射；连接管理、自动重连、超时检测、
 * 终结状态处理均在全局 store 的模块级管理器中完成。
 *
 * @param taskIds 任务 ID 数组
 * @returns 每个任务的状态映射 { [taskId]: { progress, status, error, connected } }
 */
export function useAllPullProgress(taskIds: string[]) {
  const taskIdsKey = taskIds.join(",");
  const states = usePullProgressStore((s) => s.states);

  const toast = useToast();
  const qc = useQueryClient();

  // 注册终结状态回调：toast 提示 + 刷新镜像列表缓存
  useEffect(() => {
    registerPullTerminalHandlers({
      onCompleted: (_taskId, image) => {
        toast.success(`镜像 ${image} 拉取完成`);
        qc.invalidateQueries({ queryKey: ["docker", "images"], exact: true });
      },
      onFailed: (_taskId, _image, error) => {
        toast.error(error || "拉取失败");
      },
    });
    return () => registerPullTerminalHandlers(null);
  }, [toast, qc]);

  // 订阅任务（引用计数，deps 仅用稳定的 key，effect 内解析）
  useEffect(() => {
    const ids = taskIdsKey ? taskIdsKey.split(",") : [];
    const unsubscribes = ids.map((id) => subscribePullTask(id));
    return () => unsubscribes.forEach((unsubscribe) => unsubscribe());
  }, [taskIdsKey]);

  return { states, reconnect: reconnectPullTask };
}

/**
 * 单任务拉取进度 Hook
 *
 * 基于 useAllPullProgress 实现，复用多任务 SSE 管理逻辑。
 * 适用于只需要跟踪单个任务进度的场景。
 *
 * @param taskId 任务 ID
 * @returns 进度状态和连接状态
 */
export function usePullProgress(taskId: string | null) {
  const { states, reconnect } = useAllPullProgress(taskId ? [taskId] : []);
  const state = taskId ? states[taskId] : undefined;

  return {
    progress: state?.progress ?? null,
    status: state?.error ? "failed" : state?.status ?? null,
    error: state?.error ?? null,
    connected: state?.connected ?? false,
    reconnect: () => taskId && reconnect(taskId),
  };
}
