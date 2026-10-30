/**
 * 部署任务相关 React hooks
 *
 * 提供应用商店和 Compose 部署任务的 SSE 进度监听。
 */

import { useEffect, useRef, useState } from "react";

import { useContext } from "react";
import { DeployTaskContext } from "@/contexts/DeployTaskContext";
import api, { getToken } from "@/lib/api";

/** 获取全局部署任务上下文 */
export function useDeployTasks() {
  const ctx = useContext(DeployTaskContext);
  if (!ctx) {
    throw new Error("useDeployTasks 必须在 DeployTaskProvider 内使用");
  }
  return ctx;
}

/** 部署任务进度 */
export interface DeployTaskProgress {
  stage: string;
  percentage: number;
  message: string;
  detail?: string | null;
}

/** SSE 事件数据 */
interface DeployTaskEvent extends DeployTaskProgress {
  _task_status?: "deploying" | "completed" | "failed" | "cancelled";
  _error?: string;
  _warning?: string | null;
  _meta?: Record<string, unknown> | null;
}

/** 部署任务状态 */
export interface DeployTaskState {
  taskId: string;
  type: string;
  status: "deploying" | "completed" | "failed" | "cancelled";
  stage: string;
  progress: DeployTaskProgress;
  error?: string | null;
  /** 任务成功但有遗留问题的说明（如删除时数据目录未能备份） */
  warning?: string | null;
  action?: string | null;
  projectId?: number | null;
  instanceId?: number | null;
  meta?: Record<string, unknown> | null;
  logs?: string[];
}

function getApiBaseUrl(): string {
  return import.meta.env.VITE_API_BASE_URL || "";
}

function parseDeployEvent(taskId: string, eventData: string): DeployTaskState | null {
  try {
    const data = JSON.parse(eventData) as DeployTaskEvent;
    const taskStatus = data._task_status || "deploying";
    return {
      taskId,
      type: data.stage?.startsWith("compose") ? "compose" : "app_deploy",
      status: taskStatus,
      stage: data.stage,
      progress: {
        stage: data.stage,
        percentage: data.percentage,
        message: data.message,
        detail: data.detail,
      },
      error: data._error || null,
      warning: data._warning || null,
      meta: data._meta,
    };
  } catch {
    return null;
  }
}

const statusWordMap: Record<string, string> = {
  Creating: "创建中",
  Created: "已创建",
  Recreate: "重建中",
  Recreated: "已重建",
  Recreating: "重建中",
  Starting: "启动中",
  Started: "已启动",
  Stopping: "停止中",
  Stopped: "已停止",
  Killing: "停止中",
  Killed: "已停止",
  Removing: "删除中",
  Removed: "已删除",
  Rename: "重命名中",
  Renamed: "已重命名",
  Waiting: "等待中",
  Pulling: "拉取中",
  Pulled: "已拉取",
  Extracting: "解压中",
  Downloading: "下载中",
  Downloaded: "已下载",
  Building: "构建中",
  Built: "已构建",
  Running: "运行中",
  Error: "错误",
  Done: "完成",
};

function translateStatusWord(line: string): string {
  const tokens = line.trim().split(/\s+/);
  if (tokens.length === 0) return line;
  const last = tokens[tokens.length - 1];
  if (statusWordMap[last]) {
    tokens[tokens.length - 1] = statusWordMap[last];
  }
  return tokens.join(" ");
}

const ANSI_ESC = String.fromCharCode(27);

function stripAnsi(text: string): string {
  // 移除 ANSI 转义序列（颜色、光标移动、清行等）
  return text.replace(
    new RegExp(`${ANSI_ESC}(?:[@-Z\\\\-_]|\\[[0-?]*[ -/]*[@-~])`, "g"),
    ""
  );
}

function cleanDetailLine(detail: string): string {
  // 把 docker compose 输出里的 Network/Container 翻译成中文，并去掉前置空格、翻译状态词
  const translatedPrefix = stripAnsi(detail)
    .trimStart()
    .replace(/^Network\s+/, "网络 ")
    .replace(/^Container\s+/, "容器 ");
  return translateStatusWord(translatedPrefix);
}

// docker 层状态行：12 位十六进制层 ID + 状态词（尺寸单位为 MB/GB，可能不带 /总量）
const DOCKER_LAYER_LINE_RE =
  /^[0-9a-f]{12}\s+(Waiting|Pulling fs layer|Downloading|Verifying Checksum|Download complete|Extracting|Pull complete|Already exists)\b/;
// 通用进度条：current/total，单位覆盖 docker（KB/MB/GB）与包管理器（KiB/MiB/GiB）
const SIZE_PROGRESS_RE =
  /\d+(\.\d+)?\s*(KiB|MiB|GiB|KB|MB|GB)\s*\/\s*\d+(\.\d+)?\s*(KiB|MiB|GiB|KB|MB|GB)/;

/**
 * 判断一行日志是否属于进度条行（docker 层状态或 current/total 尺寸）。
 * 例如："c949c4a16af4 Downloading 22.02MB"、"click ---- 94.81 KiB/116.45 KiB"
 */
function isProgressLine(line: string): boolean {
  return DOCKER_LAYER_LINE_RE.test(line) || SIZE_PROGRESS_RE.test(line);
}

/**
 * 提取进度条行的包名/标识前缀，用于判断是否可以覆盖上一条同类进度。
 */
function progressLineKey(line: string): string | null {
  const match = line.match(/^(\S+)/);
  return match ? match[1] : null;
}

