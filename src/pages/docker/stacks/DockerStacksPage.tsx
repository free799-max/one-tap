/**
 * Docker Compose 编排管理页面
 *
 * 以卡片式布局展示所有 Compose 项目，支持：
 * - 创建/编辑/删除项目
 * - 版本管理
 * - 启动/停止/重启 Stack
 * - 查看日志与详情
 */

import { useState } from "react";
import {
  useComposeProjects,
  useComposeAction,
  useDeleteComposeProject,
} from "@/hooks/useCompose";
import type { ComposeProject } from "@/hooks/useCompose";
import { useDeployTasks } from "@/hooks/useDeployTasks";
import { StackGrid } from "./StackGrid";
import { StackEditorDialog } from "./StackEditorDialog";
import { StackVersionDialog } from "./StackVersionDialog";
import { StackLogsDialog } from "./StackLogsDialog";
import { StackDetailDialog } from "./StackDetailDialog";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";

export function DockerStacksPage() {
  const {
    data: projects,
    isLoading,
    refetch,
    isRefetching,
  } = useComposeProjects();

  const { startTask } = useDeployTasks();

  const [editorMode, setEditorMode] = useState<"create" | "edit" | null>(null);
  const [editProject, setEditProject] = useState<ComposeProject | null>(null);
  const [versionProjectId, setVersionProjectId] = useState<number | null>(null);
  const [logsProject, setLogsProject] = useState<ComposeProject | null>(null);
  const [detailProjectId, setDetailProjectId] = useState<number | null>(null);
  const [pendingProjectId, setPendingProjectId] = useState<number | null>(null);
  const [deleteProject, setDeleteProject] = useState<ComposeProject | null>(null);

  const actionMutation = useComposeAction();
  const deleteMutation = useDeleteComposeProject();

  const handleAction = (
    project: ComposeProject,
    action: "up" | "down" | "restart"
  ) => {
    setPendingProjectId(project.id);
    actionMutation.mutate(
      { projectId: project.id, action },
      {
        onSuccess: (response) => {
          startTask(response.task_id);
        },
        onSettled: () => setPendingProjectId(null),
      }
    );
  };

  const handleDelete = (project: ComposeProject) => {
    setDeleteProject(project);
  };

  const handleConfirmDelete = () => {
    if (!deleteProject) return;
    deleteMutation.mutate(deleteProject.id, {
      onSuccess: () => setDeleteProject(null),
      onError: () => setDeleteProject(null),
    });
  };

  const handleCreate = () => {
    setEditProject(null);
    setEditorMode("create");
  };

  const handleEdit = (project: ComposeProject) => {
    setEditProject(project);
    setEditorMode("edit");
  };

  const handleEditorClose = (open: boolean) => {
    if (!open) {
      setEditorMode(null);
      setEditProject(null);
    }
  };

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-64">
        <div className="animate-spin h-8 w-8 border-2 border-primary border-t-transparent rounded-full" />
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <StackGrid
        projects={projects || []}
        isLoading={isLoading}
        isRefetching={isRefetching}
        onCreate={handleCreate}
        onRefetch={() => refetch()}
        onViewDetail={(p) => setDetailProjectId(p.id)}
        onViewLogs={(p) => setLogsProject(p)}
        onViewVersions={(p) => setVersionProjectId(p.id)}
        onEdit={handleEdit}
        onDelete={handleDelete}
        onAction={handleAction}
        pendingProjectId={pendingProjectId}
      />

      <StackEditorDialog
        mode={editorMode || "create"}
        project={editProject}
        open={editorMode !== null}
        onOpenChange={handleEditorClose}
        onTaskCreated={(taskId) => {
          startTask(taskId);
        }}
      />

      <StackVersionDialog
        projectId={versionProjectId}
        open={!!versionProjectId}
        onOpenChange={(open) => {
          if (!open) setVersionProjectId(null);
        }}
      />

      <StackLogsDialog
        projectId={logsProject?.id ?? null}
        projectName={logsProject?.project_name ?? null}
        open={!!logsProject}
        onOpenChange={(open) => {
          if (!open) setLogsProject(null);
        }}
      />

      <StackDetailDialog
        projectId={detailProjectId}
        open={!!detailProjectId}
        onOpenChange={(open) => {
          if (!open) setDetailProjectId(null);
        }}
      />

      <ConfirmDialog
        open={!!deleteProject}
        onOpenChange={(open) => {
          if (!open && deleteMutation.isPending) return;
          if (!open) setDeleteProject(null);
        }}
        title="删除编排项目"
        description={
          deleteProject
            ? `确定删除编排项目${deleteProject.project_name}，将删除容器与网络？`
            : undefined
        }
        confirmText="删除"
        cancelText="取消"
        onConfirm={handleConfirmDelete}
        destructive
        isPending={deleteMutation.isPending}
      />
    </div>
  );
}
