/**
 * Compose 项目卡片组件（重构版）
 *
 * 按容器管理页风格重构：
 * - 标题旁状态徽章按状态着色
 * - 信息网格：服务、版本、端口、创建时间
 * - 行内图标操作按钮 + 更多下拉
 */

import {
  Play,
  Square,
  RotateCcw,
  MoreHorizontal,
  FileText,
  History,
  Trash2,
  Info,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardFooter,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { ActionButton } from "../shared/ActionButton";
import { formatDate } from "@/lib/utils";
import { type ComposeProject } from "@/hooks/useCompose";
import type { ContainerInfo } from "@/hooks/useDocker";

interface ContainerChipConfig {
  chipClass: string;
}

/** 容器 chip 样式配置 */
function containerChipConfig(status: string): ContainerChipConfig {
  if (status === "running") {
    return {
      chipClass: "bg-green-50 text-green-700 border-green-200",
    };
  }
  if (status === "exited") {
    return {
      chipClass: "bg-red-50 text-red-700 border-red-200",
    };
  }
  return {
    chipClass: "bg-yellow-50 text-yellow-700 border-yellow-200",
  };
}

interface ComposeStatusConfig {
  label: string;
  dotColor: string;
  badgeClass: string;
}

/** Compose 状态配置：文字、指示点颜色、徽章样式 */
function composeStatusConfig(status: string): ComposeStatusConfig {
  switch (status) {
    case "running":
      return {
        label: "运行中",
        dotColor: "bg-green-500",
        badgeClass: "bg-green-50 text-green-700 border-green-200 hover:bg-green-50",
      };
    case "partial":
      return {
        label: "部分运行",
        dotColor: "bg-yellow-500",
        badgeClass: "bg-yellow-50 text-yellow-700 border-yellow-200 hover:bg-yellow-50",
      };
    case "exited":
      return {
        label: "已退出",
        dotColor: "bg-red-500",
        badgeClass: "bg-red-50 text-red-700 border-red-200 hover:bg-red-50",
      };
    case "stopped":
      return {
        label: "已停止",
        dotColor: "bg-red-500",
        badgeClass: "bg-red-50 text-red-700 border-red-200 hover:bg-red-50",
      };
    default:
      return {
        label: "未知",
        dotColor: "bg-muted-foreground",
        badgeClass: "bg-muted text-muted-foreground border-border hover:bg-muted",
      };
  }
}

interface StackCardProps {
  project: ComposeProject;
  containers?: ContainerInfo[];
  onViewDetail: (project: ComposeProject) => void;
  onViewLogs: (project: ComposeProject) => void;
  onViewVersions: (project: ComposeProject) => void;
  onEdit: (project: ComposeProject) => void;
  onDelete: (project: ComposeProject) => void;
  onAction: (project: ComposeProject, action: "up" | "down" | "restart") => void;
  isActionPending: boolean;
}

export function StackCard({
  project,
  containers = [],
  onViewDetail,
  onViewLogs,
  onViewVersions,
  onEdit,
  onDelete,
  onAction,
  isActionPending,
}: StackCardProps) {
  const stack = project.stack;
  const status = stack?.status || "unknown";
  const statusConfig = composeStatusConfig(status);
  const isRunning = status === "running";

  return (
    <Card className="flex flex-col h-full rounded-xl gap-1 pt-0">
      <CardHeader className="py-2 bg-muted/50">
        <div className="flex items-center justify-between gap-3">
          <CardTitle
            className="text-base font-semibold truncate"
            title={project.project_name}
          >
            {project.project_name}
          </CardTitle>
          <Badge
            variant="outline"
            className={`text-xs h-5 px-1.5 shrink-0 flex items-center ${statusConfig.badgeClass}`}
          >
            <span
              className={`w-1.5 h-1.5 rounded-full ${statusConfig.dotColor} mr-1.5`}
            />
            {statusConfig.label}
          </Badge>
        </div>
      </CardHeader>

      <CardContent className="flex-1 space-y-3 pt-1">
        {/* 信息网格 */}
        <div className="grid grid-cols-3 gap-x-4 gap-y-2 text-sm">
          <div className="space-y-0.5">
            <span className="text-xs text-muted-foreground">服务</span>
            <div>
              {stack?.running_count ?? 0} / {stack?.service_count ?? 0}
            </div>
          </div>
          <div className="space-y-0.5">
            <span className="text-xs text-muted-foreground">当前版本</span>
            <div>
              {project.current_version
                ? `v${project.current_version.version_number}`
                : "-"}
            </div>
          </div>
          <div className="space-y-0.5">
            <span className="text-xs text-muted-foreground">创建时间</span>
            <div className="text-xs">{formatDate(project.created_at)}</div>
          </div>
        </div>

        {/* 归属容器 chips */}
        <div className="flex flex-wrap items-center gap-1.5 py-2">
          {containers.length === 0 ? (
            <span className="text-xs text-muted-foreground/60">无运行容器</span>
          ) : (
            <>
              {containers.map((c) => {
                const chipConfig = containerChipConfig(c.status);
                return (
                  <div
                    key={c.id}
                    className={`inline-flex items-center gap-1 px-2 py-0.5 rounded-md text-xs border ${chipConfig.chipClass}`}
                    title={`${c.name} (${c.state})`}
                  >
                    <span className="truncate max-w-[8rem]">{c.name}</span>
                  </div>
                );
              })}
            </>
          )}
        </div>
      </CardContent>

      <CardFooter className="pt-2 pb-3 px-4 gap-1 flex-wrap border-t justify-end">
        {/* 主操作 */}
        {isRunning ? (
          <ActionButton
            icon={Square}
            title="停止"
            onClick={() => onAction(project, "down")}
            disabled={isActionPending}
          />
        ) : (
          <ActionButton
            icon={Play}
            title="启动"
            onClick={() => onAction(project, "up")}
            disabled={isActionPending}
          />
        )}
        <ActionButton
          icon={RotateCcw}
          title="重启"
          onClick={() => onAction(project, "restart")}
          disabled={isActionPending || !isRunning}
        />

        <div className="w-px h-4 bg-border mx-1" />

        {/* 次要操作 */}
        <ActionButton
          icon={FileText}
          title="查看日志"
          onClick={() => onViewLogs(project)}
        />
        <ActionButton
          icon={History}
          title="版本管理"
          onClick={() => onViewVersions(project)}
        />
        <ActionButton
          icon={Info}
          title="查看详情"
          onClick={() => onViewDetail(project)}
        />

        <div className="w-px h-4 bg-border mx-1" />

        {/* 更多操作 */}
        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              size="icon-xs"
              variant="ghost"
              className="h-6 w-6 p-0 text-muted-foreground hover:text-primary"
            >
              <MoreHorizontal className="size-4" />
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end">
            <DropdownMenuItem onClick={() => onEdit(project)}>
              编辑项目
            </DropdownMenuItem>
            <DropdownMenuSeparator />
            <DropdownMenuItem
              onClick={() => onDelete(project)}
              className="text-red-600 focus:text-red-600"
            >
              <Trash2 className="h-3.5 w-3.5 mr-2" />
              删除
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </CardFooter>
    </Card>
  );
}
