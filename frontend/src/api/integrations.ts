/**
 * 应用集成 API
 *
 * 认证集成（Vaultwarden）与网络集成（Lucky 或
 * Nginx Proxy Manager + DDNS-Go）的配置、测试、应用凭据同步、
 * 反向代理与 DDNS 管理。
 */

import api from "@/lib/api";

export type IntegrationKind = "vaultwarden" | "network";
export type NetworkProvider = "lucky" | "npm";
export type TestableProvider = NetworkProvider | "ddnsgo";

export interface IntegrationStatus {
  ok: boolean | null;
  message: string;
  version: string | null;
  checked_at: string | null;
  last_synced_at: string | null;
}

export interface IntegrationSummary {
  kind: IntegrationKind;
  enabled: boolean;
  configured: boolean;
  status: IntegrationStatus;
}

export interface IntegrationTestResult {
  ok: boolean;
  message: string;
  version: string | null;
}

export interface IntegrationApp {
  instance_id: number;
  app_name: string;
  display_name: string;
  instance_name: string;
  status: string;
  url: string | null;
  icon: string | null;
  username: string | null;
  password_set: boolean;
  api_key_set: boolean;
  has_credentials: boolean;
}

export interface VaultwardenConfig {
  kind: "vaultwarden";
  enabled: boolean;
  configured: boolean;
  managed: boolean;
  running: boolean;
  url: string;
  web_url: string | null;
  web_port: number | null;
  email: string;
  password_set: boolean;
  synced_count: number;
  status: IntegrationStatus;
}

export interface VaultwardenDeployRequest {
  email?: string | null;
  password?: string | null;
  web_port?: number | null;
  config?: Record<string, unknown> | null;
}

export interface VaultwardenDeployTask {
  task_id: string;
  instance_name: string;
  deploying: boolean;
}

export interface VaultwardenRemoveResult {
  removed: boolean;
  message: string;
}

export interface VaultwardenUpdate {
  enabled: boolean;
  url: string;
  email: string;
  password?: string | null;
}

export interface VaultwardenApp extends IntegrationApp {
  synced: boolean;
}

export interface VaultwardenSyncItem {
  instance_id: number;
  display_name: string;
  ok: boolean;
  message: string;
}

export interface VaultwardenSyncResult {
  ok: boolean;
  message: string;
  items: VaultwardenSyncItem[];
}

export interface NetworkDdnsFamily {
  enabled: boolean;
  provider: string;
  access_id: string;
  secret_set: boolean;
  domains: string[];
  manual_domains: string[];
  auto_domains: string[];
  interval: number;
  get_type: "url" | "netInterface";
  url: string;
  net_interface: string;
  ip_reg: string;
}

export interface NetworkDdns {
  enabled: boolean;
  ipv4: NetworkDdnsFamily;
  ipv6: NetworkDdnsFamily;
}

export interface LuckyConnection {
  url: string;
  username: string;
  password_set: boolean;
  listen_port: number;
  enable_tls: boolean;
  managed: boolean;
  running: boolean;
  external: boolean;
  external_name: string | null;
  web_port: number | null;
  web_url: string | null;
}

export interface NetworkLuckyDeployRequest {
  username?: string | null;
  password?: string | null;
  web_port?: number | null;
  config?: Record<string, unknown> | null;
}

export interface NetworkLuckyDeployTask {
  task_id: string;
  instance_name: string;
  deploying: boolean;
}

export interface NetworkLuckyRemoveResult {
  removed: boolean;
  message: string;
}

export interface NpmConnection {
  url: string;
  email: string;
  password_set: boolean;
  certificate_id: number | "new";
  ssl_forced: boolean;
}

export interface DdnsGoConnection {
  url: string;
  username: string;
  password_set: boolean;
}

export interface NetworkConfig {
  kind: "network";
  enabled: boolean;
  provider: NetworkProvider;
  configured: boolean;
  target_host: string;
  lucky: LuckyConnection;
  npm: NpmConnection;
  ddnsgo: DdnsGoConnection;
  status: IntegrationStatus;
  ddns: NetworkDdns;
}

export interface NetworkUpdate {
  enabled: boolean;
  provider: NetworkProvider;
  target_host: string;
  lucky: {
    url: string;
    username: string;
    password?: string | null;
    listen_port: number;
    enable_tls: boolean;
  };
  npm: {
    url: string;
    email: string;
    password?: string | null;
    certificate_id: number | "new";
    ssl_forced: boolean;
  };
  ddnsgo: {
    url: string;
    username: string;
    password?: string | null;
  };
}

export interface NetworkHostCandidate {
  value: string;
  label: string;
  kind: "current" | "lan";
}

