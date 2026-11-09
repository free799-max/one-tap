/**
 * 系统设置相关 React Query hooks
 *
 * 提供系统全局配置的查询和更新能力。
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useToast } from "@/components/ui/toast";
import { useDeployTasks } from "@/hooks/useDeployTasks";
import type { ApiError } from "@/api/types";
import {
  getSystemConfig,
  updateSystemConfig,
  getPortSuggestions,
  listStorageDirectories,
  testProxy,
  listRemoteDockerMirrors,
  getDockerMirrorEffectiveness,
  testDockerMirrors,
  listAutoUpdateTargets,
  createStorageDirectory,
  updateStorageDirectory,
  deleteStorageDirectory,
  getHostsOptimizerConfig,
  updateHostsOptimizerConfig,
  getHostsOptimizerStatus,
  testHostsOptimizer,
  runHostsOptimizer,
  getHostsAccelerationOverview,
  toggleAppAcceleration,
  deployHostsAdguard,
  removeHostsAdguard,
  syncHostsManagedRules,
  type SystemConfig,
  type SystemConfigUpdate,
  type StorageDirectory,
  type StorageDirectoryCreate,
  type StorageDirectoryUpdate,
  type HostsOptimizerConfig,
  type HostsOptimizerStatus,
  type AccelerationOverview,
  type AccelerationToggleResult,
  type PortSuggestionOptions,
  type AutoUpdateTargets,
} from "@/api/settings";
import {
  settingsDirectoriesApi,
  type DirectoryList,
  type CreateDirectoryRequest,
  type RenameDirectoryRequest,
  type DeleteDirectoryRequest,
} from "@/api/directories";

// 保持既有导出，页面侧 import 路径无需变更
export type { SystemConfig, SystemConfigUpdate } from "@/api/settings";
export type {
  StorageDirectory,
  StorageDirectoryCreate,
  StorageDirectoryUpdate,
  HostsOptimizerConfig,
  HostsOptimizerStatus,
  ProxyTestResult,
  RemoteDockerMirror,
  RemoteDockerMirrors,
  AutoUpdateTarget,
  AutoUpdateTargets,
  DockerMirrorTestItem,
  DockerMirrorTestResult,
  AccelerationOverview,
  AccelerationApp,
  AccelerationAdguard,
  AccelerationToggleResult,
  AdguardDeployResult,
  ManagedDnsRule,
  ManagedDnsRules,
} from "@/api/settings";
export type {
  DirectoryList,
  CreateDirectoryRequest,
  RenameDirectoryRequest,
  DeleteDirectoryRequest,
} from "@/api/directories";

/**
 * 查询系统全局配置
 */
export function useSystemConfig() {
  return useQuery<SystemConfig>({
    queryKey: ["settings", "system-config"],
    queryFn: getSystemConfig,
  });
}

/**
 * 更新系统全局配置
 */
export function useUpdateSystemConfig() {
  const qc = useQueryClient();
  const toast = useToast();

  return useMutation({
    mutationFn: (data: SystemConfigUpdate) => updateSystemConfig(data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["settings", "system-config"] });
      toast.success("保存成功");
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "保存失败");
    },
  });
}

/**
 * 查询可勾选自动更新的服务
 */
export function useAutoUpdateTargets(enabled = true) {
  return useQuery<AutoUpdateTargets>({
    queryKey: ["settings", "auto-update-targets"],
    queryFn: listAutoUpdateTargets,
    enabled,
    staleTime: 15_000,
  });
}

/**
 * 查询服务端口分配建议（部署表单预填端口用）
 */
export function usePortSuggestions(
  count: number,
  options?: PortSuggestionOptions
) {
  const preferredKey = options?.preferred?.join(",") ?? "";
  return useQuery<number[]>({
    queryKey: [
      "settings",
      "port-suggestions",
      count,
      preferredKey,
      options?.excludeContainer ?? "",
    ],
    queryFn: () => getPortSuggestions(count, options),
    enabled: count > 0,
  });
}

/**
 * 查询数据目录列表
 */
export function useStorageDirectories() {
  return useQuery<StorageDirectory[]>({
    queryKey: ["settings", "storage-directories"],
    queryFn: listStorageDirectories,
  });
}

/**
 * 新建数据目录
 */
