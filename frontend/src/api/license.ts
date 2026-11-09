import api from "@/lib/api";

export interface LicenseStatus {
  activated: boolean;
  valid: boolean;
  status: string;
  /** free 免费版；licensed 授权版 */
  edition: "free" | "licensed";
  /** 是否为永久授权（历史字段，当前授权均有期限） */
  perpetual: boolean;
  license_id: string | null;
  activation_id: string | null;
  /** 授权码不再随状态接口返回，仅记录是否已保存 */
  license_key: string | null;
  license_key_set: boolean;
  device_name: string | null;
  expires_at: string | null;
  last_refreshed_at: string | null;
  last_error: string | null;
  /** 专属加速购买页；未配置时为空 */
  purchase_url: string | null;
}

export interface ActivateLicenseRequest {
  license_key: string;
  device_name?: string;
}

export function getLicenseStatus(): Promise<LicenseStatus> {
  return api.get("/license/status").then((r) => r.data);
}

export function activateLicense(
  data: ActivateLicenseRequest
): Promise<LicenseStatus> {
  return api.post("/license/activate", data).then((r) => r.data);
}

export function refreshLicense(): Promise<LicenseStatus> {
  return api.post("/license/refresh").then((r) => r.data);
}

export function deactivateLicense(): Promise<LicenseStatus> {
  return api.post("/license/deactivate").then((r) => r.data);
}
