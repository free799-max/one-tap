/**
 * 网络加速相关 React Query hooks
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useToast } from "@/components/ui/toast";
import { useDeployTasks } from "@/hooks/useDeployTasks";
import type { ApiError } from "@/api/types";
import {
  deployAccelMihomo,
  getAccelManaged,
  listAccelApps,
  removeAccelMihomo,
  setAccelAppProxy,
  syncAccelManaged,
  updateAccelSyncSettings,
} from "@/api/acceleration";

const KEY = ["acceleration", "managed"];

export function useAccelManaged() {
  return useQuery({
    queryKey: KEY,
    queryFn: getAccelManaged,
    refetchInterval: 60000,
  });
}

export function useSyncAccelManaged() {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: syncAccelManaged,
    onSuccess: (result) => {
      qc.invalidateQueries({ queryKey: KEY });
      toast.success(result.message);
    },
    onError: (error: ApiError) =>
      toast.error(error.displayMessage || "同步订阅失败"),
  });
}

export function useUpdateAccelSyncSettings() {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: updateAccelSyncSettings,
    onSuccess: (result) => {
      qc.invalidateQueries({ queryKey: KEY });
      toast.success(result.message);
    },
    onError: (error: ApiError) =>
      toast.error(error.displayMessage || "保存自动同步配置失败"),
  });
}

export function useDeployAccelMihomo() {
  const qc = useQueryClient();
  const toast = useToast();
  const { startTask } = useDeployTasks();
  return useMutation({
    mutationFn: deployAccelMihomo,
    onSuccess: (result) => {
      qc.invalidateQueries({ queryKey: KEY });
      startTask(result.task_id);
      toast.success("部署任务已启动");
    },
    onError: (error: ApiError) =>
      toast.error(error.displayMessage || "部署 Mihomo 失败"),
  });
}

export function useRemoveAccelMihomo() {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: removeAccelMihomo,
    onSuccess: (result) => {
      qc.invalidateQueries({ queryKey: KEY });
      qc.invalidateQueries({ queryKey: APPS_KEY });
      toast.success(result.message);
    },
    onError: (error: ApiError) =>
      toast.error(error.displayMessage || "删除 Mihomo 失败"),
  });
}

const APPS_KEY = ["acceleration", "apps"];

export function useAccelApps() {
  return useQuery({
    queryKey: APPS_KEY,
    queryFn: listAccelApps,
  });
}

export function useSetAccelAppProxy() {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: ({ instanceId, enabled }: { instanceId: number; enabled: boolean }) =>
      setAccelAppProxy(instanceId, enabled),
    onSuccess: (result) => {
      qc.invalidateQueries({ queryKey: APPS_KEY });
      qc.invalidateQueries({ queryKey: KEY });
      toast.success(result.message);
    },
    onError: (error: ApiError) =>
      toast.error(error.displayMessage || "应用加速配置失败"),
  });
}
