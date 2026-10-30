/**
 * SSE 日志流通用 hook
 *
 * 统一处理鉴权 token 拼接、ANSI 清理、行数上限与断开清理。
 * 不同日志来源（Compose 项目、应用实例、容器）只需传入各自的日志流地址。
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { getToken } from "@/lib/api";
import { appendLogLine } from "@/lib/log-stream";

// eslint-disable-next-line no-control-regex
const ANSI_PATTERN = /\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])/g;

/** 移除 ANSI 转义序列，并把进度条的 \r 覆盖处理成最终内容 */
function cleanLogLine(line: string): string {
  const cleaned = line.replace(ANSI_PATTERN, "").replace(/\r?\n$/, "");
  if (cleaned.includes("\r")) {
    return cleaned.split("\r").pop() ?? cleaned;
  }
  return cleaned;
}

/** 为 EventSource 地址追加鉴权 token（EventSource 不支持自定义请求头） */
export function withLogToken(url: string): string {
  const token = getToken() || "";
  const separator = url.includes("?") ? "&" : "?";
  return `${url}${separator}token=${encodeURIComponent(token)}`;
}

/**
 * 订阅日志流
 *
 * @param url 已带鉴权 token 的 SSE 地址；为 null 时不连接
 * @param onError 服务端推送错误时的回调（如 toast 并关闭弹窗）
 */
export function useLogsStream(
  url: string | null,
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
    if (!url) return;

    const es = new EventSource(url);

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
  }, [url]);

  return { logs, connected, clearLogs };
}
