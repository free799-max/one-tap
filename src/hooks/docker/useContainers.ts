/**
 * Docker 容器相关 React Query hooks
 *
 * - useDockerStatus：查询 Docker 服务是否可用
 * - useContainers / useContainerDetail：容器查询
 * - useContainerAction / useCreateContainer / useBatchContainerAction：容器操作
 * - useContainerLogsStream：SSE 日志流
 * - useContainerExec：容器内执行命令
 *
 * 查询默认不自动刷新，仅在挂载/手动触发时拉取，避免页面不必要的刷新。
 */

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useState, useEffect, useRef, useCallback } from "react";
import { useToast } from "@/components/ui/toast";
import { getToken } from "@/lib/api";
import { appendLogLine } from "@/lib/log-stream";
import type { ApiError } from "@/api/types";
import * as dockerApi from "@/api/docker";
import type {
  ContainerActionResponse,
  ContainerBatchActionRequest,
  ContainerCreateRequest,
  ContainerDetail,
  ContainerExecRequest,
  ContainerInfo,
} from "@/api/docker";

/**
 * 查询 Docker 服务状态
 *
 * 检测 Docker 守护进程是否可用。
 *
 * @returns useQuery 对象，data 类型为 { available: boolean }
 */
export function useDockerStatus() {
  return useQuery<{ available: boolean }>({
    queryKey: ["docker", "status"],
    queryFn: dockerApi.getDockerStatus,
  });
}

/**
 * 查询容器列表
 *
 * 获取当前用户的所有容器信息。
 *
 * @returns useQuery 对象，data 类型为 ContainerInfo 数组
 */
export function useContainers() {
  return useQuery<ContainerInfo[]>({
    queryKey: ["docker", "containers"],
    queryFn: dockerApi.listContainers,
    refetchInterval: 10000,
  });
}

/**
 * 对容器执行操作（mutation）
 *
 * 向后端发送容器操作请求（如启动、停止、重启等），
 * 操作成功后自动刷新容器列表缓存。
 *
 * @returns useMutation 对象，传入参数为 { id: 容器ID, action: 操作类型 }
 */
export function useContainerAction() {
  // 获取 QueryClient 实例，用于手动刷新缓存
  const qc = useQueryClient();
  const toast = useToast();
  // 短时轮询定时器引用，组件卸载时清理，避免无意义请求
  const pollTimerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  useEffect(
    () => () => {
      if (pollTimerRef.current) {
        clearInterval(pollTimerRef.current);
      }
    },
    []
  );
  return useMutation({
    // 向容器操作接口发送 POST 请求（action 与后端 ContainerAction 保持一致）
    mutationFn: ({
      id,
      action,
    }: {
      id: string;
      action: "start" | "stop" | "restart";
    }): Promise<ContainerActionResponse> => dockerApi.containerAction(id, action),
    // 操作成功后，使容器列表缓存失效以触发重新查询，并短时轮询确保状态稳定
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["docker", "containers"] });
      toast.success("操作成功");

      // 短时轮询：500ms 间隔，最多 6 次（3 秒），让容器状态尽快同步到最新
      if (pollTimerRef.current) {
        clearInterval(pollTimerRef.current);
      }
      let count = 0;
      const maxPolls = 6;
      const interval = 500;
      pollTimerRef.current = setInterval(() => {
        count += 1;
        qc.refetchQueries({ queryKey: ["docker", "containers"] });
        if (count >= maxPolls && pollTimerRef.current) {
          clearInterval(pollTimerRef.current);
          pollTimerRef.current = null;
        }
      }, interval);
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "操作失败");
    },
  });
}

/**
 * 创建容器（mutation）
 *
 * @returns useMutation 对象，传入参数为 ContainerCreateRequest
 */
export function useCreateContainer() {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: (data: ContainerCreateRequest) =>
      dockerApi.createContainer(data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["docker", "containers"] });
      toast.success("容器创建成功");
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "创建容器失败");
    },
  });
}

/**
 * 批量操作容器（mutation）
 *
 * @returns useMutation 对象，传入参数为 ContainerBatchActionRequest
 */
