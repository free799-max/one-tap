/**
 * Axios 实例封装
 *
 * 功能说明：
 * - 创建以 /api 为基础路径的 Axios 实例
 * - 请求拦截器：自动读取 token 并附加到 Authorization 请求头
 * - 响应拦截器：捕获 401 未授权响应，清除本地 token 并跳转到登录页
 *
 * token 存储策略（配合登录页「记住我」）：
 * - 勾选记住我：写入 localStorage，浏览器重启后仍有效
 * - 未勾选：写入 sessionStorage，关闭浏览器标签页后失效
 */

import axios from "axios";

const TOKEN_KEY = "token";

/** 读取当前 token（优先 localStorage，其次 sessionStorage） */
export function getToken(): string | null {
  return localStorage.getItem(TOKEN_KEY) ?? sessionStorage.getItem(TOKEN_KEY);
}

/** 写入 token；remember 为 true 时持久到 localStorage，否则仅本次会话有效 */
export function setToken(token: string, remember: boolean) {
  if (remember) {
    localStorage.setItem(TOKEN_KEY, token);
    sessionStorage.removeItem(TOKEN_KEY);
  } else {
    sessionStorage.setItem(TOKEN_KEY, token);
    localStorage.removeItem(TOKEN_KEY);
  }
}

/** 清除 token（两个存储位置都清） */
export function clearToken() {
  localStorage.removeItem(TOKEN_KEY);
  sessionStorage.removeItem(TOKEN_KEY);
}

// 创建 Axios 实例，所有请求默认以 /api 为前缀
const api = axios.create({
  baseURL: "/api",
});

// 请求拦截器：在每个请求发出前，读取 token 并附加到请求头
api.interceptors.request.use((config) => {
  const token = getToken();
  if (token) {
    // 若 token 存在，附加 Bearer 认证头
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// 响应拦截器：解包 StandardResponse 并处理错误
api.interceptors.response.use(
  (response) => {
    // 204 No Content 直接透传
    if (response.status === 204) return response;
    // 提取 data 字段（StandardResponse 包装层）
    // 需同时具备 success 标识，避免误拆业务对象自身的顶层 data 字段
    if (
      response.data &&
      typeof response.data === "object" &&
      "success" in response.data &&
      "data" in response.data
    ) {
      response.data = response.data.data;
    }
    return response;
  },
  (error) => {
    if (error.response) {
      const data = error.response.data;
      // 提取后端返回的错误消息
      error.displayMessage = data?.message || "请求失败";

      if (error.response.status === 401) {
        // 排除登录接口本身的 401（让用户界面处理错误提示）
        const isLoginRequest = error.config?.url?.includes("/auth/login");
        if (!isLoginRequest) {
          // 仅当 401 请求携带的 token 与当前本地 token 一致时才清除并跳转，
          // 避免过期 token 的旧请求（如启动时的 /auth/me）在竞态中
          // 误清掉用户刚登录写入的新 token
          const currentToken = getToken();
          const requestAuth = error.config?.headers?.Authorization;
          if (!currentToken || requestAuth === `Bearer ${currentToken}`) {
            clearToken();
            window.location.href = "/login";
          }
        }
      }
    }
    return Promise.reject(error);
  }
);

export default api;
