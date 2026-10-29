/**
 * Docker Compose 编排相关 API
 */

import api from "@/lib/api";
import type { ContainerInfo } from "@/api/docker";

/** Compose 版本信息 */
export interface ComposeVersion {
  id: number;
  version_number: number;
  content: string;
  comment: string | null;
  is_current: boolean;
  created_by_user_id: number | null;
  created_at: string;
}

/** Compose Stack 状态 */
export interface ComposeStackStatus {
  status: string;
  service_count: number;
  running_count: number;
  ports: string[];
  last_action: string | null;
  last_action_at: string | null;
  updated_at: string;
}

/** Compose 项目 */
export interface ComposeProject {
  id: number;
  project_name: string;
  description: string | null;
  is_active: boolean;
  current_version: ComposeVersion | null;
  stack: ComposeStackStatus | null;
  config_files: string[] | null;
  working_dir: string | null;
  created_at: string;
  updated_at: string;
}

/** 创建项目请求 */
export interface ComposeProjectCreate {
  project_name: string;
  description?: string | null;
  content: string;
}

/** 更新项目请求 */
export interface ComposeProjectUpdate {
  description?: string | null;
  is_active?: boolean | null;
}

/** 编辑项目并自动部署请求 */
export interface ComposeEditRequest {
  content: string;
  comment?: string | null;
  description?: string | null;
}

/** 操作请求 */
export interface ComposeActionRequest {
  action: "up" | "down" | "restart";
}

/** 部署/操作任务响应 */
export interface ComposeDeployTaskResponse {
  task_id: string;
  project_id: number;
  action: string;
  status: string;
}

/** 查询 Compose 项目列表 */
export function listComposeProjects() {
  return api.get<ComposeProject[]>("/docker/compose").then((r) => r.data);
}

/** 查询单个 Compose 项目详情 */
export function getComposeProject(projectId: number) {
  return api.get<ComposeProject>(`/docker/compose/${projectId}`).then((r) => r.data);
}

/** 创建 Compose 项目（异步，返回任务 ID） */
export function createComposeProject(data: ComposeProjectCreate) {
  return api
    .post<ComposeDeployTaskResponse>("/docker/compose", data)
    .then((r) => r.data);
}

/** 更新 Compose 项目元数据 */
export function updateComposeProject(
  projectId: number,
  data: ComposeProjectUpdate
) {
  return api
    .put<ComposeProject>(`/docker/compose/${projectId}`, data)
    .then((r) => r.data);
}

/** 编辑 Compose 项目并自动部署（异步） */
export function editComposeProject(projectId: number, data: ComposeEditRequest) {
  return api
    .post<ComposeDeployTaskResponse>(`/docker/compose/${projectId}/edit`, data)
    .then((r) => r.data);
}

/** 删除 Compose 项目 */
export function deleteComposeProject(projectId: number) {
  return api.delete(`/docker/compose/${projectId}`).then((r) => r.data);
}

/** 查询 Compose 版本列表 */
export function listComposeVersions(projectId: number) {
  return api
    .get<ComposeVersion[]>(`/docker/compose/${projectId}/versions`)
    .then((r) => r.data);
}

/** 切换 Compose 版本 */
export function rollbackComposeVersion(projectId: number, versionId: number) {
  return api
    .post<ComposeVersion>(
      `/docker/compose/${projectId}/versions/${versionId}/rollback`
    )
    .then((r) => r.data);
}

/** 执行 Compose 操作（up/down/restart，异步） */
export function composeAction(
  projectId: number,
  action: ComposeActionRequest["action"]
) {
  return api
    .post<ComposeDeployTaskResponse>(`/docker/compose/${projectId}/action`, {
      action,
    })
    .then((r) => r.data);
}

/** 查询 Compose 日志 */
export function getComposeLogs(
  projectId: number,
  tail: number,
  services?: string[]
) {
  return api
    .get<{ logs: string }>(`/docker/compose/${projectId}/logs`, {
      params: {
        tail,
        services: services?.join(","),
      },
    })
    .then((r) => r.data);
}

/** 查询/同步 Compose Stack 状态 */
export function getComposeStatus(projectId: number) {
  return api
    .get<ComposeStackStatus>(`/docker/compose/${projectId}/status`)
    .then((r) => r.data);
}

/** 查询 Compose 项目维护的容器列表 */
export function getComposeProjectContainers(projectId: number) {
  return api
    .get<ContainerInfo[]>(`/docker/compose/${projectId}/containers`)
    .then((r) => r.data);
}
