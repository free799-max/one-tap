/**
 * 应用编排（自动化组合模板）相关 React Query hooks
 */

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useToast } from "@/components/ui/toast";
import { useDeployTasks } from "@/hooks/useDeployTasks";
import type { ApiError } from "@/api/types";
import * as orchestrationsApi from "@/api/orchestrations";
import type {
  AppOrchestration,
  DeployOrchestrationRequest,
  ImportCandidateApp,
  OrchestrationImportRequest,
  OrchestrationInstanceGroup,
  OrchestrationInstanceDetail,
  OrchestrationInstanceUpdatePayload,
  OrchestrationBackup,
  RestoreOrchestrationRequest,
  AppAuthVerifyRequest,
  AppInspectReport,
  AppPluginsResponse,
  AppAssetsStatus,
  OrchestrationUpdateEventsParams,
  OrchestrationUpdateEvent,
  OrchestrationSyncStatus,
  OrchestrationSyncResult,
  MusicAutoOrganizePanel,
  MusicAutoOrganizeConfig,
  ReaderAutoSyncPanel,
  ReaderAutoSyncConfig,
} from "@/api/orchestrations";

// 保持既有导出，页面侧 import 路径无需变更
export type {
  AppCompositionItem,
  AppOrchestration,
  DeployOrchestrationRequest,
  DeployOrchestrationResponse,
  ContainerMatch,
  ImportCandidateApp,
  OrchestrationImportAppConfig,
  OrchestrationImportRequest,
  OrchestrationImportResponse,
  OrchestrationInstanceApp,
  OrchestrationInstanceGroup,
  OrchestrationInstanceDetail,
  OrchestrationInstanceUpdatePayload,
  OrchestrationBackup,
  OrchestrationBackupApp,
  RestoreOrchestrationRequest,
  AppAuthVerifyRequest,
  AppAuthVerifyResponse,
  InspectItemStatus,
  AppInspectItem,
  AppInspectReport,
  AppFixResultItem,
  AppFixResponse,
  AppPlugin,
  AppPluginsResponse,
  AppAssetsStatus,
  IntegrationRecord,
  AppIntegrations,
  OrchestrationUpdateEventScope,
  OrchestrationUpdateEventMode,
  OrchestrationUpdateEventStatus,
  OrchestrationUpdateEventDetail,
  OrchestrationUpdateEventsParams,
  OrchestrationUpdateEvent,
  OrchestrationSyncResult,
  OrchestrationSyncStatus,
  MusicAutoOrganizePanel,
  MusicAutoOrganizeConfig,
  MusicAutoOrganizeLastRun,
  MusicAutoDirItem,
  ReaderAutoSyncPanel,
  ReaderAutoSyncConfig,
  ReaderAutoSyncLastRun,
  ReaderAutoSyncDetected,
} from "@/api/orchestrations";

/**
 * 查询应用编排列表，支持分类筛选
 */
export function useOrchestrations(category?: string) {
  return useQuery<AppOrchestration[]>({
    queryKey: ["orchestrations", { category }],
    queryFn: () => orchestrationsApi.listOrchestrations(category),
  });
}

/**
 * 查询单个应用编排详情
 */
export function useOrchestration(name: string) {
  return useQuery<AppOrchestration>({
    queryKey: ["orchestrations", name],
    queryFn: () => orchestrationsApi.getOrchestration(name),
    enabled: !!name,
  });
}

/**
 * 查询编排实例组列表，支持分类筛选
 */
export function useOrchestrationInstances(category?: string) {
  return useQuery<OrchestrationInstanceGroup[]>({
    queryKey: ["orchestration-instances", { category }],
    queryFn: () => orchestrationsApi.listOrchestrationInstances(category),
  });
}

/**
 * 查询单个编排实例组详情
 */
export function useOrchestrationInstanceDetail(instanceId: number | null) {
  return useQuery<OrchestrationInstanceDetail>({
    queryKey: ["orchestration-instances", instanceId, "detail"],
    queryFn: () =>
      orchestrationsApi.getOrchestrationInstanceDetail(instanceId!),
    enabled: !!instanceId,
  });
}

/**
 * 更新编排实例组信息
 */
export function useUpdateOrchestrationInstance() {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: ({
      id,
      data,
    }: {
      id: number;
      data: OrchestrationInstanceUpdatePayload;
    }) => orchestrationsApi.updateOrchestrationInstance(id, data),
    onSuccess: (_data, variables) => {
      qc.invalidateQueries({ queryKey: ["orchestration-instances"] });
      qc.invalidateQueries({
        queryKey: ["orchestration-instances", variables.id, "detail"],
      });
      toast.success("实例组已更新");
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "更新失败");
    },
  });
}

