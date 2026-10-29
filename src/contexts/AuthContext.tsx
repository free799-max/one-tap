/**
 * 全局认证上下文
 *
 * 提供全局登录状态管理：
 * - user: 当前登录用户信息
 * - isLoading: 初始化认证状态中
 * - isAuthenticated: 是否已登录
 * - login(token, remember): 登录后设置状态，remember 决定 token 持久化位置
 * - logout(): 登出清除状态并跳转
 */

import {
  createContext,
  useState,
  useEffect,
  useCallback,
  useMemo,
} from "react";
import { getMe, type User } from "@/api/auth";
import { clearToken, getToken, setToken } from "@/lib/api";

/** AuthContext 提供的值 */
interface AuthContextValue {
  user: User | null;
  isLoading: boolean;
  isAuthenticated: boolean;
  login: (token: string, remember?: boolean) => Promise<void>;
  logout: () => void;
}

// eslint-disable-next-line react-refresh/only-export-components
export const AuthContext = createContext<AuthContextValue | undefined>(
  undefined
);

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [isLoading, setIsLoading] = useState(true);

  /**
   * 初始化时验证本地 token 是否有效
   * 若存在 token（localStorage 或 sessionStorage），调用 /auth/me 验证
   */
  useEffect(() => {
    const token = getToken();
    if (!token) {
      // 使用 microtask 避免在 effect 中同步 setState
      queueMicrotask(() => setIsLoading(false));
      return;
    }

    getMe()
      .then((user) => setUser(user))
      .catch(() => {
        // token 无效或过期，清除本地存储
        // 注意：仅当本地 token 未变更时才清除，避免竞态中误删新登录的 token
        if (getToken() === token) {
          clearToken();
        }
      })
      .finally(() => setIsLoading(false));
  }, []);

  /**
   * 登录成功后调用，存储 token 并获取用户信息
   * remember 为 true 时持久保存（localStorage），否则仅本次会话（sessionStorage）
   */
  const login = useCallback(async (token: string, remember: boolean = false) => {
    setToken(token, remember);
    const user = await getMe();
    setUser(user);
  }, []);

  /**
   * 登出：清除本地 token 和用户状态，跳转到登录页
   */
  const logout = useCallback(() => {
    clearToken();
    setUser(null);
    window.location.href = "/login";
  }, []);

  const value = useMemo(
    () => ({
      user,
      isLoading,
      isAuthenticated: !!user,
      login,
      logout,
    }),
    [user, isLoading, login, logout]
  );

  return (
    <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
  );
}