export function useBatchContainerAction() {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: (data: ContainerBatchActionRequest) =>
      dockerApi.batchContainerAction(data),
    onSuccess: (result, variables) => {
      qc.invalidateQueries({ queryKey: ["docker", "containers"] });
      const actionMap: Record<string, string> = {
        start: "启动",
        stop: "停止",
        restart: "重启",
        remove: "删除",
      };
      const actionText = actionMap[variables.action] || variables.action;
      const succeeded = result.succeeded?.length || 0;
      const failed = result.failed?.length || 0;
      if (succeeded > 0) {
        toast.success(`${actionText} ${succeeded} 个容器成功`);
      }
      if (failed > 0) {
        toast.error(`${failed} 个容器${actionText}失败`);
      }
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "批量操作失败");
    },
  });
}

/**
 * 查询容器详情
 *
 * @param container_id 容器 ID
 * @returns useQuery 对象，data 类型为 ContainerDetail
 */
export function useContainerDetail(container_id: string | null) {
  return useQuery<ContainerDetail>({
    queryKey: ["docker", "containers", "detail", container_id],
    queryFn: () => dockerApi.getContainerDetail(container_id!),
    enabled: !!container_id,
  });
}

/**
 * 容器日志流元数据
 *
 * SSE 建立后首条消息携带的容器实时状态信息。
 */
export interface ContainerLogMeta {
  /** 容器短 ID */
  container_id: string;
  /** 容器名称 */
  name: string;
  /** Docker 原始状态，如 running / exited */
  status: string;
  /** 状态中文摘要 */
  state: string;
}

/**
 * 流式获取容器日志（SSE）
 *
 * 日志行数有上限（MAX_LOG_LINES），超出后丢弃最早的行，防止内存无限增长。
 *
 * @param container_id 容器 ID
 * @param tail 返回最后 N 行日志
 * @param onError 错误回调，由调用方决定如何展示错误（如 toast 并关闭弹窗）
 * @returns 日志行数组、连接状态、容器元数据、清空函数
 */
export function useContainerLogsStream(
  container_id: string | null,
  tail: number = 100,
  onError?: (message: string) => void
) {
  const [logs, setLogs] = useState<string[]>([]);
  const [connected, setConnected] = useState(false);
  const [meta, setMeta] = useState<ContainerLogMeta | null>(null);
  const onErrorRef = useRef(onError);

  // 保持 onError 最新引用，避免其变化导致 SSE 反复重建
  useEffect(() => {
    onErrorRef.current = onError;
  }, [onError]);

  const clearLogs = useCallback(() => setLogs([]), []);

  useEffect(() => {
    if (!container_id) return;

    const token = getToken() || "";
    const es = new EventSource(
      `/api/docker/containers/${container_id}/logs/stream?tail=${tail}&follow=true&timestamps=true&token=${encodeURIComponent(token)}`
    );

    es.onopen = () => {
      setConnected(true);
      setLogs([]);
      setMeta(null);
    };

    es.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.error) {
          onErrorRef.current?.(data.error);
          setConnected(false);
          return;
        }
        if (data.meta) {
          setMeta(data.meta as ContainerLogMeta);
          return;
        }
        if (typeof data.line === "string") {
          setLogs((prev) => appendLogLine(prev, data.line));
        }
      } catch {
        // 忽略解析错误
      }
    };

    es.onerror = () => {
      setConnected(false);
    };

    return () => {
      es.close();
      setLogs([]);
      setMeta(null);
    };
  }, [container_id, tail]);

  return { logs, connected, meta, clearLogs };
}

/**
 * 在容器内执行命令（mutation）
 *
 * @returns useMutation 对象，传入参数为 { id, data }
 */
export function useContainerExec() {
  const toast = useToast();
  return useMutation({
    mutationFn: ({ id, data }: { id: string; data: ContainerExecRequest }) =>
      dockerApi.containerExec(id, data),
    onSuccess: () => {
      toast.success("命令执行完成");
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "执行命令失败");
    },
  });
}
