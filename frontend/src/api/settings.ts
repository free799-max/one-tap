/**
 * 系统设置相关 API
 */

import api from "@/lib/api";

/** 系统全局配置（前端使用空字符串代替 null） */
export interface SystemConfig {
  id: number;
  /** 是否启用代理 */
  proxy_enabled: boolean;
  http_proxy: string;
  https_proxy: string;
  service_port_start: number;
  default_admin_username: string;
  default_admin_password: string;
  default_admin_password_set: boolean;
  /** 需要邮箱登录的应用使用的邮箱后缀（不含 @） */
  default_email_domain: string;
  /** 是否开启自动更新（只在夜间时段运行） */
  auto_update_enabled: boolean;
  /** 自动更新夜间时段起始小时（0-23） */
  auto_update_window_start: number;
  /** 自动更新夜间时段结束小时（0-23） */
  auto_update_window_end: number;
  /** 自动升级前是否备份应用数据目录 */
  auto_backup_enabled: boolean;
  /** 自动备份保留份数 */
  auto_backup_keep: number;
  /** 允许自动更新的服务标识（group:<id> / app:<id>） */
  auto_update_targets: string[];
  /** 使用邮箱作为登录账号的应用标识 */
  email_username_apps: string[];
  /** Docker 镜像加速地址列表，顺序即优先级 */
  docker_registry_mirrors: string[];
}

/** 系统配置更新请求 */
export interface SystemConfigUpdate {
  proxy_enabled?: boolean;
  http_proxy?: string | null;
  https_proxy?: string | null;
  service_port_start?: number;
  default_admin_username?: string;
  default_admin_password?: string;
  default_email_domain?: string | null;
  auto_update_enabled?: boolean;
  auto_update_window_start?: number;
  auto_update_window_end?: number;
  auto_backup_enabled?: boolean;
  auto_backup_keep?: number;
  auto_update_targets?: string[];
  docker_registry_mirrors?: string[];
}

/** 查询系统全局配置（后端字段可能为 null，统一转为空字符串） */
export function getSystemConfig(): Promise<SystemConfig> {
  return api.get("/settings").then((r) => {
    const data = r.data;
    return {
      ...data,
      proxy_enabled: data.proxy_enabled ?? false,
      http_proxy: data.http_proxy ?? "",
      https_proxy: data.https_proxy ?? "",
      service_port_start: data.service_port_start ?? 10000,
      default_admin_username: data.default_admin_username ?? "admin",
      default_admin_password: data.default_admin_password ?? "",
      default_email_domain: data.default_email_domain ?? "",
      auto_update_enabled: data.auto_update_enabled ?? false,
      auto_update_window_start: data.auto_update_window_start ?? 2,
      auto_update_window_end: data.auto_update_window_end ?? 6,
      auto_backup_enabled: data.auto_backup_enabled ?? true,
      auto_backup_keep: data.auto_backup_keep ?? 3,
      auto_update_targets: data.auto_update_targets ?? [],
      email_username_apps: data.email_username_apps ?? [],
      docker_registry_mirrors: data.docker_registry_mirrors ?? [],
    };
  });
}

/** 更新系统全局配置 */
export function updateSystemConfig(
  data: SystemConfigUpdate
): Promise<SystemConfig> {
  return api.put("/settings", data).then((r) => r.data as SystemConfig);
}

/** 可配置自动更新的服务 */
export interface AutoUpdateTarget {
  /** group:<id> / app:<id> */
  key: string;
  type: "group" | "app";
  id: number;
  /** 实例名 */
  name: string;
  display_name: string;
  /** 应用或方案图标 */
  icon: string | null;
  /** 图标静态资源回退使用的应用标识 */
  icon_name: string;
  status: string;
  current_version: string | null;
  target_version: string | null;
  upgrade_available: boolean;
  app_count: number;
  apps: string[];
  /** 组合部署或多容器应用，升级只替换主服务镜像 */
  complex: boolean;
  selectable: boolean;
  /** 不可选或需要谨慎的原因 */
  reason: string | null;
}

/** 自动更新服务候选列表 */
export interface AutoUpdateTargets {
  targets: AutoUpdateTarget[];
}

/** 获取可勾选自动更新的服务 */
export function listAutoUpdateTargets(): Promise<AutoUpdateTargets> {
  return api
    .get("/settings/auto-update/targets")
    .then((r) => r.data as AutoUpdateTargets);
}

