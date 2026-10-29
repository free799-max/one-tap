/**
 * 应用编排（自动化组合模板）相关 API
 */

import api from "@/lib/api";

/** 组合中的应用定义 */
export interface AppCompositionItem {
  app_name: string;
  relation: "required" | "optional" | "suggested" | "conflicting";
  group?: string | null;
  conflict_with?: string[];
  /** 方案中指定的镜像地址（可选） */
  image?: string | null;
  /** 方案中指定的镜像标签（可选） */
  tag?: string | null;
}

/** 应用编排信息 */
export interface AppOrchestration {
  id: number;
  name: string;
  display_name: string;
  description: string | null;
  category: string;
  sort_order: number;
  icon: string | null;
  website: string | null;
  source_url: string | null;
  version: string;
  is_builtin: boolean;
  is_listed: boolean;
  app_composition: AppCompositionItem[];
  shared_config_schema: Record<string, unknown>;
  /** 当前方案版本的更新说明（可能暂未由后端返回） */
  changelog?: string | null;
}

/** 部署请求 */
export interface DeployOrchestrationRequest {
  instance_name: string;
  selected_apps: string[];
  app_configs: Record<string, Record<string, unknown>>;
  shared_config: Record<string, unknown>;
}

/** 部署响应 */
export interface DeployOrchestrationResponse {
  group_id: number;
  instance_name: string;
  status: string;
  task_ids: string[];
}

/** 单个容器匹配信息 */
export interface ContainerMatch {
  container_id: string;
  container_name: string;
  image: string;
  network_ip: string | null;
  host_port: number | null;
  container_port: string | null;
  suggested_url: string | null;
}

/** 可导入应用候选 */
export interface ImportCandidateApp {
  app_name: string;
  display_name: string;
  icon: string | null;
  relation: string;
  group: string | null;
  matched: boolean;
  candidates: ContainerMatch[];
}

/** 单个应用导入配置 */
export interface OrchestrationImportAppConfig {
  selected_container_id?: string | null;
  auth_type?: "none" | "basic" | "api_key";
  url?: string | null;
  username?: string | null;
  password?: string | null;
  api_key?: string | null;
}

/** 导入请求 */
export interface OrchestrationImportRequest {
  instance_name: string;
  selected_apps: string[];
  app_configs: Record<string, OrchestrationImportAppConfig>;
  shared_config: Record<string, unknown>;
}

/** 导入响应 */
export interface OrchestrationImportResponse {
  group_id: number;
  instance_name: string;
  status: string;
  created_app_instance_ids: number[];
}

/** 编排实例组中的应用 */
export interface OrchestrationInstanceApp {
  id: number;
  app_name: string;
  display_name: string;
  icon: string | null;
  status: string;
  config: Record<string, unknown>;
  /** 关联的 Compose 项目；导入的独立容器为空 */
  project_id?: number | null;
  /** 当前 Compose 主服务镜像 */
  current_image?: string | null;
  /** 按当前模板升级后的目标镜像 */
  target_image?: string | null;
}

/** 编排实例组 */
export interface OrchestrationInstanceGroup {
  id: number;
  instance_name: string;
  orchestration_name: string;
  orchestration_display_name: string;
  status: string;
  created_at: string;
  apps: OrchestrationInstanceApp[];
  /** 实例部署时的方案版本 */
  orchestration_version?: string | null;
  /** 模板当前版本 */
  template_version?: string | null;
  /** 是否存在可升级的新方案版本 */
  upgrade_available?: boolean;
  /** 模板当前版本的更新说明 */
  template_changelog?: string | null;
}

/** 备份中的应用数据目录 */
export interface OrchestrationBackupApp {
  app_name: string;
  instance_name?: string | null;
}

/** 可恢复的实例组备份 */
export interface OrchestrationBackup {
  name: string;
  instance_name: string;
  orchestration_name?: string | null;
  deleted_at?: string | null;
  apps: OrchestrationBackupApp[];
}

/** 从备份恢复请求 */
export interface RestoreOrchestrationRequest {
  backup: string;
  instance_name?: string | null;
}

/** 编排实例组详情 */
export interface OrchestrationInstanceDetail
  extends OrchestrationInstanceGroup {
  shared_config: Record<string, unknown>;
  app_configs: Record<string, Record<string, unknown>>;
  /** 模板当前版本的更新说明 */
  template_changelog?: string | null;
}

