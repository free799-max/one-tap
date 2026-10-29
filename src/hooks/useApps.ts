/**
 * 应用商店相关 React Query hooks
 */

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useToast } from "@/components/ui/toast";
import type { ApiError } from "@/api/types";
import {
  listApps,
  listCategories,
  getApp,
  deployApp,
  previewApp,
  type App,
  type AppDetail,
  type CategoryOption,
  type DeployAppRequest,
  type AppPreviewRequest,
} from "@/api/apps";

// 保持既有导出，页面侧 import 路径无需变更
export type {
  App,
  AppDetail,
  CategoryOption,
  DeployAppRequest,
  DeployAppResponse,
  AppPreviewRequest,
  AppPreviewResponse,
} from "@/api/apps";

/**
 * 查询所有应用商店应用
 */
export function useApps(category?: string, tag?: string) {
  return useQuery<App[]>({
    queryKey: ["apps", { category, tag }],
    queryFn: () => listApps({ category, tag }),
  });
}

/**
 * 查询应用分类字典（显示名与排序由管理端维护）
 */
export function useCategories() {
  return useQuery<CategoryOption[]>({
    queryKey: ["app-categories"],
    queryFn: listCategories,
    staleTime: 5 * 60_000,
  });
}

/**
 * 查询单个应用详情
 */
export function useApp(
  name: string,
  options?: { enabled?: boolean; staleTime?: number }
) {
  return useQuery<AppDetail>({
    queryKey: ["apps", name],
    queryFn: () => getApp(name),
    enabled: options?.enabled ?? !!name,
    staleTime: options?.staleTime,
    refetchOnMount: "always",
  });
}

/**
 * 一键部署应用（异步）
 *
 * 后端立即返回任务 ID，调用方需自行监听部署进度。
 */
export function useDeployApp() {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: ({ name, data }: { name: string; data: DeployAppRequest }) =>
      deployApp(name, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["compose", "projects"] });
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "部署失败");
    },
  });
}

/**
 * 预览应用渲染后的 Compose YAML
 */
export function useAppPreview(name: string) {
  return useMutation({
    mutationFn: (data: AppPreviewRequest) => previewApp(name, data),
  });
}