function appendDeployLogs(
  existing: DeployTaskState | undefined,
  next: DeployTaskState
): string[] {
  const logs = existing?.logs ? [...existing.logs] : [];
  const lastMessage = existing?.progress.message;
  const lastDetail = existing?.progress.detail;
  const message = next.progress.message;
  const detail = next.progress.detail;

  if (message && message !== lastMessage) {
    // 升级流程逐个应用推进，块之间留空行，避免所有应用日志粘成一片
    if (
      /^升级 .+（\d+\/\d+）$/.test(message) &&
      logs.length > 0 &&
      logs[logs.length - 1] !== ""
    ) {
      logs.push("");
    }
    logs.push(message);
  }
  if (detail) {
    const cleaned = cleanDetailLine(detail);
    const lastCleaned = lastDetail ? cleanDetailLine(lastDetail) : undefined;
    if (cleaned && cleaned !== lastCleaned) {
      // 进度行按标识覆盖：docker 多层下载是交错的（A B C A' B'...），
      // 只比较最后一行会漏掉，需向前回溯找同层行原地替换，每层只保留最新一帧
      if (isProgressLine(cleaned)) {
        const key = progressLineKey(cleaned);
        if (key) {
          const searchStart = Math.max(0, logs.length - 50);
          for (let i = logs.length - 1; i >= searchStart; i--) {
            if (isProgressLine(logs[i]) && progressLineKey(logs[i]) === key) {
              logs[i] = cleaned;
              return logs;
            }
          }
        }
      }
      logs.push(cleaned);
    }
  }
  return logs;
}

/**
 * 同时监听多个部署任务的 SSE 实时进度
 */
export function useDeployTasksEvents(
  taskIds: string[],
  onComplete?: (taskId: string, state: DeployTaskState) => void,
  onError?: (taskId: string, state: DeployTaskState) => void,
  onExpired?: (taskId: string) => void
): Record<string, DeployTaskState> {
  const [states, setStates] = useState<Record<string, DeployTaskState>>({});
  const taskIdsKey = taskIds.join(",");

  // 回调用 ref 保持最新引用，避免其变化导致 SSE 反复重建
  const onCompleteRef = useRef(onComplete);
  const onErrorRef = useRef(onError);
  const onExpiredRef = useRef(onExpired);
  useEffect(() => {
    onCompleteRef.current = onComplete;
    onErrorRef.current = onError;
    onExpiredRef.current = onExpired;
  }, [onComplete, onError, onExpired]);

  useEffect(() => {
    const ids = taskIdsKey ? taskIdsKey.split(",") : [];
    if (ids.length === 0) {
      setStates({});
      return;
    }

    const baseUrl = getApiBaseUrl();
    const token = getToken() || "";
    const sources: Record<string, EventSource> = {};
    let disposed = false;
    // EventSource 读不到 HTTP 状态码，连接异常时用状态接口兜底判断：
    // - 404：任务已被后端清理（正常结束超过 TTL、页面恢复的旧任务或服务重启），
    //   交给 onExpired 从列表移除，不能当成“部署失败”误导用户
    // - 401：token 过期，交给 axios 拦截器统一跳转登录
    // 每个任务 5 秒内最多探测一次，避免 EventSource 重连时刷接口
    const lastProbeAt: Record<string, number> = {};
    const expiredTasks = new Set<string>();

    const probeTask = (taskId: string) => {
      if (expiredTasks.has(taskId)) return;
      const now = Date.now();
      if (now - (lastProbeAt[taskId] ?? 0) < 5000) return;
      lastProbeAt[taskId] = now;
      api.get(`/deploy-tasks/${taskId}/status`).catch((error) => {
        const status = (error as { response?: { status?: number } })?.response
          ?.status;
        if (disposed || status !== 404 || expiredTasks.has(taskId)) return;
        expiredTasks.add(taskId);
        sources[taskId]?.close();
        delete sources[taskId];
        setStates((prev) => {
          if (!(taskId in prev)) return prev;
          const next = { ...prev };
          delete next[taskId];
          return next;
        });
        onExpiredRef.current?.(taskId);
      });
    };

    ids.forEach((taskId) => {
      const url = `${baseUrl}/api/deploy-tasks/${taskId}/events?token=${encodeURIComponent(token)}`;
      const es = new EventSource(url);
      sources[taskId] = es;

      // 连接 SSE 前先确认任务仍在后端，避免卡片永远停在“连接任务…”
      probeTask(taskId);

      es.onmessage = (event) => {
        const newState = parseDeployEvent(taskId, event.data);
        if (!newState) return;

        setStates((prev) => {
          const existing = prev[taskId];
          const logs = appendDeployLogs(existing, newState);
          return { ...prev, [taskId]: { ...newState, logs } };
        });

        if (newState.status === "completed" || newState.status === "cancelled") {
          onCompleteRef.current?.(taskId, newState);
          es.close();
        } else if (newState.status === "failed") {
          onErrorRef.current?.(taskId, newState);
          es.close();
        }
      };

      es.onerror = () => {
        // 不主动 close：让 EventSource 自动重连。
        // 后端 SSE 会回放历史事件，重连后能恢复到最新状态（含终态），
        // 避免网络抖动导致任务卡片永远停在“连接任务…”占位。
        // 但若任务已不存在或 token 已过期，重连不会恢复，需探测后结束占位。
        probeTask(taskId);
      };
    });

    return () => {
      disposed = true;
      Object.values(sources).forEach((es) => es.close());
    };
  }, [taskIdsKey]);

  return states;
}
