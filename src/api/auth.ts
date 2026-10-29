/**
 * 认证相关 API
 */

import api from "@/lib/api";

/** 用户信息 */
export interface User {
  id: number;
  username: string;
  role: string;
}

/** 登录响应 */
export interface LoginResponse {
  access_token: string;
  token_type: string;
}

/** 登录 */
export function login(username: string, password: string) {
  return api
    .post<LoginResponse>("/auth/login", { username, password })
    .then((r) => r.data);
}

/** 注册（默认赋予 admin 角色） */
export function register(username: string, password: string) {
  return api
    .post<User>("/auth/register", { username, password })
    .then((r) => r.data);
}

/** 获取当前登录用户信息 */
export function getMe() {
  return api.get<User>("/auth/me").then((r) => r.data);
}

/** 检查系统中是否已有用户（无用户时进入注册模式） */
export function hasUsers() {
  return api
    .get<{ has_users: boolean }>("/auth/has-users")
    .then((r) => r.data.has_users);
}
