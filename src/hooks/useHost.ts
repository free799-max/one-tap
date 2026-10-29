/**
 * 宿主机相关 React Query hooks
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useToast } from "@/components/ui/toast";
import type { ApiError } from "@/api/types";
import {
  hostDirectoriesApi,
  type DirectoryList,
  type CreateDirectoryRequest,
  type RenameDirectoryRequest,
  type DeleteDirectoryRequest,
} from "@/api/directories";

// 保持既有导出，页面侧 import 路径无需变更
export type {
  DirectoryEntry,
  DirectoryList,
  CreateDirectoryRequest,
  RenameDirectoryRequest,
  DeleteDirectoryRequest,
} from "@/api/directories";

/**
 * 查询宿主机指定路径下的目录列表
 */
export function useDirectories(path: string, enabled = true) {
  return useQuery<DirectoryList>({
    queryKey: ["host", "directories", path],
    queryFn: () => hostDirectoriesApi.list(path),
    enabled: enabled && !!path,
    staleTime: 0,
  });
}

/**
 * 创建目录
 */
export function useCreateDirectory() {
  const qc = useQueryClient();
  const toast = useToast();

  return useMutation({
    mutationFn: (data: CreateDirectoryRequest) => hostDirectoriesApi.create(data),
    onSuccess: (_, vars) => {
      qc.invalidateQueries({ queryKey: ["host", "directories", vars.path] });
      toast.success("创建成功");
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "创建失败");
    },
  });
}

/**
 * 重命名目录
 */
export function useRenameDirectory() {
  const qc = useQueryClient();
  const toast = useToast();

  return useMutation({
    mutationFn: (data: RenameDirectoryRequest) => hostDirectoriesApi.rename(data),
    onSuccess: (_, vars) => {
      const parent = vars.old_path.replace(/\/[^/]*\/?$/, "") || "/";
      qc.invalidateQueries({ queryKey: ["host", "directories", parent] });
      toast.success("重命名成功");
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "重命名失败");
    },
  });
}

/**
 * 删除目录
 */
export function useDeleteDirectory() {
  const qc = useQueryClient();
  const toast = useToast();

  return useMutation({
    mutationFn: (data: DeleteDirectoryRequest) => hostDirectoriesApi.remove(data),
    onSuccess: (_, vars) => {
      const parent = vars.path.replace(/\/[^/]*\/?$/, "") || "/";
      qc.invalidateQueries({ queryKey: ["host", "directories", parent] });
      toast.success("删除成功");
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "删除失败");
    },
  });
}
