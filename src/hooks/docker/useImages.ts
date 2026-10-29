/**
 * Docker 镜像相关 React Query hooks
 *
 * - useImages / useImageDetail / useImageTags：镜像查询
 * - useSearchImages：Docker Hub 搜索
 * - usePullImage / usePullImageStream：拉取镜像
 * - useRemoveImage / useBatchRemoveImages / usePruneImages：删除与清理
 */

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useToast } from "@/components/ui/toast";
import { formatBytes } from "@/lib/utils";
import type { ApiError } from "@/api/types";
import * as dockerApi from "@/api/docker";
import type {
  BatchDeleteResult,
  BatchImageDeleteRequest,
  ImageDetail,
  ImageInfo,
  ImagePruneResult,
  ImageSearchPage,
  ImageTag,
} from "@/api/docker";

/**
 * 查询本地镜像列表
 *
 * @returns useQuery 对象，data 类型为 ImageInfo 数组
 */
export function useImages() {
  return useQuery<ImageInfo[]>({
    queryKey: ["docker", "images"],
    queryFn: dockerApi.listImages,
  });
}

/**
 * 删除本地镜像（mutation）
 *
 * @returns useMutation 对象
 */
export function useRemoveImage() {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: ({ id, force }: { id: string; force?: boolean }) =>
      dockerApi.removeImage(id, force),
    onSuccess: (_, { id }) => {
      qc.setQueryData<ImageInfo[]>(["docker", "images"], (old) =>
        old?.filter((img) => img.image_id !== id) ?? []
      );
      qc.invalidateQueries({ queryKey: ["docker", "images"], exact: true });
      toast.success("镜像已删除");
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "删除失败");
    },
  });
}

/**
 * 从 Docker Hub 搜索镜像
 *
 * @param q 搜索关键词
 * @param page 页码，从 1 开始
 * @returns useQuery 对象，data 类型为 ImageSearchPage
 */
export function useSearchImages(q: string, page: number = 1) {
  return useQuery<ImageSearchPage>({
    queryKey: ["docker", "images", "search", q, page],
    queryFn: () => dockerApi.searchImages(q, page),
    enabled: !!q.trim(),
    staleTime: 60_000,
    placeholderData: (previousData) => previousData,
  });
}

/**
 * 拉取镜像（mutation）
 *
 * @returns useMutation 对象
 */
export function usePullImage() {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: (image: string) => dockerApi.pullImage(image),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["docker", "images"], exact: true });
      toast.success("镜像拉取成功");
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "拉取失败");
    },
  });
}

/**
 * 批量删除本地镜像（mutation）
 *
 * @returns useMutation 对象，传入参数为 BatchImageDeleteRequest
 */
export function useBatchRemoveImages() {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: (data: BatchImageDeleteRequest) =>
      dockerApi.batchRemoveImages(data),
    onSuccess: (result: BatchDeleteResult) => {
      const deletedTags: string[] = result.deleted || [];
      qc.setQueryData<ImageInfo[]>(["docker", "images"], (old) =>
        old?.filter((img) => !deletedTags.includes(img.full_tag)) ?? []
      );
      qc.invalidateQueries({ queryKey: ["docker", "images"], exact: true });
      const failed = result.failed?.length || 0;
      if (deletedTags.length > 0) {
        toast.success(`已删除 ${deletedTags.length} 个镜像`);
      }
      if (failed > 0) {
        toast.error(`${failed} 个镜像删除失败`);
      }
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "批量删除失败");
    },
  });
}

/**
 * 查询镜像完整元数据
 *
 * @param image_id 镜像完整 ID
 * @returns useQuery 对象，data 类型为 ImageDetail
 */
export function useImageDetail(image_id: string | null) {
  return useQuery<ImageDetail>({
    queryKey: ["docker", "images", "detail", image_id],
    queryFn: () => dockerApi.getImageDetail(image_id!),
    enabled: !!image_id,
  });
}

/**
 * 查询指定镜像的可用标签列表
 *
 * @param image 镜像名称（不含标签）
 * @returns useQuery 对象，data 类型为 ImageTag 数组
 */
export function useImageTags(image: string) {
  return useQuery<ImageTag[]>({
    queryKey: ["docker", "images", "tags", image],
    queryFn: () => dockerApi.getImageTags(image),
    enabled: !!image.trim(),
    staleTime: 60_000,
  });
}

/**
 * 启动镜像拉取任务（mutation）
 *
 * 返回 task_id，用于后续进度跟踪。
 *
 * @returns useMutation 对象，传入参数为镜像名称（含标签）
 */
export function usePullImageStream() {
  const toast = useToast();
  return useMutation({
    mutationFn: (image: string) => dockerApi.pullImage(image),
    onError: (error: unknown) => {
      const err = error as { displayMessage?: string };
      toast.error(err.displayMessage || "启动拉取失败");
    },
  });
}

/**
 * 移除未使用镜像（mutation）
 *
 * @returns useMutation 对象
 */
export function usePruneImages() {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: () => dockerApi.pruneImages(),
    onSuccess: (result: ImagePruneResult) => {
      const deletedTags: string[] = result.deleted || [];
      qc.setQueryData<ImageInfo[]>(["docker", "images"], (old) =>
        old?.filter((img) => !deletedTags.includes(img.full_tag)) ?? []
      );
      qc.invalidateQueries({ queryKey: ["docker", "images"], exact: true });
      const space = result.space_reclaimed || 0;
      if (deletedTags.length > 0) {
        toast.success(`已清理 ${deletedTags.length} 个未使用镜像，释放 ${formatBytes(space)}`);
      } else {
        toast.success("没有可清理的未使用镜像");
      }
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "清理失败");
    },
  });
}
