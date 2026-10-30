/**
 * 服务概览 React Query hooks
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  getOverview,
  getOverviewAppDetail,
  upgradeOverviewApp,
  type Overview,
  type OverviewAppDetail,
} from "@/api/dashboard";

export type {
  Overview,
  OverviewApp,
  OverviewAppDetail,
  OverviewAppMetrics,
  OverviewAppPort,
  OverviewCategory,
} from "@/api/dashboard";

/** 查询服务概览，每分钟自动刷新运行状态与新增应用 */
export function useOverview() {
  return useQuery<Overview>({
    queryKey: ["dashboard", "overview"],
    queryFn: getOverview,
    refetchInterval: 60_000,
    refetchOnWindowFocus: true,
  });
}

/** 查询应用容器详情与监控指标，弹窗打开时每 5 秒刷新 */
export function useOverviewAppDetail(instanceId: number | null) {
  return useQuery<OverviewAppDetail>({
    queryKey: ["dashboard", "app-detail", instanceId],
    queryFn: () => getOverviewAppDetail(instanceId as number),
    enabled: instanceId != null,
    refetchInterval: instanceId != null ? 5_000 : false,
  });
}

/** 手动升级单应用，成功后刷新概览与详情 */
export function useUpgradeOverviewApp() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (instanceId: number) => upgradeOverviewApp(instanceId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    },
  });
}