/** 管理端下发的 Docker 镜像加速源 */
export interface RemoteDockerMirror {
  name: string;
  url: string;
  priority: number;
  /** free 免费线路 / subscription 授权专属域名 */
  tier: string;
  /** 上游源站：docker.io、ghcr.io 等 */
  source: string;
  auth_type: string;
  region: string | null;
  description: string | null;
  doc_url: string | null;
  last_check_ok: boolean | null;
  last_latency_ms: number | null;
  last_checked_at: string | null;
  /** 专属域名的到期时间 */
  expires_at: string | null;
}

/** 从管理端获取镜像加速源的结果 */
export interface RemoteDockerMirrors {
  /** 是否配置了管理端地址 */
  configured: boolean;
  /** 本机是否已激活授权并拿到专属域名 */
  licensed: boolean;
  mirrors: RemoteDockerMirror[];
}

/** 从配套管理端获取启用中的 Docker 镜像加速地址 */
export function listRemoteDockerMirrors(): Promise<RemoteDockerMirrors> {
  return api
    .get("/settings/docker-mirrors/remote")
    .then((r) => r.data as RemoteDockerMirrors);
}

/** 授权专属镜像源的实际可用状态 */
export interface DockerMirrorEffectiveness {
  effective: boolean;
  available_count: number;
  total_count: number;
  message: string;
}

/** 实际探测授权专属镜像源是否可用 */
export function getDockerMirrorEffectiveness(): Promise<DockerMirrorEffectiveness> {
  return api
    .get("/settings/docker-mirrors/effectiveness")
    .then((r) => r.data as DockerMirrorEffectiveness);
}

/** 单个镜像加速地址的检测结果 */
export interface DockerMirrorTestItem {
  url: string;
  ok: boolean;
  status_code: number | null;
  latency_ms: number;
  error: string | null;
}

/** 一次镜像加速地址检测的结果 */
export interface DockerMirrorTestResult {
  ok_count: number;
  total: number;
  results: DockerMirrorTestItem[];
}

/** 并发检测镜像加速地址的连通性 */
export function testDockerMirrors(
  urls: string[]
): Promise<DockerMirrorTestResult> {
  return api
    .post("/settings/docker-mirrors/test", { urls }, { timeout: 30000 })
    .then((r) => r.data as DockerMirrorTestResult);
}

/** 单个站点的代理检测结果 */
export interface ProxyTestSite {
  name: string;
  url: string;
  ok: boolean;
  status_code: number | null;
  latency_ms: number | null;
  error: string | null;
}

/** 一次代理检测的结果 */
export interface ProxyTestResult {
  /** 实际使用的代理，直接检测时为 null */
  proxy: string | null;
  ok_count: number;
  total: number;
  results: ProxyTestSite[];
}

/**
 * 检测代理对常用境外站点的连通性
 *
 * 字段为 null 时后端使用已保存配置；传空字符串表示不走代理直接检测。
 */
export function testProxy(data: {
  http_proxy?: string | null;
  https_proxy?: string | null;
}): Promise<ProxyTestResult> {
  return api
    .post("/settings/proxy/test", data, { timeout: 30000 })
    .then((r) => r.data as ProxyTestResult);
}

/** 存储目录：宿主机路径 ↔ 容器内路径的对应关系 */
export interface StorageDirectory {
  id: number;
  name: string;
  /** 宿主机路径 */
  host_path: string;
  /** 容器内路径 */
  mount_path: string;
  remark: string | null;
  enabled: boolean;
  /** 是否为应用数据目录（全表至多一条，存放应用配置等持久化小数据） */
  is_app_data: boolean;
  sort_order: number;
  /** mount_path 在后端容器内是否可见 */
  visible: boolean;
  created_at: string;
  updated_at: string;
}

/** 新建存储目录请求 */
export interface StorageDirectoryCreate {
  name: string;
  host_path: string;
  mount_path: string;
  remark?: string | null;
  enabled?: boolean;
  /** 设为应用数据目录；保存为 true 时后端会自动清除其他记录的该标记 */
  is_app_data?: boolean;
  sort_order?: number;
}

/** 更新存储目录请求（部分字段） */
export type StorageDirectoryUpdate = Partial<StorageDirectoryCreate>;