/** 自动整理目录层级（Music Tag Web 的 dir_list 项） */
export interface MusicAutoDirItem {
  value: string;
  value2?: string;
}

/** 自动整理最近一次运行结果 */
export interface MusicAutoOrganizeLastRun {
  status?: "ok" | "partial" | "error";
  trigger?: string;
  processed?: number;
  failed?: number;
  total?: number;
  message?: string;
  started_at?: string;
  finished_at?: string;
  navidrome?: { ok: boolean; message?: string };
}

/** 自动整理配置（存于 app_configs["music-tag-web"].auto_organize） */
export interface MusicAutoOrganizeConfig {
  enabled: boolean;
  interval_minutes: number;
  watch_dir: string;
  library_dir: string;
  scrape_enabled: boolean;
  tidy_enabled: boolean;
  source_list: string[];
  modify_list: string[];
  dir_list: MusicAutoDirItem[];
  min_age_seconds: number;
  max_files_per_run: number;
  scrape_timeout_seconds: number;
  tidy_timeout_seconds: number;
  navidrome_scan: boolean;
  select_mode: string;
  skip_tag: boolean;
  folder_album: boolean;
  last_run_at?: string | null;
  last_result?: MusicAutoOrganizeLastRun | null;
}

/** 自动整理面板数据 */
export interface MusicAutoOrganizePanel {
  supported: boolean;
  config: MusicAutoOrganizeConfig;
  detected?: { watch_dir: string; library_dir: string };
  running: boolean;
  progress?: {
    running?: boolean;
    stage?: string;
    message?: string;
    processed?: number;
    failed?: number;
    total?: number;
    started_at?: string;
  } | null;
  last_run?: MusicAutoOrganizeLastRun | null;
  stats?: { pending?: number; done?: number; failed?: number };
}

/** Reader 自动同步最近一次运行结果 */
export interface ReaderAutoSyncLastRun {
  status?: "ok" | "partial" | "error";
  trigger?: string;
  processed?: number;
  failed?: number;
  total?: number;
  exported?: number;
  cached?: number;
  message?: string;
  started_at?: string;
  finished_at?: string;
  ermao?: { ok: boolean; message?: string };
}

/** Reader 自动同步配置（存于 app_configs["reader"].auto_sync） */
export interface ReaderAutoSyncConfig {
  enabled: boolean;
  interval_minutes: number;
  reader_url: string;
  reader_username: string;
  reader_password: string;
  reader_token: string;
  library_dir: string;
  library_host_dir: string;
  ermao_library_dir: string;
  cache_missing_chapters: boolean;
  max_seconds: number;
  scan_enabled: boolean;
  scan_wait_seconds: number;
  auto_setup: boolean;
  ermao_url: string;
  ermao_email: string;
  ermao_password: string;
  ermao_lib_id: string;
  last_run_at?: string | null;
  last_result?: ReaderAutoSyncLastRun | null;
}

/** Reader 自动同步从应用实际部署中检测到的值 */
export interface ReaderAutoSyncDetected {
  reader_url: string;
  reader_username?: string;
  reader_password?: string;
  reader_token?: string;
  ermao_url: string;
  library_dir: string;
  library_host_dir: string;
  ermao_library_dir: string;
  ermao_email: string;
  ermao_password?: string;
  source: "docker" | "config" | string;
}

/** Reader 自动同步面板数据 */
export interface ReaderAutoSyncPanel {
  supported: boolean;
  config: ReaderAutoSyncConfig;
  detected?: ReaderAutoSyncDetected;
  running: boolean;
  progress?: {
    running?: boolean;
    stage?: string;
    message?: string;
    processed?: number;
    failed?: number;
    total?: number;
    started_at?: string;
  } | null;
  last_run?: ReaderAutoSyncLastRun | null;
  stats?: { pending?: number; done?: number; failed?: number };
}

/** 更新编排实例组请求 */
export interface OrchestrationInstanceUpdatePayload {
  instance_name?: string;
  shared_config?: Record<string, unknown>;
  app_configs?: Record<string, Record<string, unknown>>;
}

/** 更新事件范围：方案模板 / 实例组 */
export type OrchestrationUpdateEventScope = "template" | "instance";

/** 更新方式：自动 / 手动 */
export type OrchestrationUpdateEventMode = "auto" | "manual";

/** 更新结果 */
export type OrchestrationUpdateEventStatus = "applied" | "failed";

