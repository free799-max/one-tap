/**
 * Docker Compose 日志相关 hooks
 *
 * - useComposeLogs：一次性查询日志
 * - useComposeLogsStream：SSE 实时日志流（行数有上限，防止内存无限增长）
 */

import { useQuery } from "@tanstack/react-query";
import { useState, useEffect, useRef, useCallback } from "react";
import { getToken } from "@/lib/api";
import { appendLogLine } from "@/lib/log-stream";
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

// eslint-disable-next-line no-control-regex
const ANSI_PATTERN = /\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])/g;

function stripAnsi(text: string): string {
  return text.replace(ANSI_PATTERN, "");
}

function cleanLogLine(line: string): string {
  const cleaned = stripAnsi(line).replace(/\r?\n$/, "");
  if (cleaned.includes("\r")) {
    return cleaned.split("\r").pop() ?? cleaned;
  }
  return cleaned;
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
  const [logs, setLogs] = useState<string[]>([]);
  const [connected, setConnected] = useState(false);
  const onErrorRef = useRef(onError);

  // 保持 onError 最新引用，避免其变化导致 SSE 反复重建
  useEffect(() => {
    onErrorRef.current = onError;
  }, [onError]);

  const clearLogs = useCallback(() => setLogs([]), []);

  useEffect(() => {
    if (!projectId) return;

    const token = getToken() || "";
    const es = new EventSource(
      `/api/docker/compose/${projectId}/logs/stream?tail=${tail}&follow=true&token=${encodeURIComponent(token)}`
    );

    es.onopen = () => {
      setConnected(true);
      setLogs([]);
    };

    es.onmessage = (event) => {
      try {
        const data = JSON.parse(event.data);
        if (data.error) {
          onErrorRef.current?.(data.error);
          setConnected(false);
          return;
        }
        if (typeof data.line === "string") {
          setLogs((prev) => appendLogLine(prev, cleanLogLine(data.line)));
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
      setConnected(false);
    };
  }, [projectId, tail]);

  return { logs, connected, clearLogs };
}
