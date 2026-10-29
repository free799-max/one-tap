/**
 * Docker 镜像拉取进度全局内存态 Store（zustand）
 *
 * 替代原先 localStorage + 手动 dispatchEvent('storage') + 2 秒轮询的同步方案：
 * - history：拉取任务历史（内存态，页面刷新后不保留）
 * - states：每个任务的实时进度状态
 * - SSE 连接由模块级管理器统一维护，多个 UI 组件对同一任务共享一条连接
 *   （引用计数订阅，最后一个订阅者取消时才断开）
 * - 终结状态（completed/failed）：立即关闭连接、更新历史、
 *   触发已注册的 UI 回调（toast + 镜像列表缓存失效），不再重连
 */

import { create } from "zustand";
import { getPullTaskStatus } from "@/api/docker";
import type { PullProgressEvent } from "@/api/docker";
import { getToken } from "@/lib/api";

/** 最大保留历史记录数 */
export const MAX_HISTORY_COUNT = 10;

/** 单任务自动重连上限 */
const RECONNECT_LIMIT = 5;
/** 重连间隔 */
const RECONNECT_DELAY_MS = 2000;
/** 拉取超时时间：超时后前端将任务标记为失败 */
const PULL_TIMEOUT_MS = 10 * 60 * 1000;

/** 拉取历史记录项 */
export interface PullHistoryItem {
  /** 任务唯一标识 */
  taskId: string;
  /** 镜像名称（含标签） */
  image: string;
  /** 任务状态 */
  status: "pulling" | "completed" | "failed" | "cancelled";
  /** 任务开始时间戳 */
  createdAt: number;
  /** 任务完成/失败时间戳 */
  completedAt: number | null;
  /** 最后活跃时间戳（收到进度或状态更新） */
  lastActivityAt: number;
  /** 错误信息 */
  error: string | null;
  /** 完成/失败时的最终进度快照 */
  finalProgress: PullProgressEvent | null;
}

/** 单个任务的实时进度状态 */
export interface PullTaskState {
  progress: PullProgressEvent | null;
  status: "pulling" | "completed" | "failed" | null;
  error: string | null;
  connected: boolean;
}

interface PullProgressStoreState {
  history: PullHistoryItem[];
  states: Record<string, PullTaskState>;
  addTask: (taskId: string, image: string) => void;
  updateHistoryItem: (
    taskId: string,
    partial: Partial<Omit<PullHistoryItem, "taskId" | "image" | "createdAt">>
  ) => void;
  removeHistoryItem: (taskId: string) => void;
  clearHistory: () => void;
  patchTaskState: (taskId: string, patch: Partial<PullTaskState>) => void;
}

/** 按 createdAt 降序排列（最新的在最前面） */
function sortPullHistory(items: PullHistoryItem[]): PullHistoryItem[] {
  return [...items].sort((a, b) => b.createdAt - a.createdAt);
}

/** 超限清理：仅保留最新的 MAX_HISTORY_COUNT 条 */
function capHistory(items: PullHistoryItem[]): PullHistoryItem[] {
  if (items.length <= MAX_HISTORY_COUNT) return items;
  return sortPullHistory(items).slice(0, MAX_HISTORY_COUNT);
}

export const usePullProgressStore = create<PullProgressStoreState>((set) => ({
  history: [],
  states: {},

  addTask: (taskId, image) =>
    set((s) => ({
      history: capHistory(
        sortPullHistory([
          ...s.history.filter((item) => item.taskId !== taskId),
          {
            taskId,
            image,
            status: "pulling" as const,
            createdAt: Date.now(),
            completedAt: null,
            lastActivityAt: Date.now(),
            error: null,
            finalProgress: null,
          },
        ])
      ),
    })),

  updateHistoryItem: (taskId, partial) =>
    set((s) => {
      const index = s.history.findIndex((item) => item.taskId === taskId);
      if (index === -1) return s;
      const updated: PullHistoryItem = {
        ...s.history[index],
        ...partial,
        lastActivityAt: Date.now(),
      };
      if (
        ["completed", "failed", "cancelled"].includes(updated.status) &&
        updated.completedAt === null
      ) {
        updated.completedAt = Date.now();
      }
      const history = [...s.history];
      history[index] = updated;
      return { history };
    }),

  removeHistoryItem: (taskId) =>
    set((s) => ({
      history: s.history.filter((item) => item.taskId !== taskId),
    })),

  clearHistory: () => set({ history: [] }),

  patchTaskState: (taskId, patch) =>
    set((s) => {
      const prev: PullTaskState = s.states[taskId] ?? {
        progress: null,
        status: null,
        error: null,
        connected: false,
      };
      return { states: { ...s.states, [taskId]: { ...prev, ...patch } } };
    }),
}));

