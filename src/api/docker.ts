/**
 * Docker 相关 API（容器 / 镜像 / 仓库配置 / 宿主机）
 */

import api from "@/lib/api";

/* ===================== 容器 ===================== */

/**
 * 容器信息接口
 *
 * 描述 Docker 容器的基本信息，包括 ID、名称、状态、健康状态和使用的镜像。
 */
export interface ContainerInfo {
  /** 容器唯一标识 */
  id: string;
  /** 容器名称 */
  name: string;
  /** 容器运行状态（如 running、exited 等） */
  status: string;
  /** 状态中文摘要 */
  state: string;
  /** 容器健康状态（如 healthy、unhealthy 等） */
  health: string;
  /** 容器使用的镜像名称 */
  image: string;
  /** 端口映射摘要 */
  ports: string;
  /** 结构化端口映射 */
  port_bindings: ContainerPortBinding[];
  /** 容器标签 */
  labels: Record<string, string>;
  /** 创建时间 */
  created: string;
}

/** 容器端口绑定详情 */
export interface ContainerPortBinding {
  container_port: string;
  host_ip: string;
  host_port: string;
}

/** 容器挂载详情 */
export interface ContainerMount {
  type: string;
  source: string;
  destination: string;
  mode: string;
  rw: boolean;
}

/** 容器网络信息 */
export interface ContainerNetwork {
  name: string;
  ip_address: string;
  gateway: string;
  mac_address: string;
}

/** 容器完整详情 */
export interface ContainerDetail {
  id: string;
  name: string;
  image: string;
  status: string;
  state: string;
  health: string;
  command: string[] | null;
  entrypoint: string[] | null;
  env: string[] | null;
  working_dir: string | null;
  user: string | null;
  labels: Record<string, string> | null;
  ports: ContainerPortBinding[];
  mounts: ContainerMount[];
  networks: ContainerNetwork[];
  restart_policy: string;
  network_mode: string;
  privileged: boolean;
  created: string;
  started_at: string;
  finished_at: string;
  exit_code: number;
  error: string;
}

/** 容器操作响应 */
export interface ContainerActionResponse {
  status: string;
  error: string;
}

/** 创建容器请求 */
export interface ContainerCreateRequest {
  image: string;
  name?: string | null;
  command?: string | null;
  entrypoint?: string | null;
  ports?: { container: string; host: string }[];
  environment?: { key: string; value: string }[];
  volumes?: { host: string; container: string; mode?: "rw" | "ro" }[];
  network?: string | null;
  labels?: { key: string; value: string }[];
  restart_policy?: "no" | "unless-stopped" | "always" | "on-failure";
  auto_start?: boolean;
}

/** 批量容器操作请求 */
export interface ContainerBatchActionRequest {
  ids: string[];
  action: "start" | "stop" | "restart" | "remove";
}

/** 批量容器操作响应 */
export interface ContainerBatchActionResult {
  succeeded: string[];
  failed: { id: string; reason: string }[];
}

/** 容器执行命令请求 */
export interface ContainerExecRequest {
  command: string;
  workdir?: string | null;
  user?: string | null;
  environment?: { key: string; value: string }[];
}

/** 容器执行命令响应 */
export interface ContainerExecResponse {
  exit_code: number;
  output: string;
}

/** 查询 Docker 服务状态 */
export function getDockerStatus() {
  return api.get<{ available: boolean }>("/docker/status").then((r) => r.data);
}

/** 查询容器列表 */
export function listContainers() {
  return api.get<ContainerInfo[]>("/docker/containers").then((r) => r.data);
}

/** 对容器执行操作（start/stop/restart） */
export function containerAction(
  id: string,
  action: "start" | "stop" | "restart"
) {
  return api
    .post<ContainerActionResponse>(`/docker/containers/${id}/action`, { action })
    .then((r) => r.data);
}

/** 创建容器 */
export function createContainer(data: ContainerCreateRequest) {
  return api.post<ContainerInfo>("/docker/containers", data).then((r) => r.data);
}

/** 批量操作容器 */
export function batchContainerAction(data: ContainerBatchActionRequest) {
  return api
    .post<ContainerBatchActionResult>("/docker/containers/batch-action", data)
    .then((r) => r.data);
}

