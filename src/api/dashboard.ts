/**
 * 服务概览相关 API
 */

import api from "@/lib/api";

/** 服务概览中的单个已部署应用 */
export interface OverviewApp {
  id: number;
  app_name: string;
  display_name: string;
  description: string | null;
  icon: string | null;
  category: string;
  status: string;
  /** 后端根据部署端口自动生成的访问地址 */
  url: string | null;
  instance_name: string;
  /** 实例部署时的应用模板版本 */
  version?: string | null;
  /** 应用市场当前模板版本 */
  latest_version?: string | null;
  /** 单应用是否支持一键升级（编排应用由实例组统一升级） */
  upgrade_supported?: boolean;
  /** 是否有新版本可升级 */
  update_available?: boolean;
}

/** 按分类聚合的应用分组 */
export interface OverviewCategory {
  category: string;
  apps: OverviewApp[];
}

/** 服务概览响应 */
export interface Overview {
  categories: OverviewCategory[];
  total: number;
  running: number;
}

/** 容器端口映射 */
export interface OverviewAppPort {
  container_port: string;
  host_ip: string | null;
  host_port: string | null;
}

/** 容器实时监控指标 */
export interface OverviewAppMetrics {
  cpu_percent: number;
  memory_used: number;
  memory_limit: number;
  memory_percent: number;
  network_rx: number;
  network_tx: number;
  block_read: number;
  block_write: number;
  pids: number;
}

/** 应用详情：基本信息与容器信息，监控指标由独立查询获取 */
export interface OverviewAppDetail {
  app: OverviewApp;
  /** 关联的 Compose 项目；导入的外部容器为空 */
  project_id: number | null;
  image: string | null;
  container_id: string | null;
  container_name: string | null;
  project_name: string | null;
  restart_policy: string | null;
  started_at: string | null;
  uptime_seconds: number | null;
  ports: OverviewAppPort[];
  metrics: OverviewAppMetrics | null;
  error: string | null;
}

/** 查询服务概览 */
export function getOverview() {
  return api
    .get<Overview>("/dashboard/overview")
    .then((response) => response.data);
}

/** 查询单个应用的容器详情 */
export function getOverviewAppDetail(instanceId: number) {
  return api
    .get<OverviewAppDetail>(`/dashboard/apps/${instanceId}`)
    .then((response) => response.data);
}

/** 独立查询容器监控指标，避免慢采样阻塞详情首屏 */
export function getOverviewAppMetrics(instanceId: number) {
  return api
    .get<OverviewAppMetrics | null>(`/dashboard/apps/${instanceId}/metrics`)
    .then((response) => response.data);
}

/** 手动升级单应用（只替换镜像，返回部署任务） */
export function upgradeOverviewApp(instanceId: number) {
  return api
    .post<{
      task_id: string;
      instance_id: number;
      project_id: number | null;
      status: string;
    }>(`/dashboard/apps/${instanceId}/upgrade`)
    .then((response) => response.data);
}
