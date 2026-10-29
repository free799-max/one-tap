/**
 * 认证状态 Hook
 *
 * 封装 AuthContext 的 useContext 调用，提供类型安全的访问方式。
 * 必须在 AuthProvider 包裹的组件树内使用。
 */

import { useContext } from "react";
import { AuthContext } from "@/contexts/AuthContext";

export function useAuth() {
  const context = useContext(AuthContext);
  if (context === undefined) {
    throw new Error("useAuth 必须在 AuthProvider 内部使用");
  }
  return context;
}
