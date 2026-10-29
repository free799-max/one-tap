/**
 * 应用认证配置通用工具函数
 *
 * 用于导入弹窗和配置面板中判断当前认证信息是否足够发起检测。
 */

export interface AuthConfigLike {
  url?: string | null;
  auth_type?: "none" | "basic" | "api_key" | "apikey" | string | null;
  username?: string | null;
  password?: string | null;
  api_key?: string | null;
}

/**
 * 判断给定的认证配置是否已经填写到可以发起检测的程度。
 */
export function isAuthConfigReady(config: AuthConfigLike): boolean {
  const url = config.url?.trim() ?? "";
  if (!url.startsWith("http://") && !url.startsWith("https://")) {
    return false;
  }

  const authType = config.auth_type ?? "none";

  if (authType === "basic") {
    return Boolean(config.username?.trim() && config.password);
  }

  if (authType === "api_key" || authType === "apikey") {
    return Boolean(config.api_key?.trim());
  }

  return true;
}