export interface NetworkHosts {
  current: string;
  candidates: NetworkHostCandidate[];
}

export interface NetworkApp extends IntegrationApp {
  domain: string | null;
  domains: string[];
  target_url: string | null;
  proxy_enabled: boolean;
  is_custom: boolean;
  listen_port: number | null;
  enable_tls: boolean;
  network: "tcp" | "tcp4" | "tcp6";
  certificate_id: number;
  ssl_forced: boolean;
}

export interface NetworkProxyResult {
  ok: boolean;
  message: string;
}

export interface NetworkDdnsTask {
  task_key: string;
  task_name: string;
  task_type: string;
  domains: string[];
  enabled: boolean;
  status: "ok" | "unchanged" | "error" | null;
  message: string;
  address: string;
  last_sync: string | null;
  next_sync: string | null;
}

export interface NetworkDdnsState extends NetworkDdns {
  tasks: NetworkDdnsTask[];
  message: string;
}

export interface NetworkDdnsInterface {
  name: string;
  hardware_addr: string;
  addresses: string[];
}

export interface NetworkDdnsInterfaces {
  ipv4: NetworkDdnsInterface[];
  ipv6: NetworkDdnsInterface[];
}

export interface NetworkDdnsUpdateFamily {
  enabled: boolean;
  provider: string;
  access_id: string;
  secret?: string | null;
  domains: string[];
  interval: number;
  get_type: "url" | "netInterface";
  url: string;
  net_interface: string;
  ip_reg: string;
}

export interface NetworkDdnsUpdate {
  enabled: boolean;
  ipv4: NetworkDdnsUpdateFamily;
  ipv6: NetworkDdnsUpdateFamily;
}

export interface NpmCertificate {
  id: number;
  nice_name: string;
  domain_names: string[];
  expires_on: string | null;
}

export function listIntegrations(): Promise<IntegrationSummary[]> {
  return api.get("/integrations").then((r) => r.data as IntegrationSummary[]);
}

// ---------- Vaultwarden ----------

export function getVaultwardenConfig(): Promise<VaultwardenConfig> {
  return api
    .get("/integrations/vaultwarden")
    .then((r) => r.data as VaultwardenConfig);
}

export function updateVaultwardenConfig(
  data: VaultwardenUpdate
): Promise<VaultwardenConfig> {
  return api
    .put("/integrations/vaultwarden", data)
    .then((r) => r.data as VaultwardenConfig);
}

export function deployVaultwarden(
  data: VaultwardenDeployRequest
): Promise<VaultwardenDeployTask> {
  return api
    .post("/integrations/vaultwarden/deploy", data)
    .then((r) => r.data as VaultwardenDeployTask);
}

export function removeVaultwarden(): Promise<VaultwardenRemoveResult> {
  return api
    .delete("/integrations/vaultwarden/managed", { timeout: 120000 })
    .then((r) => r.data as VaultwardenRemoveResult);
}

export function testVaultwarden(data: {
  url?: string | null;
  email?: string | null;
  password?: string | null;
}): Promise<IntegrationTestResult> {
  return api
    .post("/integrations/vaultwarden/test", data, { timeout: 60000 })
    .then((r) => r.data as IntegrationTestResult);
}

export function listVaultwardenApps(): Promise<VaultwardenApp[]> {
  return api
    .get("/integrations/vaultwarden/apps")
    .then((r) => r.data as VaultwardenApp[]);
}

export function syncVaultwarden(
  instanceIds: number[] | null
): Promise<VaultwardenSyncResult> {
  return api
    .post(
      "/integrations/vaultwarden/sync",
      { instance_ids: instanceIds },
      { timeout: 120000 }
    )
    .then((r) => r.data as VaultwardenSyncResult);
}

// ---------- 网络集成 ----------

export function getNetworkConfig(): Promise<NetworkConfig> {
  return api.get("/integrations/network").then((r) => r.data as NetworkConfig);
}

export function listNetworkHosts(): Promise<NetworkHosts> {
  return api
    .get("/integrations/network/hosts")
    .then((r) => r.data as NetworkHosts);
}

export function updateNetworkConfig(data: NetworkUpdate): Promise<NetworkConfig> {
  return api
    .put("/integrations/network", data)
    .then((r) => r.data as NetworkConfig);
}

export function deployNetworkLucky(
  data: NetworkLuckyDeployRequest
): Promise<NetworkLuckyDeployTask> {
  return api
    .post("/integrations/network/lucky/deploy", data)
    .then((r) => r.data as NetworkLuckyDeployTask);
}