export function useCreateStorageDirectory() {
  const qc = useQueryClient();
  const toast = useToast();

  return useMutation({
    mutationFn: (data: StorageDirectoryCreate) => createStorageDirectory(data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["settings", "storage-directories"] });
      toast.success("创建成功");
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "创建失败");
    },
  });
}

/**
 * 更新数据目录
 */
export function useUpdateStorageDirectory() {
  const qc = useQueryClient();
  const toast = useToast();

  return useMutation({
    mutationFn: ({ id, data }: { id: number; data: StorageDirectoryUpdate }) =>
      updateStorageDirectory(id, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["settings", "storage-directories"] });
      toast.success("保存成功");
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "保存失败");
    },
  });
}

/**
 * 删除数据目录
 */
export function useDeleteStorageDirectory() {
  const qc = useQueryClient();
  const toast = useToast();

  return useMutation({
    mutationFn: (id: number) => deleteStorageDirectory(id),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["settings", "storage-directories"] });
      toast.success("删除成功");
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "删除失败");
    },
  });
}

/**
 * 查询宿主机指定路径下的目录列表（设置页专用）
 */
export function useSettingsDirectories(path: string, enabled = true) {
  return useQuery<DirectoryList>({
    queryKey: ["settings", "directories", path],
    queryFn: () => settingsDirectoriesApi.list(path),
    enabled: enabled && !!path,
    staleTime: 0,
  });
}

/**
 * 创建目录（设置端点）
 */
export function useSettingsCreateDirectory() {
  const qc = useQueryClient();
  const toast = useToast();

  return useMutation({
    mutationFn: (data: CreateDirectoryRequest) =>
      settingsDirectoriesApi.create(data),
    onSuccess: (_, vars) => {
      qc.invalidateQueries({
        queryKey: ["settings", "directories", vars.path],
      });
      toast.success("创建成功");
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "创建失败");
    },
  });
}

/**
 * 重命名目录（设置端点）
 */
export function useSettingsRenameDirectory() {
  const qc = useQueryClient();
  const toast = useToast();

  return useMutation({
    mutationFn: (data: RenameDirectoryRequest) =>
      settingsDirectoriesApi.rename(data),
    onSuccess: (_, vars) => {
      const parent = vars.old_path.replace(/\/[^/]*\/?$/, "") || "/";
      qc.invalidateQueries({
        queryKey: ["settings", "directories", parent],
      });
      toast.success("重命名成功");
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "重命名失败");
    },
  });
}

/**
 * 删除目录（设置端点）
 */
export function useSettingsDeleteDirectory() {
  const qc = useQueryClient();
  const toast = useToast();

  return useMutation({
    mutationFn: (data: DeleteDirectoryRequest) =>
      settingsDirectoriesApi.remove(data),
    onSuccess: (_, vars) => {
      const parent = vars.path.replace(/\/[^/]*\/?$/, "") || "/";
      qc.invalidateQueries({
        queryKey: ["settings", "directories", parent],
      });
      toast.success("删除成功");
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "删除失败");
    },
  });
}

/**
 * 检测代理对常用境外站点的连通性
 */
export function useTestProxy() {
  const toast = useToast();

  return useMutation({
    mutationFn: (data: {
      http_proxy?: string | null;
      https_proxy?: string | null;
    }) => testProxy(data),
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "检测失败");
    },
  });
}

/**
 * 从管理端获取启用中的镜像加速地址
 */
export function useRemoteDockerMirrors() {
  return useMutation({
    mutationFn: listRemoteDockerMirrors,
  });
}

/** 实际核验授权专属镜像源是否可用 */
export function useDockerMirrorStatus(enabled = true) {
  return useQuery({
    queryKey: ["settings", "docker-mirrors", "effectiveness"],
    queryFn: getDockerMirrorEffectiveness,
    enabled,
    refetchInterval: 60000,
  });
}

/**
 * 检测镜像加速地址的连通性
 */
export function useTestDockerMirrors() {
  return useMutation({
    mutationFn: testDockerMirrors,
  });
}

/**
 * 查询 Hosts 优选配置
 */
export function useHostsOptimizerConfig() {
  return useQuery<HostsOptimizerConfig>({
    queryKey: ["settings", "hosts-optimizer", "config"],
    queryFn: getHostsOptimizerConfig,
  });
}

/**
 * 保存 Hosts 优选配置
 */
