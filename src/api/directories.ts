/**
 * 宿主机目录浏览/管理 API
 *
 * 宿主机端点（/docker/host/directories）与设置端点（/settings/directories）
 * 提供完全相同的操作，通过工厂函数复用。
 */

import api from "@/lib/api";

/** 目录条目 */
export interface DirectoryEntry {
  name: string;
  path: string;
  is_directory: boolean;
}

/** 目录列表响应 */
export interface DirectoryList {
  path: string;
  entries: DirectoryEntry[];
  exists?: boolean;
}

/** 创建目录请求 */
export interface CreateDirectoryRequest {
  path: string;
  name: string;
}

/** 重命名目录请求 */
export interface RenameDirectoryRequest {
  old_path: string;
  new_name: string;
}

/** 删除目录请求 */
export interface DeleteDirectoryRequest {
  path: string;
}

/** 目录操作 API 工厂：两个端点共用同一组操作 */
function createDirectoryApi(baseUrl: string) {
  return {
    /** 查询指定路径下的目录列表 */
    list: (path: string) =>
      api.get<DirectoryList>(baseUrl, { params: { path } }).then((r) => r.data),
    /** 创建目录 */
    create: (data: CreateDirectoryRequest) =>
      api.post(baseUrl, data).then((r) => r.data),
    /** 重命名目录 */
    rename: (data: RenameDirectoryRequest) =>
      api.put(baseUrl, data).then((r) => r.data),
    /** 删除目录 */
    remove: (data: DeleteDirectoryRequest) =>
      api.delete(baseUrl, { data }).then((r) => r.data),
  };
}

/** 宿主机目录端点（部署表单中使用） */
export const hostDirectoriesApi = createDirectoryApi("/docker/host/directories");

/** 设置页目录端点（系统设置中使用） */
export const settingsDirectoriesApi = createDirectoryApi("/settings/directories");