export function removeNetworkLucky(): Promise<NetworkLuckyRemoveResult> {
  return api
    .delete("/integrations/network/lucky/instance", { timeout: 120000 })
    .then((r) => r.data as NetworkLuckyRemoveResult);
}

export function testNetwork(data: {
  provider: TestableProvider;
  url?: string | null;
  username?: string | null;
  email?: string | null;
  password?: string | null;
}): Promise<IntegrationTestResult> {
  return api
    .post("/integrations/network/test", data, { timeout: 60000 })
    .then((r) => r.data as IntegrationTestResult);
}

export function listNetworkApps(): Promise<NetworkApp[]> {
  return api
    .get("/integrations/network/apps")
    .then((r) => r.data as NetworkApp[]);
}

export function upsertNetworkProxy(data: {
  app_instance_id: number;
  custom_name?: string | null;
  domains: string[];
  target_url?: string | null;
  listen_port?: number | null;
  enable_tls?: boolean;
  network?: "tcp" | "tcp4" | "tcp6";
  certificate_id?: number;
  ssl_forced?: boolean;
}): Promise<NetworkProxyResult> {
  return api
    .put("/integrations/network/proxy", data, { timeout: 60000 })
    .then((r) => r.data as NetworkProxyResult);
}

export function deleteNetworkProxy(
  instanceId: number
): Promise<NetworkProxyResult> {
  return api
    .delete(`/integrations/network/proxy/${instanceId}`, { timeout: 60000 })
    .then((r) => r.data as NetworkProxyResult);
}

export type NetworkSecretProvider = "lucky" | "npm" | "ddnsgo" | "ddns";
export type IntegrationSecretProvider =
  | NetworkSecretProvider
  | "vaultwarden"
  | "adguard"
  | "system"
  | "license";

export interface IntegrationSecretOut {
  password: string;
  reveal_token: string;
  expires_at: string;
}

export function revealIntegrationSecret(data: {
  provider: IntegrationSecretProvider;
  password?: string;
  reveal_token?: string;
}): Promise<IntegrationSecretOut> {
  const { provider, ...credentials } = data;
  const path =
    provider === "vaultwarden"
      ? "/integrations/vaultwarden/secret"
      : provider === "adguard"
        ? "/settings/hosts-optimizer/secret"
        : provider === "system"
          ? "/settings/secret"
          : provider === "license"
            ? "/license/secret"
            : "/integrations/network/secret";
  const payload =
    provider === "vaultwarden" ||
    provider === "adguard" ||
    provider === "system" ||
    provider === "license"
      ? credentials
      : { provider, ...credentials };
  return api
    .post(path, payload)
    .then((r) => r.data as IntegrationSecretOut);
}

export function listNpmCertificates(): Promise<NpmCertificate[]> {
  return api
    .get("/integrations/network/npm/certificates", { timeout: 60000 })
    .then((r) => r.data as NpmCertificate[]);
}

export interface NetworkCertificate {
  id: string;
  name: string;
  domains: string[];
  expires_on: string | null;
  status: "valid" | "pending" | "failed";
  error: string | null;
}

export interface NetworkCertificateResult {
  ok: boolean;
  message: string;
  certificate: NetworkCertificate | null;
}

export function listNetworkCertificates(): Promise<NetworkCertificate[]> {
  return api
    .get("/integrations/network/certificates", { timeout: 60000 })
    .then((r) => r.data as NetworkCertificate[]);
}

export function autoProvisionNetworkCertificate(data: {
  domains: string[];
  mode?: "auto" | "selfsigned" | "acme";
}): Promise<NetworkCertificateResult> {
  return api
    .post("/integrations/network/certificates/auto", data, {
      timeout: 120000,
    })
    .then((r) => r.data as NetworkCertificateResult);
}

export function getNetworkDdns(): Promise<NetworkDdnsState> {
  return api
    .get("/integrations/network/ddns")
    .then((r) => r.data as NetworkDdnsState);
}

export function listNetworkDdnsInterfaces(): Promise<NetworkDdnsInterfaces> {
  return api
    .get("/integrations/network/ddns/interfaces", { timeout: 60000 })
    .then((r) => r.data as NetworkDdnsInterfaces);
}

export function saveNetworkDdns(
  data: NetworkDdnsUpdate
): Promise<NetworkDdnsState> {
  return api
    .put("/integrations/network/ddns", data, { timeout: 60000 })
    .then((r) => r.data as NetworkDdnsState);
}

export function deleteNetworkDdns(): Promise<NetworkDdnsState> {
  return api
    .delete("/integrations/network/ddns", { timeout: 60000 })
    .then((r) => r.data as NetworkDdnsState);
}
