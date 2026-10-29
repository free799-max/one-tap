/**
 * 轻量级 Toast 通知组件
 *
 * 支持成功/错误类型，右上角显示，自动关闭。
 * 不依赖外部库，纯 React + Tailwind 实现。
 */

import { useState, useEffect, useCallback, createContext, useContext, useMemo } from "react";
import { CheckCircle, AlertCircle, X } from "lucide-react";

export interface Toast {
  id: number;
  message: string;
  type: "success" | "error";
  duration?: number;
}

interface ToastContextValue {
  toast: {
    success: (message: string, options?: { duration?: number }) => void;
    error: (message: string, options?: { duration?: number }) => void;
  };
}

const ToastContext = createContext<ToastContextValue | undefined>(undefined);

let toastIdCounter = 0;

function ToastItem({
  toast,
  onRemove,
}: {
  toast: Toast;
  onRemove: (id: number) => void;
}) {
  /**
   * 组件挂载后启动自动关闭定时器
   */
  useEffect(() => {
    const timer = setTimeout(() => onRemove(toast.id), toast.duration ?? 3000);
    return () => clearTimeout(timer);
  }, [toast.id, toast.duration, onRemove]);

  return (
    <div
      className={`pointer-events-auto flex items-center gap-3 px-5 py-3 rounded-xl text-sm text-white shadow-lg transition-all duration-300 ${
        toast.type === "success"
          ? "bg-emerald-500/90 border border-emerald-400/30"
          : "bg-red-500/90 border border-red-400/30"
      }`}
      style={{ backdropFilter: "blur(12px)" }}
    >
      {toast.type === "success" ? (
        <CheckCircle className="h-4 w-4 shrink-0" />
      ) : (
        <AlertCircle className="h-4 w-4 shrink-0" />
      )}
      <span>{toast.message}</span>
      <button
        onClick={() => onRemove(toast.id)}
        className="ml-1 p-0.5 rounded hover:bg-white/20 transition-colors"
      >
        <X className="h-3 w-3" />
      </button>
    </div>
  );
}

export function ToastProvider({ children }: { children: React.ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const removeToast = useCallback((id: number) => {
    setToasts((prev) => prev.filter((t) => t.id !== id));
  }, []);

  const addToast = useCallback(
    (message: string, type: "success" | "error", duration?: number) => {
      const id = ++toastIdCounter;
      setToasts((prev) => [...prev, { id, message, type, duration }]);
    },
    []
  );

  const toastValue = useMemo(
    () => ({
      toast: {
        success: (message: string, options?: { duration?: number }) =>
          addToast(message, "success", options?.duration),
        error: (message: string, options?: { duration?: number }) =>
          addToast(message, "error", options?.duration),
      },
    }),
    [addToast]
  );

  return (
    <ToastContext.Provider value={toastValue}>
      {children}
      <div className="fixed top-4 right-4 z-[9999] flex flex-col gap-3 pointer-events-none">
        {toasts.map((t) => (
          <ToastItem key={t.id} toast={t} onRemove={removeToast} />
        ))}
      </div>
    </ToastContext.Provider>
  );
}

// eslint-disable-next-line react-refresh/only-export-components
export function useToast() {
  const context = useContext(ToastContext);
  if (context === undefined) {
    throw new Error("useToast 必须在 ToastProvider 内部使用");
  }
  return context.toast;
}