/** 更新事件附加信息 */
export interface OrchestrationUpdateEventDetail {
  changelog?: string | null;
  apps_added?: string[];
  apps_removed?: string[];
  /** 新方案已移除、但升级时保留运行的应用 */
  apps_retained?: string[];
  selected_apps?: string[];
  /** 升级前配置快照 ID */
  backup_id?: number;
  /** 升级模式，如 image_only */
  mode?: string;
}

/** 方案/实例更新记录 */
export interface OrchestrationUpdateEvent {
  id: number;
  orchestration_name: string;
  scope: OrchestrationUpdateEventScope;
  group_id?: number | null;
  from_version?: string | null;
  to_version?: string | null;
  mode: OrchestrationUpdateEventMode;
  status: OrchestrationUpdateEventStatus;
  detail?: OrchestrationUpdateEventDetail | null;
  message?: string | null;
  created_at: string;
}

/** 更新记录查询参数 */
export interface OrchestrationUpdateEventsParams {
  scope?: OrchestrationUpdateEventScope;
  orchestration_name?: string;
  limit?: number;
}

/** 方案同步统计结果 */
export interface OrchestrationSyncResult {
  skipped?: boolean;
  created?: number;
  updated?: number;
  /** 本次同步清理的重复内置旧方案数 */
  removed?: number;
  total?: number;
  /** 本次同步产生的版本变更事件数 */
  events?: number;
}

/** 方案同步状态 */
export interface OrchestrationSyncStatus {
  last_sync_at?: string | null;
  last_error?: string | null;
  last_result?: OrchestrationSyncResult | null;
}

/** 应用认证检测请求 */
export interface AppAuthVerifyRequest {
  app_name: string;
  url: string;
  auth_type: "none" | "basic" | "api_key";
  username?: string;
  password?: string;
  api_key?: string;
}

/** 应用认证检测响应 */
export interface AppAuthVerifyResponse {
  valid: boolean;
  message?: string;
}

/** 检测项状态 */
export type InspectItemStatus = "ok" | "warn" | "error";

/** 应用配置健康检测项 */
export interface AppInspectItem {
  id: string;
  title: string;
  status: InspectItemStatus;
  message?: string | null;
  current_value?: string | null;
  expected_value?: string | null;
  fixable: boolean;
  /** 展示分组（可选），如「核心配置」 */
  category?: string | null;
}

/** 应用配置健康检测报告 */
export interface AppInspectReport {
  supported: boolean;
  items: AppInspectItem[];
}

/** 单个检测项的自动修复结果 */
export interface AppFixResultItem {
  id: string;
  ok: boolean;
  message?: string | null;
}

/** 检测项自动修复响应 */
export interface AppFixResponse {
  results: AppFixResultItem[];
}

/** 应用插件（MoviePilot 原始结构透传，字段做容错读取） */
export interface AppPlugin {
  id?: string;
  plugin_id?: string;
  plugin_name?: string;
  plugin_desc?: string;
  plugin_version?: string;
  plugin_author?: string;
  repo_url?: string;
  installed?: boolean;
  [key: string]: unknown;
}

/** 应用插件列表响应 */
export interface AppPluginsResponse {
  installed: AppPlugin[];
  market: AppPlugin[];
}

/** 离线资源拷贝项 */
export interface AppAssetCopy {
  label: string;
  size: string;
  source: string;
  target: string;
}

/** 应用离线资源状态（插件依赖/浏览器内核） */
export interface AppAssetsStatus {
  supported: boolean;
  app_name?: string;
  assets_image?: string;
  revision?: string;
  container?: string | null;
  running?: boolean;
  copied?: boolean;
  copied_at?: string | null;
  marker_image?: string | null;
  copies?: AppAssetCopy[];
  message?: string;
}

/** 装配记录（下载器/媒体服务器） */
export interface IntegrationRecord {
  name: string;
  type?: string;
  url?: string | null;
  status: "ok" | "error";
  message?: string;
  webhook_url?: string;
}

/** 应用间自动装配结果（存于 app_configs[目标应用].integrations） */
export interface AppIntegrations {
  wired_at?: string;
  downloaders: IntegrationRecord[];
  mediaservers: IntegrationRecord[];
}

/** 查询应用编排列表，支持分类筛选 */
export function listOrchestrations(category?: string) {
  return api
    .get<AppOrchestration[]>("/orchestrations", { params: { category } })
    .then((r) => r.data);
}

