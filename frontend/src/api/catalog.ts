/**
 * 管理端目录同步 API
 *
 * 从配套管理端拉取应用分类、应用市场与自动化配置到本地缓存。
 */

import api from "@/lib/api";
import type { OrchestrationSyncResult } from "@/api/orchestrations";

/** 应用市场同步统计 */
export interface AppSyncResult {
  /** 未配置管理端地址时为 true，否则为因校验失败跳过的条目数 */
  skipped?: boolean | number;
  created?: number;
  updated?: number;
  removed?: number;
  total?: number;
  error?: string;
}

/** 分类字典同步统计 */
export interface CategorySyncResult {
  skipped?: boolean;
  total?: number;
  error?: string;
}

/** 目录同步结果（各部分独立，单类失败不影响其余部分） */
export interface CatalogSyncResult {
  /** 未配置管理端地址时整体跳过 */
  skipped?: boolean;
  categories?: CategorySyncResult;
  apps?: AppSyncResult;
  orchestrations?: OrchestrationSyncResult;
  /** 失败项描述，为空表示全部成功 */
  errors?: string[];
}

/** 手动触发从管理端同步目录（仅管理员） */
export function syncCatalog() {
  return api.post<CatalogSyncResult>("/catalog/sync").then((r) => r.data);
}
