/**
 * Compose 项目详情弹窗
 *
 * 展示项目基本信息、当前版本、Stack 服务状态。
 */

import { Loader2, RefreshCw, Play, Square, RotateCcw } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { StatusDot } from "../shared/StatusDot";
import {
  useComposeProject,
  useComposeStatus,
  useComposeProjectContainers,
} from "@/hooks/useCompose";
import { useContainerAction } from "@/hooks/useDocker";
import CodeMirror from "@uiw/react-codemirror";
import { yaml } from "@codemirror/lang-yaml";

/** 状态颜色 */
function statusVariant(status: string): string {
  switch (status) {
    case "running":
      return "bg-green-500/10 text-green-600 border-green-500/20";
    case "partial":
      return "bg-amber-500/10 text-amber-600 border-amber-500/20";
    case "exited":
    case "stopped":
      return "bg-slate-500/10 text-slate-600 border-slate-500/20";
    default:
      return "bg-muted text-muted-foreground border-border";
  }
}

/** 信息卡片项 */
function InfoItem({
  label,
  children,
  className,
}: {
  label: string;
  children: React.ReactNode;
  className?: string;
}) {
  return (
    <div className={className}>
      <div className="text-xs text-muted-foreground mb-1">{label}</div>
      <div className="text-sm">{children}</div>
    </div>
  );
}