/**
 * 删除编排实例组（后台任务）
 *
 * 返回任务 ID，由调用方接入全局任务进度弹窗。
 */
export function useDeleteOrchestrationInstance() {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: (id: number) =>
      orchestrationsApi.deleteOrchestrationInstance(id),
    onSuccess: () => {
      // 删除仍在后台进行，完成后由进度弹窗统一刷新实例列表
      qc.invalidateQueries({ queryKey: ["orchestration-backups"] });
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "删除失败");
    },
  });
}

/**
 * 查询可恢复的实例组备份
 */
export function useOrchestrationBackups(
  orchestrationName: string | null,
  enabled = true
) {
  return useQuery<OrchestrationBackup[]>({
    queryKey: ["orchestration-backups", orchestrationName],
    queryFn: () =>
      orchestrationsApi.listOrchestrationBackups(orchestrationName ?? undefined),
    enabled: enabled && !!orchestrationName,
  });
}

/**
 * 从备份恢复实例组
 */
export function useRestoreOrchestrationBackup() {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: (data: RestoreOrchestrationRequest) =>
      orchestrationsApi.restoreOrchestrationBackup(data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["compose", "projects"] });
      qc.invalidateQueries({ queryKey: ["apps", "instances"] });
      qc.invalidateQueries({ queryKey: ["orchestration-instances"] });
      qc.invalidateQueries({ queryKey: ["orchestration-backups"] });
      toast.success("恢复已启动，正在重新部署");
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "恢复失败");
    },
  });
}

/**
 * 查询可导入的 Docker 容器候选
 */
export function useImportCandidates(name: string) {
  return useQuery<ImportCandidateApp[]>({
    queryKey: ["orchestrations", name, "import-candidates"],
    queryFn: () => orchestrationsApi.getImportCandidates(name),
    enabled: !!name,
  });
}

/**
 * 导入已有 Docker 部署为编排实例
 */
export function useImportOrchestration() {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: ({
      name,
      data,
    }: {
      name: string;
      data: OrchestrationImportRequest;
    }) => orchestrationsApi.importOrchestration(name, data),
    onSuccess: (_data, variables) => {
      qc.invalidateQueries({ queryKey: ["apps", "instances"] });
      qc.invalidateQueries({ queryKey: ["orchestrations"] });
      qc.invalidateQueries({
        queryKey: ["orchestration-instances"],
      });
      qc.invalidateQueries({
        queryKey: ["orchestrations", variables.name, "import-candidates"],
      });
      toast.success("导入成功");
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "导入失败");
    },
  });
}

/**
 * 验证应用访问地址与认证信息
 */
export function useVerifyAppAuth() {
  const toast = useToast();
  return useMutation({
    mutationFn: (data: AppAuthVerifyRequest) =>
      orchestrationsApi.verifyAppAuth(data),
    onSuccess: (data) => {
      if (data.valid) {
        toast.success(data.message || "认证检测通过");
      } else {
        toast.error(data.message || "认证检测失败");
      }
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "认证检测请求失败");
    },
  });
}

/**
 * 组合部署应用编排
 */
export function useDeployOrchestration() {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: ({
      name,
      data,
    }: {
      name: string;
      data: DeployOrchestrationRequest;
    }) => orchestrationsApi.deployOrchestration(name, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["compose", "projects"] });
      qc.invalidateQueries({ queryKey: ["apps", "instances"] });
      qc.invalidateQueries({ queryKey: ["orchestration-instances"] });
      toast.success("组合部署已启动");
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "部署失败");
    },
  });
}

/**
 * 修复编排实例组（重新部署失败/卡死的应用）
 */
export function useRedeployOrchestrationInstance() {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: ({
      id,
      data,
    }: {
      id: number;
      data: DeployOrchestrationRequest;
    }) => orchestrationsApi.redeployOrchestrationInstance(id, data),
    onSuccess: (_data, variables) => {
      qc.invalidateQueries({ queryKey: ["compose", "projects"] });
      qc.invalidateQueries({ queryKey: ["apps", "instances"] });
      qc.invalidateQueries({ queryKey: ["orchestration-instances"] });
      qc.invalidateQueries({
        queryKey: ["orchestration-instances", variables.id, "detail"],
      });
      toast.success("修复已启动");
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "修复失败");
    },
  });
}