/* ===================== SSE 连接管理器（模块级，跨组件共享） ===================== */

/** 终结状态回调，由 React 侧（useAllPullProgress）注册，用于 toast 与缓存失效 */
export interface PullTerminalHandlers {
  onCompleted: (taskId: string, image: string) => void;
  onFailed: (taskId: string, image: string, error: string | null) => void;
}

let terminalHandlers: PullTerminalHandlers | null = null;

/** 注册/注销终结状态回调（同一时间只保留最新一份） */
export function registerPullTerminalHandlers(
  handlers: PullTerminalHandlers | null
) {
  terminalHandlers = handlers;
}

interface TaskConnection {
  es: EventSource | null;
  reconnects: number;
  subscribers: number;
  reconnectTimer: ReturnType<typeof setTimeout> | null;
  timeoutTimer: ReturnType<typeof setTimeout> | null;
  /** 任务已进入终结状态（completed/failed），不再重连 */
  finalized: boolean;
}

const connections = new Map<string, TaskConnection>();

function getHistoryImage(taskId: string): string {
  return (
    usePullProgressStore.getState().history.find((i) => i.taskId === taskId)
      ?.image ?? ""
  );
}

/** 乐观初始化：任务添加后立即显示「准备拉取」（已有状态则跳过） */
function initOptimisticState(taskId: string) {
  const existing = usePullProgressStore.getState().states[taskId];
  if (existing) return;
  usePullProgressStore.getState().patchTaskState(taskId, {
    progress: {
      total_layers: 0,
      completed_layers: 0,
      current_layer: "",
      percentage: 0,
      status: "准备拉取",
      speed: 0,
      total_size: 0,
      downloaded_size: 0,
      size_text: "--",
      layers: [],
    },
    status: "pulling",
    error: null,
    connected: true,
  });
}

/** 任务终结：更新历史、触发回调、关闭连接、清理定时器（幂等） */
function finalizeTask(
  conn: TaskConnection,
  taskId: string,
  status: "completed" | "failed",
  error: string | null,
  finalProgress: PullProgressEvent | null
) {
  if (conn.finalized) return;
  conn.finalized = true;

  if (conn.es) {
    conn.es.close();
    conn.es = null;
  }
  if (conn.reconnectTimer) {
    clearTimeout(conn.reconnectTimer);
    conn.reconnectTimer = null;
  }
  if (conn.timeoutTimer) {
    clearTimeout(conn.timeoutTimer);
    conn.timeoutTimer = null;
  }

  const store = usePullProgressStore.getState();
  store.patchTaskState(taskId, { status, error, connected: false });
  store.updateHistoryItem(taskId, { status, error, finalProgress });

  const image = getHistoryImage(taskId);
  if (status === "completed") {
    terminalHandlers?.onCompleted(taskId, image);
  } else {
    terminalHandlers?.onFailed(taskId, image, error);
  }
}