/** 查询容器详情 */
export function getContainerDetail(id: string) {
  return api
    .get<ContainerDetail>(`/docker/containers/${id}/detail`)
    .then((r) => r.data);
}

/** 在容器内执行命令 */
export function containerExec(id: string, data: ContainerExecRequest) {
  return api
    .post<ContainerExecResponse>(`/docker/containers/${id}/exec`, data)
    .then((r) => r.data);
}

/* ===================== 宿主机 ===================== */

/** Docker 引擎版本信息 */
export interface DockerVersionInfo {
  version: string;
  api_version: string;
  go_version: string;
  os: string;
  arch: string;
  kernel_version: string;
  build_time: string;
}

/** 宿主机资源信息 */
export interface ResourceInfo {
  cpu_cores: number;
  cpu_usage_percent: number;
  memory_total: number;
  memory_used: number;
  disk_total: number;
  disk_used: number;
  disk_free: number;
  disk_usage_percent: number;
}

/** Docker 统计信息 */
export interface DockerStatsInfo {
  containers_total: number;
  containers_running: number;
  containers_paused: number;
  containers_stopped: number;
  images: number;
}

/** Docker 网络信息 */
export interface NetworkInfo {
  id: string;
  name: string;
  driver: string;
  scope: string;
}

/** Docker 宿主机综合信息 */
export interface HostInfo {
  hostname: string;
  os: string;
  arch: string;
  kernel_version: string;
  uptime_seconds: number;
  load_average: number[];
  docker_version: DockerVersionInfo;
  resources: ResourceInfo;
  stats: DockerStatsInfo;
  storage_driver: string;
  docker_root_dir: string;
  networks: NetworkInfo[];
}

/** 查询 Docker 宿主机综合信息 */
export function getDockerHostInfo() {
  return api.get<HostInfo>("/docker/host/info").then((r) => r.data);
}

/* ===================== 镜像 ===================== */

/** 批量删除镜像请求 */
export interface BatchImageDeleteRequest {
  ids: string[];
  force?: boolean;
}

/** 批量删除镜像响应 */
export interface BatchDeleteResult {
  deleted: string[];
  failed: { id: string; reason: string }[];
}

/** 移除未使用镜像结果 */
export interface ImagePruneResult {
  deleted: string[];
  space_reclaimed: number;
}

/** 镜像标签信息 */
export interface ImageTag {
  name: string;
  last_updated: string;
  size: number;
  digest: string;
}

/** 本地镜像信息（扁平化，每行对应一个 tag） */
export interface ImageInfo {
  id: string;
  image_id: string;
  name: string;
  tag: string;
  full_tag: string;
  size: number;
  created: string;
  containers: number;
}

/** 镜像层表格数据 */
export interface ImageLayer {
  order: number;
  size: number;
  layer: string;
}

/** 镜像完整元数据 */
export interface ImageDetail {
  id: string;
  name: string;
  tag: string;
  full_tag: string;
  size: number;
  created: string;
  architecture: string;
  os: string;
  cmd: string[] | null;
  entrypoint: string[] | null;
  env: string[] | null;
  exposed_ports: string[] | null;
  volumes: string[] | null;
  working_dir: string | null;
  user: string | null;
  labels: Record<string, string> | null;
  layers: string[] | null;
  history: string[] | null;
  parent: string | null;
  docker_version: string | null;
  build: string | null;
  layers_table: ImageLayer[] | null;
}

/** Docker Hub 搜索结果 */
export interface ImageSearchResult {
  name: string;
  description: string;
  star_count: number;
  pull_count: number;
  official: boolean;
  is_automated: boolean;
}

/** 镜像搜索分页结果 */
export interface ImageSearchPage {
  total: number;
  page: number;
  page_size: number;
  results: ImageSearchResult[];
}

/** 拉取任务响应 */
export interface PullTaskResponse {
  task_id: string;
  image: string;
  status: string;
}

/** 单层拉取进度 */
export interface PullProgressLayer {
  id: string;
  status: string;
  status_text: string;
  current: number;
  total: number;
  progress_text: string;
  percentage: number;
  speed: number;
}