export function StackDetailDialog({
  projectId,
  open,
  onOpenChange,
}: {
  projectId: number | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const { data: project, isLoading: projectLoading } = useComposeProject(projectId);
  const {
    data: status,
    isLoading: statusLoading,
    refetch: refetchStatus,
  } = useComposeStatus(projectId);
  const {
    data: containers = [],
    isLoading: containersLoading,
    refetch: refetchContainers,
  } = useComposeProjectContainers(projectId);
  const containerAction = useContainerAction();

  const handleContainerAction = (id: string, action: "start" | "stop" | "restart") => {
    // 容器操作只使容器列表缓存失效，这里同时刷新 Stack 状态与归属容器，
    // 让弹窗内的状态立即更新而不是等待下一次轮询
    containerAction.mutate(
      { id, action },
      {
        onSuccess: () => {
          refetchStatus();
          refetchContainers();
        },
      }
    );
  };

  const canControlContainer = (status: string) => {
    return status === "running" || status === "exited";
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-5xl sm:max-w-5xl w-[calc(100%-2rem)] max-h-[85vh] p-0 gap-0 flex flex-col overflow-hidden">
        <DialogHeader className="px-6 pt-6 pb-4 shrink-0">
          <DialogTitle>{project?.project_name || "项目详情"}</DialogTitle>
          <DialogDescription>
            {project?.description || ""}
          </DialogDescription>
        </DialogHeader>

        {projectLoading ? (
          <div className="flex items-center justify-center py-12">
            <Loader2 className="h-5 w-5 animate-spin mr-2" />
            加载中…
          </div>
        ) : project ? (
          <div className="flex-1 overflow-y-auto min-h-0 px-6 pb-6 space-y-5">
            {/* 基本信息 */}
            <div className="rounded-lg border bg-muted/20 p-4 space-y-3">
              <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
                <InfoItem label="CLI 项目名">
                  <span className="font-mono break-all">{project.project_name}</span>
                </InfoItem>
                <InfoItem label="状态">
                  <Badge
                    variant="outline"
                    className={`text-[10px] ${statusVariant(status?.status || "unknown")}`}
                  >
                    {status?.status || project.stack?.status || "unknown"}
                  </Badge>
                </InfoItem>
                <InfoItem label="服务运行数">
                  {status?.running_count ?? project.stack?.running_count ?? 0} /{" "}
                  {status?.service_count ?? project.stack?.service_count ?? 0}
                </InfoItem>
                <InfoItem label="当前版本">
                  {project.current_version
                    ? `v${project.current_version.version_number}`
                    : "-"}
                </InfoItem>
              </div>
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                <InfoItem label="Compose 文件">
                  <span
                    className="font-mono text-sm break-all"
                    title={project.config_files?.join(", ") || undefined}
                  >
                    {project.config_files?.join(", ") || "-"}
                  </span>
                </InfoItem>
                <InfoItem label="工作目录">
                  <span
                    className="font-mono text-sm break-all"
                    title={project.working_dir || undefined}
                  >
                    {project.working_dir || "-"}
                  </span>
                </InfoItem>
              </div>
            </div>

            {/* 端口映射 */}
            <div className="space-y-2">
              <span className="text-sm font-medium">端口映射</span>
              <div className="rounded-md border p-3">
                {(status?.ports?.length || project.stack?.ports?.length) ? (
                  <div className="flex flex-wrap gap-2">
                    {(status?.ports || project.stack?.ports || []).map((port, idx) => (
                      <Badge
                        key={idx}
                        variant="secondary"
                        className="font-mono text-xs font-normal"
                      >
                        {port}
                      </Badge>
                    ))}
                  </div>
                ) : (
                  <div className="text-sm text-muted-foreground text-center py-2">
                    暂无端口映射
                  </div>
                )}
              </div>
            </div>

            {/* 容器列表 */}
            <div className="space-y-2">
              <div className="flex items-center justify-between">
                <span className="text-sm font-medium block mb-3">容器列表</span>
                <Button
                  variant="outline"
                  size="sm"
                  onClick={() => {
                    refetchStatus();
                    refetchContainers();
                  }}
                  disabled={statusLoading || containersLoading}
                >
                  {statusLoading || containersLoading ? (
                    <Loader2 className="h-3.5 w-3.5 mr-1 animate-spin" />
                  ) : (
                    <RefreshCw className="h-3.5 w-3.5 mr-1" />
                  )}
                  刷新状态
                </Button>
              </div>
              {containersLoading ? (
                <div className="flex items-center justify-center py-8 border rounded-md">
                  <Loader2 className="h-5 w-5 animate-spin mr-2" />
                  加载中…
                </div>
              ) : containers.length === 0 ? (
                <div className="px-3 py-4 text-sm text-muted-foreground text-center border rounded-md">
                  暂无归属容器
                </div>
              ) : (
                <div className="border rounded-md overflow-hidden">
                  <Table className="table-fixed">
                    <TableHeader>
                      <TableRow>
                        <TableHead className="text-xs w-[30%]">名称</TableHead>
                        <TableHead className="text-xs w-[18%]">状态</TableHead>
                        <TableHead className="text-xs w-[42%]">镜像</TableHead>
                        <TableHead className="text-xs w-[10%]">操作</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {containers.map((c) => (
                        <TableRow key={c.id}>
                          <TableCell className="text-sm break-all whitespace-normal">
                            {c.name}
                          </TableCell>
                          <TableCell>
                            <div className="flex items-center gap-1.5 text-sm">
                              <StatusDot status={c.status} />
                              {c.state}
                            </div>
                          </TableCell>
                          <TableCell className="text-xs text-muted-foreground break-all whitespace-normal">
                            {c.image}
                          </TableCell>
                          <TableCell>
                            <div className="flex items-center gap-0.5">
                              {c.status !== "running" && (
                                <Button
                                  size="icon-xs"
                                  variant="ghost"
                                  className="h-6 w-6 p-0 text-muted-foreground hover:text-primary"
                                  onClick={() => handleContainerAction(c.id, "start")}
                                  disabled={containerAction.isPending || !canControlContainer(c.status)}
                                  title="启动"
                                >
                                  <Play className="size-3.5" />
                                </Button>
                              )}
                              {c.status === "running" && (
                                <Button
                                  size="icon-xs"
                                  variant="ghost"
                                  className="h-6 w-6 p-0 text-muted-foreground hover:text-primary"
                                  onClick={() => handleContainerAction(c.id, "stop")}
                                  disabled={containerAction.isPending}
                                  title="停止"
                                >
                                  <Square className="size-3.5" />
                                </Button>
                              )}
                              <Button
                                size="icon-xs"
                                variant="ghost"
                                className="h-6 w-6 p-0 text-muted-foreground hover:text-primary"
                                onClick={() => handleContainerAction(c.id, "restart")}
                                disabled={containerAction.isPending || c.status !== "running"}
                                title="重启"
                              >
                                <RotateCcw className="size-3.5" />
                              </Button>
                            </div>
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              )}
            </div>

            {/* 当前 YAML */}
            <div className="space-y-2">
              <span className="text-sm font-medium block mb-3">当前 YAML</span>
              <div className="border rounded-md overflow-hidden">
                <CodeMirror
                  value={project.current_version?.content || "未加载"}
                  height="auto"
                  extensions={[yaml()]}
                  editable={false}
                  className="text-sm"
                  basicSetup={{
                    lineNumbers: true,
                    highlightActiveLineGutter: false,
                    highlightActiveLine: false,
                    foldGutter: false,
                  }}
                />
              </div>
            </div>
          </div>
        ) : (
          <div className="py-8 text-center text-muted-foreground">项目不存在</div>
        )}

        <DialogFooter className="shrink-0 !-mx-0 !-mb-0">
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            关闭
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