/**
 * 升级编排实例组到最新方案版本
 */
export function useUpgradeOrchestrationInstance() {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: ({
      id,
      data,
    }: {
      id: number;
      data: DeployOrchestrationRequest;
    }) => orchestrationsApi.upgradeOrchestrationInstance(id, data),
    onSuccess: (_data, variables) => {
      qc.invalidateQueries({ queryKey: ["compose", "projects"] });
      qc.invalidateQueries({ queryKey: ["apps", "instances"] });
      qc.invalidateQueries({ queryKey: ["orchestration-instances"] });
      qc.invalidateQueries({
        queryKey: ["orchestration-instances", variables.id, "detail"],
      });
      qc.invalidateQueries({ queryKey: ["orchestration-update-events"] });
      toast.success("已开始升级");
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "升级失败");
    },
  });
}

/**
 * 查询方案与实例的更新记录
 */
export function useUpdateEvents(
  params: OrchestrationUpdateEventsParams = {},
  enabled = true
) {
  return useQuery<OrchestrationUpdateEvent[]>({
    queryKey: ["orchestration-update-events", params],
    queryFn: () => orchestrationsApi.listUpdateEvents(params),
    enabled,
    retry: 1,
  });
}

/**
 * 查询最近一次方案同步状态
 */
export function useSyncStatus(enabled = true) {
  return useQuery<OrchestrationSyncStatus>({
    queryKey: ["orchestration-sync-status"],
    queryFn: () => orchestrationsApi.getSyncStatus(),
    enabled,
    staleTime: 30_000,
    retry: false,
  });
}

/**
 * 手动触发方案目录同步
 */
export function useManualSync() {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation<OrchestrationSyncResult, ApiError>({
    mutationFn: () => orchestrationsApi.syncOrchestrations(),
    onSuccess: (data) => {
      if (data?.skipped) {
        toast.success("同步已跳过：未配置管理端地址");
      } else {
        const created = data?.created ?? 0;
        const updated = data?.updated ?? 0;
        const removed = data?.removed ?? 0;
        toast.success(
          `同步完成：新增 ${created} 个，更新 ${updated} 个${
            removed ? `，清理 ${removed} 个重复方案` : ""
          }`
        );
      }
      qc.invalidateQueries({ queryKey: ["orchestrations"] });
      qc.invalidateQueries({ queryKey: ["orchestration-instances"] });
      qc.invalidateQueries({ queryKey: ["orchestration-update-events"] });
      qc.invalidateQueries({ queryKey: ["orchestration-sync-status"] });
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "同步失败");
      // 同步失败时后端也会记录 last_error，刷新状态以便面板展示
      qc.invalidateQueries({ queryKey: ["orchestration-sync-status"] });
    },
  });
}

/**
 * 手动重跑应用间自动装配（后台任务）
 *
 * 装配结果写入实例详情的 app_configs.integrations，这里在启动后
 * 延迟两次刷新详情查询，让装配结果自动出现在界面上。
 */
export function useWireOrchestrationInstance() {
  const qc = useQueryClient();
  const toast = useToast();
  const { startTask } = useDeployTasks();
  return useMutation({
    mutationFn: (id: number) =>
      orchestrationsApi.wireOrchestrationInstance(id),
    onSuccess: (data, id) => {
      // 装配可能等待服务启动数分钟，接入全局进度弹窗让过程可见
      if (data.task_id) {
        startTask(data.task_id);
      }
      toast.success("装配已在后台开始，进度可在任务面板查看");
      for (const delay of [4000, 10000]) {
        setTimeout(() => {
          qc.invalidateQueries({
            queryKey: ["orchestration-instances", id, "detail"],
          });
        }, delay);
      }
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "装配启动失败");
    },
  });
}

/**
 * 应用配置健康检测
 *
 * 检测为实时调用后端应用 API、耗时数秒，因此默认不自动执行，
 * 由面板通过 refetch() 手动触发；使用独立顶层 queryKey，避免
 * orchestration-instances 的宽泛失效导致误触发重新检测。
 */
export function useAppInspect(
  instanceId: number | null,
  appName: string | null
) {
  return useQuery<AppInspectReport>({
    queryKey: ["orchestration-inspect", instanceId, appName],
    queryFn: () =>
      orchestrationsApi.inspectOrchestrationApp(instanceId!, appName!),
    enabled: false,
    gcTime: 0,
    retry: false,
  });
}

/**
 * 检测项自动修复，成功后 toast 汇总结果；面板在 onSuccess 中重新检测
 */
