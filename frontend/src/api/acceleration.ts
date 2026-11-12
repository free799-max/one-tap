/**
 * 网络加速 API
 *
 * 规则与节点由管理端下发，客户端只保存订阅地址并同步到本机 Mihomo。
 */

import api from "@/lib/api";
import type { AccelerationApp } from "@/api/settings";

export interface AccelProxyApp extends AccelerationApp {
  proxy_enabled: boolean;
}

export interface AccelMihomoStatus {
  installed: boolean;
  running: boolean;
  container_name: string;
  version: string;
  mixed_port: number;
  controller: string;
  subscription_name: string;
  applied_revision: number;
  config_updated_at: string | null;
  last_sync_at: string | null;
  last_error: string | null;
  subscription_url: string;
  error: string | null;
}

export interface AccelMoviePilot {
  installed: boolean;
  container: string;
  status: string;
  proxy_env: string;
  expected_proxy: string;
  message: string;
}

export interface AccelRuleItem {
  kind: string;
  value: string;
  app?: string;
}

export interface AccelSyncResult {
  status: "success" | "unchanged" | "error" | string;
  message: string;
  trigger: "auto" | "manual" | string;
  changed: boolean | null;
  rules: number;
  revision: number;
  synced_at: string | null;
}

export interface AccelManaged {
  configured: boolean;
  subscription_url: string;
  rules: AccelRuleItem[];
  rules_synced_at: string | null;
  last_sync_at: string | null;
  last_sync_error: string | null;
  auto_sync_enabled: boolean;
  auto_sync_interval_minutes: number;
  last_sync_result: AccelSyncResult | null;
  mihomo: AccelMihomoStatus;
  moviepilot: AccelMoviePilot;
}

export interface AccelActionResult {
  ok: boolean;
  message: string;
  detail: Record<string, unknown>;
}

const BASE = "/acceleration";

export function getAccelManaged(): Promise<AccelManaged> {
  return api.get(`${BASE}/managed`).then((r) => r.data as AccelManaged);
}

export function syncAccelManaged(): Promise<AccelActionResult> {
  return api
    .post(`${BASE}/managed/sync`, {}, { timeout: 120000 })
    .then((r) => r.data as AccelActionResult);
}

export function updateAccelSyncSettings(payload: {
  enabled: boolean;
  interval_minutes: number;
}): Promise<AccelActionResult> {
  return api
    .put(`${BASE}/managed/sync-settings`, payload)
    .then((r) => r.data as AccelActionResult);
}

export interface AccelDeployTask {
  task_id: string;
  instance_name: string;
}

export function deployAccelMihomo(payload?: {
  config?: Record<string, unknown>;
}): Promise<AccelDeployTask> {
  return api
    .post(`${BASE}/mihomo/deploy`, payload ?? {}, { timeout: 30000 })
    .then((r) => r.data as AccelDeployTask);
}

export function removeAccelMihomo(): Promise<AccelActionResult> {
  return api
    .delete(`${BASE}/mihomo`, { timeout: 320000 })
    .then((r) => r.data as AccelActionResult);
}

export function listAccelApps(): Promise<AccelProxyApp[]> {
  return api.get(`${BASE}/apps`).then((r) => r.data as AccelProxyApp[]);
}

export function setAccelAppProxy(
  instanceId: number,
  enabled: boolean
): Promise<AccelActionResult> {
  return api
    .post(`${BASE}/apps/${instanceId}`, { enabled }, { timeout: 180000 })
    .then((r) => r.data as AccelActionResult);
}
