/**
 * Docker Compose 日志相关 hooks
 *
 * - useComposeLogs：一次性查询日志
 * - useComposeLogsStream：SSE 实时日志流（实现见通用 hook useLogsStream）
 */

import { useQuery } from "@tanstack/react-query";
import { useLogsStream, withLogToken } from "@/hooks/useLogsStream";
import * as composeApi from "@/api/compose";

/**
 * 查询 Compose 日志
 */
export function useComposeLogs(
  projectId: number | null,
  tail: number = 100,
  services?: string[]
) {
  return useQuery<{ logs: string }>({
    queryKey: ["compose", "projects", projectId, "logs", tail, services],
    queryFn: () => composeApi.getComposeLogs(projectId!, tail, services),
    enabled: !!projectId,
  });
}

/** 构造 Compose 项目日志流地址（已带鉴权 token） */
export function composeLogsStreamUrl(
  projectId: number,
  tail: number = 100,
  follow: boolean = true
): string {
  return withLogToken(
    `/api/docker/compose/${projectId}/logs/stream?tail=${tail}&follow=${follow}`
  );
}

/**
 * 流式获取 Compose 项目日志（SSE）
 *
 * @param projectId 项目 ID
 * @param tail 返回最后 N 行日志
 * @param onError 错误回调，由调用方决定如何展示错误（如 toast 并关闭弹窗）
 * @returns 日志行数组、连接状态、清空函数
 */
export function useComposeLogsStream(
  projectId: number | null,
  tail: number = 100,
  onError?: (message: string) => void
) {
  return useLogsStream(
    projectId ? composeLogsStreamUrl(projectId, tail) : null,
    onError
  );
}