export function useFixAppInspect() {
  const toast = useToast();
  return useMutation({
    mutationFn: ({
      instanceId,
      appName,
      itemIds,
    }: {
      instanceId: number;
      appName: string;
      itemIds: string[];
    }) =>
      orchestrationsApi.fixOrchestrationAppInspect(
        instanceId,
        appName,
        itemIds
      ),
    onSuccess: (data) => {
      const okCount = data.results.filter((r) => r.ok).length;
      const failCount = data.results.length - okCount;
      const firstError = data.results.find((r) => !r.ok)?.message;
      if (failCount === 0) {
        toast.success(`修复完成：${okCount} 项全部成功`);
      } else {
        toast.error(
          `修复完成：成功 ${okCount} 项，失败 ${failCount} 项${
            firstError ? `（${firstError}）` : ""
          }`
        );
      }
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "修复失败");
    },
  });
}

/**
 * 查询应用已安装插件与插件市场列表（当前仅 MoviePilot 支持）
 *
 * 接口依赖应用在线且已认证，失败时由面板展示错误提示。
 */
export function useAppPlugins(
  instanceId: number | null,
  appName: string | null
) {
  return useQuery<AppPluginsResponse>({
    queryKey: ["orchestration-app-plugins", instanceId, appName],
    queryFn: () =>
      orchestrationsApi.listOrchestrationAppPlugins(instanceId!, appName!),
    enabled: !!instanceId && !!appName,
    retry: false,
  });
}

/**
 * 安装应用插件（当前仅 MoviePilot 支持）
 */
export function useInstallAppPlugin() {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: ({
      instanceId,
      appName,
      pluginId,
      repoUrl,
    }: {
      instanceId: number;
      appName: string;
      pluginId: string;
      repoUrl?: string;
    }) =>
      orchestrationsApi.installOrchestrationAppPlugin(instanceId, appName, {
        plugin_id: pluginId,
        repo_url: repoUrl,
      }),
    onSuccess: (_data, variables) => {
      qc.invalidateQueries({
        queryKey: [
          "orchestration-app-plugins",
          variables.instanceId,
          variables.appName,
        ],
      });
      toast.success("插件安装成功");
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "插件安装失败");
    },
  });
}

/**
 * 应用自动整理配置与运行状态
 *
 * 运行中自动短轮询，结束后停止；配置保存在实例组 app_configs，
 * 因此重新打开页面或重启后端都能保留。
 */
export function useAppAutoOrganize(
  instanceId: number | null,
  appName: string | null
) {
  return useQuery<MusicAutoOrganizePanel>({
    queryKey: ["orchestration-auto-organize", instanceId, appName],
    queryFn: () =>
      orchestrationsApi.getAppAutoOrganize(instanceId!, appName!),
    enabled: !!instanceId && !!appName,
    retry: false,
    refetchInterval: (query) =>
      query.state.data?.running ? 3000 : false,
  });
}

/** 保存自动整理配置 */
export function useSaveAppAutoOrganize() {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: ({
      instanceId,
      appName,
      config,
    }: {
      instanceId: number;
      appName: string;
      config: MusicAutoOrganizeConfig;
    }) =>
      orchestrationsApi.saveAppAutoOrganize(
        instanceId,
        appName,
        config
      ),
    onSuccess: (data, variables) => {
      qc.setQueryData(
        [
          "orchestration-auto-organize",
          variables.instanceId,
          variables.appName,
        ],
        data
      );
      // 同步详情缓存，避免随后保存认证信息时用旧 app_configs 覆盖自动整理配置
      qc.setQueryData<OrchestrationInstanceDetail>(
        ["orchestration-instances", variables.instanceId, "detail"],
        (previous) =>
          previous
            ? {
                ...previous,
                app_configs: {
                  ...previous.app_configs,
                  [variables.appName]: {
                    ...(previous.app_configs?.[variables.appName] ?? {}),
                    auto_organize: data.config,
                  },
                },
              }
            : previous
      );
      qc.invalidateQueries({
        queryKey: ["orchestration-instances", variables.instanceId, "detail"],
      });
      toast.success("自动整理配置已保存");
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "保存自动整理配置失败");
    },
  });
}