function connect(conn: TaskConnection, taskId: string) {
  conn.reconnects += 1;

  const token = getToken() || "";
  const es = new EventSource(
    `/api/docker/images/pull/${taskId}/events?token=${encodeURIComponent(token)}`
  );
  conn.es = es;

  es.onmessage = (event) => {
    try {
      const raw = JSON.parse(event.data);
      const data = raw as PullProgressEvent;
      // 后端在任务完成/失败时嵌入 _task_status 和 _error
      const taskStatus = (raw as Record<string, unknown>)._task_status as
        | "completed"
        | "failed"
        | undefined;
      const taskError =
        ((raw as Record<string, unknown>)._error as string | undefined) ?? null;

      const store = usePullProgressStore.getState();
      store.patchTaskState(taskId, { progress: data });
      store.updateHistoryItem(taskId, {});

      if (taskStatus === "completed" || taskStatus === "failed") {
        finalizeTask(conn, taskId, taskStatus, taskError, data);
      }
    } catch {
      // 忽略解析错误
    }
  };

  es.onerror = () => {
    es.close();
    if (conn.es === es) conn.es = null;
    // 已终结的任务（服务端正常关流也会触发 error）不再重连
    if (conn.finalized) return;

    usePullProgressStore.getState().patchTaskState(taskId, {
      connected: false,
    });

    // 自动重连（最多 RECONNECT_LIMIT 次）
    if (conn.reconnects < RECONNECT_LIMIT) {
      conn.reconnectTimer = setTimeout(() => {
        conn.reconnectTimer = null;
        if (!conn.finalized && !conn.es) connect(conn, taskId);
      }, RECONNECT_DELAY_MS);
    }
  };
}

/** 通过 REST API 获取当前状态（保底 + 恢复页面刷新后的任务状态） */
async function fetchRestStatus(conn: TaskConnection, taskId: string) {
  try {
    const task = await getPullTaskStatus(taskId);
    if (conn.finalized) return;

    const store = usePullProgressStore.getState();
    const existing = store.states[taskId];
    // 已有 SSE 推送的有效进度时优先保留，防止 REST 旧数据覆盖
    const keepSseProgress =
      existing?.progress &&
      existing.progress.percentage > 0 &&
      task.status === "pulling";
    store.patchTaskState(taskId, {
      progress: keepSseProgress ? existing.progress : task.progress,
      status: task.status,
      error: task.error,
      connected: true,
    });

    if (task.status === "completed" || task.status === "failed") {
      finalizeTask(conn, taskId, task.status, task.error, task.progress);
    }
  } catch {
    // 状态接口失败不影响 SSE
  }
}

/** 启动超时检测定时器（模块级，不受组件渲染影响而重置） */
function startTimeoutTimer(conn: TaskConnection, taskId: string) {
  conn.timeoutTimer = setTimeout(() => {
    conn.timeoutTimer = null;
    const state = usePullProgressStore.getState().states[taskId];
    if (state?.status !== "pulling") return;
    finalizeTask(
      conn,
      taskId,
      "failed",
      "拉取超时，请检查网络或镜像源配置",
      state.progress ?? null
    );
  }, PULL_TIMEOUT_MS);
}

function teardown(taskId: string, conn: TaskConnection) {
  if (conn.es) {
    conn.es.close();
    conn.es = null;
  }
  if (conn.reconnectTimer) clearTimeout(conn.reconnectTimer);
  if (conn.timeoutTimer) clearTimeout(conn.timeoutTimer);
  connections.delete(taskId);
}

/**
 * 订阅某个拉取任务（引用计数）。首次订阅建立共享 SSE 连接，
 * 最后一个订阅者取消时断开连接并清理定时器。
 */
export function subscribePullTask(taskId: string): () => void {
  let conn = connections.get(taskId);
  if (!conn) {
    conn = {
      es: null,
      reconnects: 0,
      subscribers: 0,
      reconnectTimer: null,
      timeoutTimer: null,
      finalized: false,
    };
    connections.set(taskId, conn);
    initOptimisticState(taskId);
    startTimeoutTimer(conn, taskId);
    connect(conn, taskId);
    void fetchRestStatus(conn, taskId);
  }
  conn.subscribers += 1;

  return () => {
    conn.subscribers -= 1;
    if (conn.subscribers <= 0) {
      teardown(taskId, conn);
    }
  };
}

/** 手动重连（全部或指定任务） */
export function reconnectPullTask(targetTaskId?: string) {
  const ids = targetTaskId ? [targetTaskId] : [...connections.keys()];
  for (const taskId of ids) {
    const conn = connections.get(taskId);
    if (!conn) continue;
    if (conn.es) {
      conn.es.close();
      conn.es = null;
    }
    if (conn.reconnectTimer) {
      clearTimeout(conn.reconnectTimer);
      conn.reconnectTimer = null;
    }
    conn.reconnects = 0;
    if (!conn.finalized) connect(conn, taskId);
  }
}
