/**
 * 管理端目录同步 React Query hooks
 */

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useToast } from "@/components/ui/toast";
import type { ApiError } from "@/api/types";
import { syncCatalog, type AppSyncResult, type CatalogSyncResult } from "@/api/catalog";
import type { OrchestrationSyncResult } from "@/api/orchestrations";

/**
 * 手动触发从管理端同步应用市场与自动化配置（仅管理员可调用）
 *
 * 同步完成后刷新应用、分类与编排方案相关的所有查询。
 */
export function useCatalogSync() {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation<CatalogSyncResult, ApiError>({
    mutationFn: syncCatalog,
    onSuccess: (data) => {
      if (data?.skipped) {
        toast.success("同步已跳过：未配置管理端地址");
      } else {
        const apps: AppSyncResult = data?.apps ?? {};
        const orchestrations: OrchestrationSyncResult = data?.orchestrations ?? {};
        const message = `同步完成：应用市场新增 ${apps.created ?? 0} / 更新 ${
          apps.updated ?? 0
        }，自动化配置新增 ${orchestrations.created ?? 0} / 更新 ${
          orchestrations.updated ?? 0
        }`;
        if (data?.errors?.length) {
          toast.error(`${message}；失败：${data.errors.join("；")}`);
        } else {
          toast.success(message);
        }
      }
      qc.invalidateQueries({ queryKey: ["apps"] });
      qc.invalidateQueries({ queryKey: ["app-categories"] });
      qc.invalidateQueries({ queryKey: ["orchestrations"] });
      qc.invalidateQueries({ queryKey: ["orchestration-instances"] });
      qc.invalidateQueries({ queryKey: ["orchestration-update-events"] });
      qc.invalidateQueries({ queryKey: ["orchestration-sync-status"] });
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "同步失败");
    },
  });
}