/** 查询单个应用编排详情 */
export function getOrchestration(name: string) {
  return api.get<AppOrchestration>(`/orchestrations/${name}`).then((r) => r.data);
}

/** 查询编排实例组列表，支持分类筛选 */
export function listOrchestrationInstances(category?: string) {
  return api
    .get<OrchestrationInstanceGroup[]>("/orchestrations/instances", {
      params: { category },
    })
    .then((r) => r.data);
}

/** 查询单个编排实例组详情 */
export function getOrchestrationInstanceDetail(instanceId: number) {
  return api
    .get<OrchestrationInstanceDetail>(`/orchestrations/instances/${instanceId}`)
    .then((r) => r.data);
}

/** 更新编排实例组信息 */
export function updateOrchestrationInstance(
  id: number,
  data: OrchestrationInstanceUpdatePayload
) {
  return api
    .patch<OrchestrationInstanceDetail>(`/orchestrations/instances/${id}`, data)
    .then((r) => r.data);
}

/** 删除实例组响应（后台任务） */
export interface DeleteOrchestrationResponse {
  task_id: string;
}

/** 删除编排实例组（后台任务，返回任务 ID） */
export function deleteOrchestrationInstance(id: number) {
  return api
    .delete<DeleteOrchestrationResponse>(`/orchestrations/instances/${id}`)
    .then((r) => r.data);
}

/** 列出可恢复的实例组备份 */
export function listOrchestrationBackups(orchestration?: string) {
  return api
    .get<OrchestrationBackup[]>("/orchestrations/backups", {
      params: { orchestration },
    })
    .then((r) => r.data);
}

/** 从备份恢复实例组（移回数据目录并重新部署） */
export function restoreOrchestrationBackup(data: RestoreOrchestrationRequest) {
  return api
    .post<DeployOrchestrationResponse>(
      "/orchestrations/backups/restore",
      data
    )
    .then((r) => r.data);
}

/** 查询可导入的 Docker 容器候选 */
export function getImportCandidates(name: string) {
  return api
    .get<ImportCandidateApp[]>(`/orchestrations/${name}/import-candidates`)
    .then((r) => r.data);
}

/** 导入已有 Docker 部署为编排实例 */
export function importOrchestration(
  name: string,
  data: OrchestrationImportRequest
) {
  return api
    .post<OrchestrationImportResponse>(`/orchestrations/${name}/import`, data)
    .then((r) => r.data);
}

/** 验证应用访问地址与认证信息 */
export function verifyAppAuth(data: AppAuthVerifyRequest) {
  return api
    .post<AppAuthVerifyResponse>("/orchestrations/auth/verify", data)
    .then((r) => r.data);
}

/** 组合部署应用编排 */
export function deployOrchestration(
  name: string,
  data: DeployOrchestrationRequest
) {
  return api
    .post<DeployOrchestrationResponse>(`/orchestrations/${name}/deploy`, data)
    .then((r) => r.data);
}

/** 修复编排实例组（重新部署失败/卡死的应用） */
export function redeployOrchestrationInstance(
  id: number,
  data: DeployOrchestrationRequest
) {
  return api
    .post<DeployOrchestrationResponse>(
      `/orchestrations/instances/${id}/redeploy`,
      data
    )
    .then((r) => r.data);
}

/** 升级编排实例组到最新方案版本（payload 与部署/修复一致） */
export function upgradeOrchestrationInstance(
  id: number,
  data: DeployOrchestrationRequest
) {
  return api
    .post<DeployOrchestrationResponse>(
      `/orchestrations/instances/${id}/upgrade`,
      data
    )
    .then((r) => r.data);
}

/** 查询方案与实例的更新记录 */
export function listUpdateEvents(params: OrchestrationUpdateEventsParams = {}) {
  return api
    .get<OrchestrationUpdateEvent[]>("/orchestrations/update-events", {
      params,
    })
    .then((r) => r.data ?? []);
}

/** 手动触发方案目录同步 */
export function syncOrchestrations() {
  return api
    .post<OrchestrationSyncResult>("/orchestrations/sync")
    .then((r) => r.data);
}

/** 查询最近一次方案同步状态 */
export function getSyncStatus() {
  return api
    .get<OrchestrationSyncStatus>("/orchestrations/sync-status")
    .then((r) => r.data);
}

