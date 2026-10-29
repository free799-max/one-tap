/**
 * API 层共享类型
 */

/** API 错误对象（由 api.ts 响应拦截器附加 displayMessage） */
export interface ApiError {
  displayMessage?: string;
}