export function useUpdateHostsOptimizerConfig() {
  const qc = useQueryClient();
  const toast = useToast();

  return useMutation({
    mutationFn: (data: HostsOptimizerConfig) => updateHostsOptimizerConfig(data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["settings", "hosts-optimizer"] });
      toast.success("保存成功");
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "保存失败");
    },
  });
}

/**
 * 查询 Hosts 优选运行状态（自动刷新）
 */
export function useHostsOptimizerStatus() {
  return useQuery<HostsOptimizerStatus>({
    queryKey: ["settings", "hosts-optimizer", "status"],
    queryFn: getHostsOptimizerStatus,
    refetchInterval: 15000,
  });
}

/**
 * 测试 AdGuard 连接
 */
export function useTestHostsOptimizer() {
  const toast = useToast();

  return useMutation({
    mutationFn: (data: {
      adguard_url?: string | null;
      adguard_username?: string;
      adguard_password?: string;
    }) => testHostsOptimizer(data),
    onSuccess: (result) => {
      if (result.ok) {
        toast.success(`AdGuard 连接成功${result.version ? `（${result.version}）` : ""}`);
      } else {
        toast.error(result.message || "AdGuard 连接失败");
      }
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "测试失败");
    },
  });
}

/**
 * 立即执行一次 Hosts 优选
 */
export function useRunHostsOptimizer() {
  const qc = useQueryClient();
  const toast = useToast();

  return useMutation({
    mutationFn: () => runHostsOptimizer(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["settings", "hosts-optimizer", "status"] });
      toast.success("优选完成");
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "优选失败");
    },
  });
}

/**
 * 查询应用加速、AdGuard 与管理端规则状态
 */
export function useHostsAccelerationOverview(enabled = true) {
  return useQuery<AccelerationOverview>({
    queryKey: ["settings", "hosts-optimizer", "acceleration"],
    queryFn: getHostsAccelerationOverview,
    enabled,
    refetchInterval: 30000,
  });
}

/**
 * 开启或关闭单个应用的 DNS 加速
 */
export function useToggleAppAcceleration() {
  const qc = useQueryClient();
  const toast = useToast();

  return useMutation({
    mutationFn: ({
      instanceId,
      enabled,
    }: {
      instanceId: number;
      enabled: boolean;
    }): Promise<AccelerationToggleResult> =>
      toggleAppAcceleration(instanceId, enabled),
    onSuccess: (result) => {
      qc.invalidateQueries({ queryKey: ["settings", "hosts-optimizer"] });
      qc.invalidateQueries({ queryKey: ["settings", "hosts-optimizer", "acceleration"] });
      if (result.warnings.length > 0) {
        toast.success(result.message);
        result.warnings.forEach((warning) => toast.error(warning));
      } else {
        toast.success(result.message);
      }
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "应用加速操作失败");
    },
  });
}

/**
 * 自动部署 OneTap 专用 AdGuard Home
 */
export function useDeployHostsAdguard() {
  const qc = useQueryClient();
  const toast = useToast();
  const { startTask } = useDeployTasks();

  return useMutation({
    mutationFn: deployHostsAdguard,
    onSuccess: (result) => {
      qc.invalidateQueries({ queryKey: ["settings", "hosts-optimizer"] });
      startTask(result.task_id);
      toast.success("部署任务已启动");
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "AdGuard Home 部署失败");
    },
  });
}

/**
 * 关闭所有应用加速并删除 OneTap 托管的 AdGuard Home
 */
export function useRemoveHostsAdguard() {
  const qc = useQueryClient();
  const toast = useToast();

  return useMutation({
    mutationFn: removeHostsAdguard,
    onSuccess: (result) => {
      qc.invalidateQueries({ queryKey: ["settings", "hosts-optimizer"] });
      toast.success(result.message || "AdGuard Home 已删除");
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "删除 AdGuard Home 失败");
    },
  });
}

/**
 * 从管理端同步加速域名规则
 */
export function useSyncHostsManagedRules() {
  const qc = useQueryClient();
  const toast = useToast();

  return useMutation({
    mutationFn: syncHostsManagedRules,
    onSuccess: (result) => {
      qc.invalidateQueries({ queryKey: ["settings", "hosts-optimizer"] });
      toast.success(`已同步 ${result.rules.length} 条加速规则`);
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "同步加速规则失败");
    },
  });
}
