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
  /** 需要邮箱登录的应用使用的邮箱后缀（不含 @） */
  default_email_domain: string;
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

/** 管理端下发的 Docker 镜像加速源 */
export interface RemoteDockerMirror {
  name: string;
  url: string;
  priority: number;
  region: string | null;
  description: string | null;
  doc_url: string | null;
  last_check_ok: boolean | null;
  last_latency_ms: number | null;
  last_checked_at: string | null;
}

/** 从管理端获取镜像加速源的结果 */
export interface RemoteDockerMirrors {
  /** 是否配置了管理端地址 */
  configured: boolean;
  mirrors: RemoteDockerMirror[];
}

/** 从配套管理端获取启用中的 Docker 镜像加速地址 */
export function listRemoteDockerMirrors(): Promise<RemoteDockerMirrors> {
  return api
    .get("/settings/docker-mirrors/remote")
    .then((r) => r.data as RemoteDockerMirrors);
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
export function getPortSuggestions(count: number): Promise<number[]> {
  return api
    .get("/settings/port-suggestions", { params: { count } })
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
}

/** 单个域名的优选结果 */
export interface HostsOptimizerDomainResult {
  status: string;
  ips: { ip: string; latency_ms: number }[];
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

/** 查询 Hosts 优选运行状态 */
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

/** 立即执行一次 Hosts 优选 */
export function runHostsOptimizer(): Promise<HostsOptimizerRun> {
  return api
    .post("/settings/hosts-optimizer/run", {}, { timeout: 200000 })
    .then((r) => r.data as HostsOptimizerRun);
}