/** 查询存储目录列表 */
export function listStorageDirectories(): Promise<StorageDirectory[]> {
  return api.get("/settings/storage-directories").then((r) => r.data);
}

/** 新建存储目录 */
export function createStorageDirectory(
  data: StorageDirectoryCreate
): Promise<StorageDirectory> {
  return api.post("/settings/storage-directories", data).then((r) => r.data);
}

/** 更新存储目录 */
export function updateStorageDirectory(
  id: number,
  data: StorageDirectoryUpdate
): Promise<StorageDirectory> {
  return api
    .put(`/settings/storage-directories/${id}`, data)
    .then((r) => r.data);
}

/** 删除存储目录 */
export function deleteStorageDirectory(id: number): Promise<null> {
  return api.delete(`/settings/storage-directories/${id}`).then((r) => r.data);
}

/** 获取服务端口分配建议（从起始端口顺序分配的空闲端口） */
export interface PortSuggestionOptions {
  /** 优先复用且确认空闲的端口 */
  preferred?: number[];
  /** 忽略该容器已映射的端口（用于重建同一服务） */
  excludeContainer?: string;
}

export function getPortSuggestions(
  count: number,
  options?: PortSuggestionOptions
): Promise<number[]> {
  const params: Record<string, string | number> = { count };
  if (options?.preferred?.length) {
    params.preferred = options.preferred.join(",");
  }
  if (options?.excludeContainer) {
    params.exclude_container = options.excludeContainer;
  }
  return api
    .get("/settings/port-suggestions", { params })
    .then((r) => r.data.ports as number[]);
}

/** Hosts 优选配置 */
export interface HostsOptimizerConfig {
  enabled: boolean;
  mode: "manual" | "auto";
  interval_minutes: number;
  top_n: number;
  domains: string[];
  adguard_url: string | null;
  adguard_username: string;
  adguard_password: string;
  adguard_password_set?: boolean;
  /** 以下字段由后端维护，只读 */
  adguard_managed?: boolean;
  adguard_dns_ip?: string | null;
  manage_configured?: boolean;
  managed_rules?: unknown[];
  managed_rules_synced_at?: string | null;
}

/** 单个域名的优选结果；只有数量和最优延迟，不含具体 IP */
export interface HostsOptimizerDomainResult {
  status: string;
  source?: string;
  ip_count?: number;
  best_latency_ms?: number | null;
}

/** 一次优选运行结果 */
export interface HostsOptimizerRun {
  mode: string;
  started_at: string;
  finished_at: string | null;
  duration_ms: number;
  adguard: string;
  errors: string[];
  domains: Record<string, HostsOptimizerDomainResult>;
}

/** Hosts 优选运行状态 */
export interface HostsOptimizerStatus {
  running: boolean;
  next_run_at: string | null;
  last_run: HostsOptimizerRun | null;
}

/** AdGuard 连接测试结果 */
export interface HostsOptimizerTestResult {
  ok: boolean;
  version: string | null;
  message: string;
}

/** 查询 Hosts 优选配置 */
export function getHostsOptimizerConfig(): Promise<HostsOptimizerConfig> {
  return api
    .get("/settings/hosts-optimizer/config")
    .then((r) => r.data as HostsOptimizerConfig);
}

/** 保存 Hosts 优选配置（整份提交） */
export function updateHostsOptimizerConfig(
  data: HostsOptimizerConfig
): Promise<HostsOptimizerConfig> {
  return api
    .put("/settings/hosts-optimizer/config", data)
    .then((r) => r.data as HostsOptimizerConfig);
}

/** 查询 Hosts 优选运行状态；结果不含具体 IP */
export function getHostsOptimizerStatus(): Promise<HostsOptimizerStatus> {
  return api
    .get("/settings/hosts-optimizer/status")
    .then((r) => r.data as HostsOptimizerStatus);
}

/** 测试 AdGuard 连接；字段为空时后端使用已保存配置 */
export function testHostsOptimizer(data: {
  adguard_url?: string | null;
  adguard_username?: string;
  adguard_password?: string;
}): Promise<HostsOptimizerTestResult> {
  return api
    .post("/settings/hosts-optimizer/test", data, { timeout: 20000 })
    .then((r) => r.data as HostsOptimizerTestResult);
}

