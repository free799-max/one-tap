/**
 * Docker 镜像搜索接口配置（Registry）相关 React Query hooks
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useToast } from "@/components/ui/toast";
import type { ApiError } from "@/api/types";
import * as dockerApi from "@/api/docker";
import type { Registry, RegistryCreate, RegistryUpdate } from "@/api/docker";

/**
 * 查询镜像搜索接口配置列表
 *
 * @returns useQuery 对象，data 类型为 Registry 数组
 */
export function useRegistries() {
  return useQuery<Registry[]>({
    queryKey: ["docker", "registries"],
    queryFn: dockerApi.listRegistries,
  });
}

/**
 * 创建镜像搜索接口配置（mutation）
 *
 * @returns useMutation 对象
 */
export function useCreateRegistry() {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: (data: RegistryCreate) => dockerApi.createRegistry(data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["docker", "registries"] });
      toast.success("配置已创建");
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "创建失败");
    },
  });
}

/**
 * 更新镜像搜索接口配置（mutation）
 *
 * @returns useMutation 对象
 */
export function useUpdateRegistry() {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: ({ id, data }: { id: number; data: RegistryUpdate }) =>
      dockerApi.updateRegistry(id, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["docker", "registries"] });
      toast.success("配置已更新");
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "更新失败");
    },
  });
}

/**
 * 删除镜像搜索接口配置（mutation）
 *
 * @returns useMutation 对象
 */
export function useDeleteRegistry() {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: (id: number) => dockerApi.deleteRegistry(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["docker", "registries"] });
      toast.success("配置已删除");
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "删除失败");
    },
  });
}

/**
 * 设置默认镜像搜索接口配置（mutation）
 *
 * @returns useMutation 对象
 */
export function useSetDefaultRegistry() {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: (id: number) => dockerApi.setDefaultRegistry(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["docker", "registries"] });
      toast.success("默认配置已切换");
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "切换失败");
    },
  });
}
