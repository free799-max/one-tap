/**
 * Docker Compose 项目管理相关 React Query hooks
 *
 * - useComposeProjects / useComposeProject：项目查询
 * - useCreateComposeProject / useUpdateComposeProject / useEditComposeProject / useDeleteComposeProject：项目变更
 * - useComposeVersions / useRollbackComposeVersion：版本管理
 * - useComposeAction：up/down/restart
 * - useComposeStatus / useComposeProjectContainers：状态与归属容器
 */

import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { useToast } from "@/components/ui/toast";
import type { ApiError } from "@/api/types";
import type { ContainerInfo } from "@/api/docker";
import * as composeApi from "@/api/compose";
import type {
  ComposeVersion,
  ComposeStackStatus,
  ComposeProject,
  ComposeProjectCreate,
  ComposeProjectUpdate,
  ComposeEditRequest,
  ComposeActionRequest,
} from "@/api/compose";

/**
 * 查询 Compose 项目列表
 *
 * 默认 10 秒自动刷新，配合后端实时状态同步。
 */
export function useComposeProjects() {
  return useQuery<ComposeProject[]>({
    queryKey: ["compose", "projects"],
    queryFn: composeApi.listComposeProjects,
    refetchInterval: 10000,
  });
}

/**
 * 查询单个 Compose 项目详情
 */
export function useComposeProject(
  projectId: number | null,
  options?: { refetchInterval?: number | false }
) {
  return useQuery<ComposeProject>({
    queryKey: ["compose", "projects", projectId],
    queryFn: () => composeApi.getComposeProject(projectId!),
    enabled: !!projectId,
    refetchInterval: options?.refetchInterval ?? false,
  });
}

/**
 * 创建 Compose 项目（异步）
 */
export function useCreateComposeProject() {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: (data: ComposeProjectCreate) =>
      composeApi.createComposeProject(data),
    onSuccess: () => {
      // 刷新项目列表，让新项目及时出现
      qc.invalidateQueries({ queryKey: ["compose", "projects"] });
      toast.success("创建部署任务已启动");
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "创建编排项目失败");
    },
  });
}

/**
 * 更新 Compose 项目元数据
 */
export function useUpdateComposeProject(projectId: number) {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: (data: ComposeProjectUpdate) =>
      composeApi.updateComposeProject(projectId, data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["compose", "projects"] });
      qc.invalidateQueries({ queryKey: ["compose", "projects", projectId] });
      toast.success("项目信息更新成功");
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "更新项目信息失败");
    },
  });
}

/**
 * 编辑 Compose 项目并自动部署（异步）
 */
export function useEditComposeProject(projectId: number) {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: (data: ComposeEditRequest) =>
      composeApi.editComposeProject(projectId, data),
    onSuccess: () => {
      // 刷新项目列表与详情（含状态），避免展示旧版本信息
      qc.invalidateQueries({ queryKey: ["compose", "projects"] });
      qc.invalidateQueries({ queryKey: ["compose", "projects", projectId] });
      toast.success("编辑部署任务已启动");
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "保存并部署失败");
    },
  });
}

/**
 * 删除 Compose 项目
 */
export function useDeleteComposeProject() {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: (projectId: number) =>
      composeApi.deleteComposeProject(projectId),
    onMutate: async (projectId) => {
      await qc.cancelQueries({ queryKey: ["compose", "projects"] });
      const previous = qc.getQueryData<ComposeProject[]>(["compose", "projects"]);
      qc.setQueryData<ComposeProject[]>(
        ["compose", "projects"],
        (old) => old?.filter((p) => p.id !== projectId)
      );
      return { previous };
    },
    onError: (error: ApiError, _projectId: number, context) => {
      if (context?.previous) {
        qc.setQueryData<ComposeProject[]>(["compose", "projects"], context.previous);
      }
      toast.error(error.displayMessage || "删除编排项目失败");
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["compose", "projects"] });
      toast.success("编排项目已删除");
    },
  });
}

/**
 * 查询 Compose 版本列表
 */
export function useComposeVersions(projectId: number | null) {
  return useQuery<ComposeVersion[]>({
    queryKey: ["compose", "projects", projectId, "versions"],
    queryFn: () => composeApi.listComposeVersions(projectId!),
    enabled: !!projectId,
  });
}

/**
 * 切换 Compose 版本
 */
export function useRollbackComposeVersion(projectId: number) {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: (versionId: number) =>
      composeApi.rollbackComposeVersion(projectId, versionId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["compose", "projects"] });
      qc.invalidateQueries({
        queryKey: ["compose", "projects", projectId, "versions"],
      });
      qc.invalidateQueries({
        queryKey: ["compose", "projects", projectId, "status"],
      });
      toast.success("版本切换成功");
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "版本切换失败");
    },
  });
}

/**
 * 执行 Compose 操作（up/down/restart，异步）
 */
export function useComposeAction() {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: ({
      projectId,
      action,
    }: {
      projectId: number;
      action: ComposeActionRequest["action"];
    }) => composeApi.composeAction(projectId, action),
    onSuccess: (_, { projectId, action }) => {
      // 刷新项目列表与详情（含状态）及归属容器，及时反映 up/down/restart 结果
      const refreshCaches = () => {
        qc.invalidateQueries({ queryKey: ["compose", "projects"] });
        qc.invalidateQueries({ queryKey: ["compose", "projects", projectId] });
        qc.invalidateQueries({ queryKey: ["docker", "containers"] });
      };
      refreshCaches();
      // 操作是后台异步任务，POST 返回时还没真正执行完；补几次延迟刷新，
      // 避免用户提前关闭进度弹窗后，卡片状态一直停在操作前的值
      for (const delay of [3000, 8000, 15000]) {
        setTimeout(refreshCaches, delay);
      }
      const actionMap: Record<string, string> = {
        up: "启动",
        down: "停止",
        restart: "重启",
      };
      toast.success(`${actionMap[action] || action} 任务已启动`);
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "操作失败");
    },
  });
}

/**
 * 查询/同步 Compose Stack 状态
 */
export function useComposeStatus(projectId: number | null) {
  return useQuery<ComposeStackStatus>({
    queryKey: ["compose", "projects", projectId, "status"],
    queryFn: () => composeApi.getComposeStatus(projectId!),
    enabled: !!projectId,
    refetchInterval: 30000,
  });
}

/**
 * 查询 Compose 项目维护的容器列表
 *
 * 通过容器标签 nasdeck.compose.project 过滤归属容器。
 */
export function useComposeProjectContainers(projectId: number | null) {
  return useQuery<ContainerInfo[]>({
    queryKey: ["compose", "projects", projectId, "containers"],
    queryFn: () => composeApi.getComposeProjectContainers(projectId!),
    enabled: !!projectId,
    refetchInterval: 10000,
  });
}