/** 立即执行一次 Hosts 优选；返回结果不含具体 IP */
export function runHostsOptimizer(): Promise<HostsOptimizerRun> {
  return api
    .post("/settings/hosts-optimizer/run", {}, { timeout: 200000 })
    .then((r) => r.data as HostsOptimizerRun);
}

/** 管理端下发的加速域名规则 */
export interface ManagedDnsRule {
  domain: string;
  answers: string[];
  priority: number;
  description: string | null;
  /** free 免费线路 / subscription 专属加速 */
  tier?: string;
}

/** 管理端加速规则状态 */
export interface ManagedDnsRules {
  configured: boolean;
  rules: ManagedDnsRule[];
  synced_at: string | null;
  error: string | null;
}

/** 可配置 DNS 加速的应用实例 */
export interface AccelerationApp {
  instance_id: number;
  instance_name: string;
  display_name: string;
  app_name: string | null;
  icon: string | null;
  project_id: number | null;
  project_name: string | null;
  orchestration_group_id: number | null;
  orchestration_group_name: string | null;
  orchestration_name: string | null;
  status: string;
  services: string[];
  acceleration_enabled: boolean;
  dns: string[];
  can_enable: boolean;
  reason: string | null;
}

/** AdGuard Home 自动部署状态 */
export interface AccelerationAdguard {
  configured: boolean;
  managed: boolean;
  running: boolean;
  url: string | null;
  web_url: string | null;
  dns_ip: string | null;
  setup_port: number | null;
  web_port: number | null;
  version: string | null;
  network: string;
  fallback_dns?: string[];
  message: string;
}

/** 应用加速聚合状态 */
export interface AccelerationOverview {
  adguard: AccelerationAdguard;
  managed_rules: ManagedDnsRules;
  apps: AccelerationApp[];
  network_effective: boolean | null;
  network_rule_count: number;
  network_applied_rule_count: number;
  network_app_count: number;
  network_message: string;
}

/** 应用加速开关结果 */
export interface AccelerationToggleResult {
  instance_id: number;
  enabled: boolean;
  dns_ip: string | null;
  services: string[];
  warnings: string[];
  message: string;
}

/** 自动部署 AdGuard Home 的任务响应 */
export interface AdguardDeployResult {
  task_id: string;
  instance_name: string | null;
  status: string;
}

/** 删除 OneTap 托管 AdGuard Home 的结果 */
export interface AdguardRemoveResult {
  removed: boolean;
  disabled_apps: string[];
  warnings: string[];
  message: string;
}

/** 查询应用加速、AdGuard 与管理端规则状态 */
export function getHostsAccelerationOverview(): Promise<AccelerationOverview> {
  return api
    .get("/settings/hosts-optimizer/acceleration")
    .then((r) => r.data as AccelerationOverview);
}

/** 开启或关闭单个应用的 DNS 加速 */
export function toggleAppAcceleration(
  instanceId: number,
  enabled: boolean
): Promise<AccelerationToggleResult> {
  return api
    .post(
      `/settings/hosts-optimizer/acceleration/apps/${instanceId}`,
      { enabled },
      { timeout: 320000 }
    )
    .then((r) => r.data as AccelerationToggleResult);
}

/** 应用市场安装弹窗提交的 AdGuard 部署参数 */
export interface AdguardDeployRequest {
  instance_name?: string;
  config?: Record<string, unknown>;
}

/** 自动部署 OneTap 专用 AdGuard Home */
/** 自动部署 OneTap 专用 AdGuard Home（立即返回部署任务 ID） */
export function deployHostsAdguard(
  data?: AdguardDeployRequest
): Promise<AdguardDeployResult> {
  return api
    .post(
      "/settings/hosts-optimizer/acceleration/adguard/deploy",
      data ?? {},
      { timeout: 30000 }
    )
    .then((r) => r.data as AdguardDeployResult);
}

/** 关闭所有应用加速并删除 OneTap 托管的 AdGuard Home */
export function removeHostsAdguard(): Promise<AdguardRemoveResult> {
  return api
    .delete("/settings/hosts-optimizer/acceleration/adguard", {
      timeout: 320000,
    })
    .then((r) => r.data as AdguardRemoveResult);
}

/** 从管理端同步加速域名规则 */
export function syncHostsManagedRules(): Promise<ManagedDnsRules> {
  return api
    .post("/settings/hosts-optimizer/managed-rules/sync", {}, { timeout: 30000 })
    .then((r) => r.data as ManagedDnsRules);
}