/** 手动重跑应用间自动装配（后台执行，立即返回进度任务 ID） */
export function wireOrchestrationInstance(id: number) {
  return api
    .post<{ ok: boolean; message?: string; task_id?: string | null }>(
      `/orchestrations/instances/${id}/wire`
    )
    .then((r) => r.data);
}

/** 读取应用自动整理配置与运行状态 */
export function getAppAutoOrganize(instanceId: number, appName: string) {
  return api
    .get<MusicAutoOrganizePanel>(
      `/orchestrations/instances/${instanceId}/apps/${appName}/auto-organize`
    )
    .then((r) => r.data);
}

/** 保存应用自动整理配置 */
export function saveAppAutoOrganize(
  instanceId: number,
  appName: string,
  config: MusicAutoOrganizeConfig
) {
  return api
    .put<MusicAutoOrganizePanel>(
      `/orchestrations/instances/${instanceId}/apps/${appName}/auto-organize`,
      config
    )
    .then((r) => r.data);
}

/** 立即执行一轮自动整理 */
export function runAppAutoOrganize(instanceId: number, appName: string) {
  return api
    .post<{ ok: boolean; message?: string; status?: Record<string, unknown> }>(
      `/orchestrations/instances/${instanceId}/apps/${appName}/auto-organize/run`
    )
    .then((r) => r.data);
}

/** 读取 Reader 自动同步配置与运行状态 */
export function getAppAutoSync(instanceId: number, appName: string) {
  return api
    .get<ReaderAutoSyncPanel>(
      `/orchestrations/instances/${instanceId}/apps/${appName}/auto-sync`
    )
    .then((r) => r.data);
}

/** 保存 Reader 自动同步配置 */
export function saveAppAutoSync(
  instanceId: number,
  appName: string,
  config: ReaderAutoSyncConfig
) {
  return api
    .put<ReaderAutoSyncPanel>(
      `/orchestrations/instances/${instanceId}/apps/${appName}/auto-sync`,
      config
    )
    .then((r) => r.data);
}

/** 立即执行一轮 Reader 自动同步 */
export function runAppAutoSync(instanceId: number, appName: string) {
  return api
    .post<{ ok: boolean; message?: string; status?: Record<string, unknown> }>(
      `/orchestrations/instances/${instanceId}/apps/${appName}/auto-sync/run`
    )
    .then((r) => r.data);
}

/** 对组内指定应用执行配置健康检测 */
export function inspectOrchestrationApp(instanceId: number, appName: string) {
  return api
    .get<AppInspectReport>(
      `/orchestrations/instances/${instanceId}/apps/${appName}/inspect`
    )
    .then((r) => r.data);
}

/** 对组内指定应用的检测项执行自动修复 */
export function fixOrchestrationAppInspect(
  instanceId: number,
  appName: string,
  itemIds: string[]
) {
  return api
    .post<AppFixResponse>(
      `/orchestrations/instances/${instanceId}/apps/${appName}/inspect/fix`,
      { item_ids: itemIds }
    )
    .then((r) => r.data);
}

/** 查询应用已安装插件与插件市场列表（当前仅 MoviePilot 支持） */
export function listOrchestrationAppPlugins(
  instanceId: number,
  appName: string
) {
  return api
    .get<AppPluginsResponse>(
      `/orchestrations/instances/${instanceId}/apps/${appName}/plugins`
    )
    .then((r) => r.data);
}

/** 安装应用插件（当前仅 MoviePilot 支持） */
export function installOrchestrationAppPlugin(
  instanceId: number,
  appName: string,
  data: { plugin_id: string; repo_url?: string }
) {
  return api
    .post<{ ok: boolean; message?: string }>(
      `/orchestrations/instances/${instanceId}/apps/${appName}/plugins/install`,
      data
    )
    .then((r) => r.data);
}

/** 查询应用离线资源（插件依赖/浏览器内核）的拷贝状态 */
export function getOrchestrationAppAssets(
  instanceId: number,
  appName: string
) {
  return api
    .get<AppAssetsStatus>(
      `/orchestrations/instances/${instanceId}/apps/${appName}/assets`
    )
    .then((r) => r.data);
}

/** 把离线资源拷贝到应用容器（后台任务） */
export function copyOrchestrationAppAssets(
  instanceId: number,
  appName: string
) {
  return api
    .post<{ ok: boolean; task_id?: string | null }>(
      `/orchestrations/instances/${instanceId}/apps/${appName}/assets/copy`
    )
    .then((r) => r.data);
}
