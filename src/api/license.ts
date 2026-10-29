import api from "@/lib/api";

export interface LicenseStatus {
  activated: boolean;
  valid: boolean;
  status: string;
  /** 是否为永久授权 */
  perpetual: boolean;
  license_id: string | null;
  activation_id: string | null;
  /** 授权码，仅管理员接口返回 */
  license_key: string | null;
  device_name: string | null;
  expires_at: string | null;
  last_refreshed_at: string | null;
  last_error: string | null;
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