/** 立即执行一轮自动整理 */
export function useRunAppAutoOrganize() {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: ({
      instanceId,
      appName,
    }: {
      instanceId: number;
      appName: string;
    }) => orchestrationsApi.runAppAutoOrganize(instanceId, appName),
    onSuccess: (_data, variables) => {
      qc.invalidateQueries({
        queryKey: [
          "orchestration-auto-organize",
          variables.instanceId,
          variables.appName,
        ],
      });
      toast.success("自动整理已开始，进度会在面板中刷新");
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "启动自动整理失败");
    },
  });
}

/**
 * Reader 自动同步配置与运行状态
 *
 * 运行中自动短轮询；配置保存在实例组 app_configs["reader"].auto_sync，
 * 由后端定时导出 Epub 并触发二毛扫描。
 */
export function useAppAutoSync(
  instanceId: number | null,
  appName: string | null
) {
  return useQuery<ReaderAutoSyncPanel>({
    queryKey: ["orchestration-auto-sync", instanceId, appName],
    queryFn: () => orchestrationsApi.getAppAutoSync(instanceId!, appName!),
    enabled: !!instanceId && !!appName,
    retry: false,
    refetchInterval: (query) =>
      query.state.data?.running ? 3000 : false,
  });
}

/** 保存 Reader 自动同步配置 */
export function useSaveAppAutoSync() {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: ({
      instanceId,
      appName,
      config,
    }: {
      instanceId: number;
      appName: string;
      config: ReaderAutoSyncConfig;
    }) => orchestrationsApi.saveAppAutoSync(instanceId, appName, config),
    onSuccess: (data, variables) => {
      qc.setQueryData(
        ["orchestration-auto-sync", variables.instanceId, variables.appName],
        data
      );
      // 同步详情缓存，避免随后保存认证信息时用旧 app_configs 覆盖自动同步配置
      qc.setQueryData<OrchestrationInstanceDetail>(
        ["orchestration-instances", variables.instanceId, "detail"],
        (previous) =>
          previous
            ? {
                ...previous,
                app_configs: {
                  ...previous.app_configs,
                  [variables.appName]: {
                    ...(previous.app_configs?.[variables.appName] ?? {}),
                    auto_sync: data.config,
                  },
                },
              }
            : previous
      );
      qc.invalidateQueries({
        queryKey: ["orchestration-instances", variables.instanceId, "detail"],
      });
      toast.success("自动同步配置已保存");
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "保存自动同步配置失败");
    },
  });
}

/** 立即执行一轮 Reader 自动同步 */
export function useRunAppAutoSync() {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: ({
      instanceId,
      appName,
    }: {
      instanceId: number;
      appName: string;
    }) => orchestrationsApi.runAppAutoSync(instanceId, appName),
    onSuccess: (_data, variables) => {
      qc.invalidateQueries({
        queryKey: [
          "orchestration-auto-sync",
          variables.instanceId,
          variables.appName,
        ],
      });
      toast.success("自动同步已开始，进度会在面板中刷新");
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "启动自动同步失败");
    },
  });
}

/**
 * 查询应用离线资源（插件依赖/浏览器内核）的拷贝状态
 *
 * 状态来自容器内的完成标记，容器重建后会重新变成“未拷贝”。
 */
export function useAppAssets(
  instanceId: number | null,
  appName: string | null
) {
  return useQuery<AppAssetsStatus>({
    queryKey: ["orchestration-app-assets", instanceId, appName],
    queryFn: () =>
      orchestrationsApi.getOrchestrationAppAssets(instanceId!, appName!),
    enabled: !!instanceId && !!appName,
    retry: false,
  });
}

/**
 * 把离线资源拷贝到应用容器（后台任务）
 *
 * 拷贝可能需要停止/启动容器，耗时较长，接入全局任务进度弹窗；
 * 完成后延迟刷新若干次状态，让“已拷贝”及时出现。
 */
export function useCopyAppAssets() {
  const qc = useQueryClient();
  const toast = useToast();
  const { startTask } = useDeployTasks();
  return useMutation({
    mutationFn: ({
      instanceId,
      appName,
    }: {
      instanceId: number;
      appName: string;
    }) => orchestrationsApi.copyOrchestrationAppAssets(instanceId, appName),
    onSuccess: (data, variables) => {
      if (data.task_id) {
        startTask(data.task_id);
      }
      toast.success("离线数据拷贝已开始，可在任务面板查看进度");
      for (const delay of [6000, 20000, 45000, 90000, 150000, 210000]) {
        setTimeout(() => {
          qc.invalidateQueries({
            queryKey: [
              "orchestration-app-assets",
              variables.instanceId,
              variables.appName,
            ],
          });
        }, delay);
      }
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "拷贝离线数据失败");
    },
  });
}