/** 拉取进度事件 */
export interface PullProgressEvent {
  total_layers: number;
  completed_layers: number;
  current_layer: string;
  percentage: number;
  status: string;
  speed: number;
  total_size: number;
  downloaded_size: number;
  size_text: string;
  layers: PullProgressLayer[];
}

/** 拉取任务完整状态 */
export interface PullTaskStatus {
  task_id: string;
  image: string;
  status: "pulling" | "completed" | "failed";
  progress: PullProgressEvent;
  error: string | null;
  created_at: string;
  updated_at: string;
  completed_at: string | null;
}

/** 查询本地镜像列表 */
export function listImages() {
  return api.get<ImageInfo[]>("/docker/images").then((r) => r.data);
}

/** 删除本地镜像 */
export function removeImage(id: string, force?: boolean) {
  return api.delete(`/docker/images/${id}`, { params: { force } });
}

/** 从 Docker Hub 搜索镜像 */
export function searchImages(q: string, page: number = 1) {
  return api
    .get<ImageSearchPage>("/docker/images/search", { params: { q, page } })
    .then((r) => r.data);
}

/** 启动镜像拉取任务（返回 task_id 用于进度跟踪） */
export function pullImage(image: string) {
  return api
    .post<PullTaskResponse>("/docker/images/pull", { image })
    .then((r) => r.data);
}

/** 查询镜像拉取任务状态 */
export function getPullTaskStatus(taskId: string) {
  return api
    .get<PullTaskStatus>(`/docker/images/pull/${taskId}/status`)
    .then((r) => r.data);
}

/** 批量删除本地镜像 */
export function batchRemoveImages(data: BatchImageDeleteRequest) {
  return api
    .post<BatchDeleteResult>("/docker/images/batch-delete", data)
    .then((r) => r.data);
}

/** 查询镜像完整元数据 */
export function getImageDetail(imageId: string) {
  return api
    .get<ImageDetail>(`/docker/images/${imageId}/detail`)
    .then((r) => r.data);
}

/** 查询指定镜像的可用标签列表 */
export function getImageTags(image: string, signal?: AbortSignal) {
  return api
    .get<ImageTag[]>("/docker/images/tags", { params: { image }, signal })
    .then((r) => r.data);
}

/** 移除未使用镜像 */
export function pruneImages() {
  return api.post<ImagePruneResult>("/docker/images/prune").then((r) => r.data);
}

/* ===================== 镜像仓库配置 ===================== */

/** 镜像搜索接口配置 */
export interface Registry {
  id: number;
  name: string;
  search_api_url: string;
  mirror_url: string | null;
  mirror_urls: string[] | null;
  enable_mirror: boolean;
  username: string | null;
  trust_ssl_self_signed: boolean;
  is_default: boolean;
  created_at: string;
  updated_at: string;
}

/** 创建镜像搜索接口配置请求 */
export interface RegistryCreate {
  name: string;
  search_api_url: string;
  mirror_url?: string | null;
  mirror_urls?: string[] | null;
  enable_mirror?: boolean;
  username?: string | null;
  password?: string | null;
  trust_ssl_self_signed?: boolean;
}

/** 更新镜像搜索接口配置请求 */
export interface RegistryUpdate {
  name?: string;
  search_api_url?: string;
  mirror_url?: string | null;
  mirror_urls?: string[] | null;
  enable_mirror?: boolean;
  username?: string | null;
  password?: string | null;
  trust_ssl_self_signed?: boolean;
}

/** 查询镜像搜索接口配置列表 */
export function listRegistries() {
  return api.get<Registry[]>("/docker/registries").then((r) => r.data);
}

/** 创建镜像搜索接口配置 */
export function createRegistry(data: RegistryCreate) {
  return api.post("/docker/registries", data).then((r) => r.data);
}

/** 更新镜像搜索接口配置 */
export function updateRegistry(id: number, data: RegistryUpdate) {
  return api.put(`/docker/registries/${id}`, data).then((r) => r.data);
}

/** 删除镜像搜索接口配置 */
export function deleteRegistry(id: number) {
  return api.delete(`/docker/registries/${id}`).then((r) => r.data);
}

/** 设置默认镜像搜索接口配置 */
export function setDefaultRegistry(id: number) {
  return api.post(`/docker/registries/${id}/set-default`).then((r) => r.data);
}
