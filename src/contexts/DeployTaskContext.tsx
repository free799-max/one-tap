/**
 * 部署任务全局状态上下文
 *
 * 提供跨页面共享的“正在部署”任务列表与统一进度弹窗。
 * - startTask：添加任务并自动弹出进度弹窗
 * - removeTask：移除任务
 * - openPanel / closePanel / togglePanel：控制进度弹窗显隐
 */

import {
  createContext,
  useCallback,
  useEffect,
  useState,
} from "react";

import api, { getToken } from "@/lib/api";
import { useAuth } from "@/hooks/useAuth";

interface DeployTaskContextValue {
  activeTaskIds: string[];
  isPanelOpen: boolean;
  startTask: (taskId: string) => void;
  removeTask: (taskId: string) => void;
  openPanel: () => void;
  closePanel: () => void;
  togglePanel: () => void;
}

// eslint-disable-next-line react-refresh/only-export-components
export const DeployTaskContext = createContext<DeployTaskContextValue | null>(
  null
);

const STORAGE_KEY = "onetap:deploy-task-ids";

/** 读取当前标签页保存的任务 ID；刷新/重新登录后仍能回到进度面板 */
function readStoredTaskIds(): string[] {
  try {
    const parsed = JSON.parse(sessionStorage.getItem(STORAGE_KEY) || "[]");
    return Array.isArray(parsed)
      ? parsed.filter((id): id is string => typeof id === "string" && !!id)
      : [];
  } catch {
    return [];
  }
}

export function DeployTaskProvider({ children }: { children: React.ReactNode }) {
  const [activeTaskIds, setActiveTaskIds] = useState<string[]>(readStoredTaskIds);
  const [isPanelOpen, setIsPanelOpen] = useState(false);

  // 任务 ID 持久化到 sessionStorage：token 过期跳登录或刷新页面后，
  // 顶部「部署中」入口和进度面板仍能重新连上仍在运行的任务
  useEffect(() => {
    try {
      if (activeTaskIds.length > 0) {
        sessionStorage.setItem(STORAGE_KEY, JSON.stringify(activeTaskIds));
      } else {
        sessionStorage.removeItem(STORAGE_KEY);
      }
    } catch {
      // 隐私模式等场景下写入失败时忽略，不影响内存中的任务跟踪
    }
  }, [activeTaskIds]);

  const { user } = useAuth();

  // 登录后从后端恢复仍在进行的任务：刷新页面或 token 过期重新登录后，
  // 顶部「部署中」入口仍能连上后台任务继续查看进度
  useEffect(() => {
    if (!user || !getToken()) return;
    let cancelled = false;
    api
      .get<string[]>("/deploy-tasks")
      .then((response) => {
        if (cancelled || !Array.isArray(response.data)) return;
        const ids = response.data.filter(
          (id): id is string => typeof id === "string" && !!id
        );
        if (ids.length === 0) return;
        setActiveTaskIds((prev) => Array.from(new Set([...prev, ...ids])));
      })
      .catch(() => {
        // 接口不可用时忽略，不影响手动发起的新部署任务
      });
    return () => {
      cancelled = true;
    };
  }, [user]);

  const startTask = useCallback((taskId: string) => {
    if (!taskId) return;
    setActiveTaskIds((prev) => {
      if (prev.includes(taskId)) return prev;
      return [...prev, taskId];
    });
    setIsPanelOpen(true);
  }, []);

  const removeTask = useCallback((taskId: string) => {
    setActiveTaskIds((prev) => prev.filter((id) => id !== taskId));
  }, []);

  const openPanel = useCallback(() => setIsPanelOpen(true), []);
  const closePanel = useCallback(() => setIsPanelOpen(false), []);
  const togglePanel = useCallback(
    () => setIsPanelOpen((prev) => !prev),
    []
  );

  // 所有任务结束（或清空）后自动关闭弹窗
  useEffect(() => {
    if (activeTaskIds.length === 0 && isPanelOpen) {
      setIsPanelOpen(false);
    }
  }, [activeTaskIds.length, isPanelOpen]);

  return (
    <DeployTaskContext.Provider
      value={{
        activeTaskIds,
        isPanelOpen,
        startTask,
        removeTask,
        openPanel,
        closePanel,
        togglePanel,
      }}
    >
      {children}
    </DeployTaskContext.Provider>
  );
}
