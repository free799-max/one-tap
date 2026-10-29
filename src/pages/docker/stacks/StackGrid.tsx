/**
 * Compose 项目卡片网格（重构版）
 *
 * 增加头部工具栏：标题/数量、搜索、刷新、创建。
 * 卡片按响应式网格排列。
 */

import { useMemo, useState } from "react";
import { Layers, Plus, RefreshCw, Search, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Card, CardContent } from "@/components/ui/card";
import { StackCard } from "./StackCard";
import { useContainers } from "@/hooks/useDocker";
import type { ComposeProject } from "@/hooks/useCompose";

const COMPOSE_PROJECT_LABEL = "com.docker.compose.project";

interface StackGridProps {
  projects: ComposeProject[];
  isLoading?: boolean;
  isRefetching?: boolean;
  onCreate: () => void;
  onRefetch?: () => void;
  onViewDetail: (project: ComposeProject) => void;
  onViewLogs: (project: ComposeProject) => void;
  onViewVersions: (project: ComposeProject) => void;
  onEdit: (project: ComposeProject) => void;
  onDelete: (project: ComposeProject) => void;
  onAction: (project: ComposeProject, action: "up" | "down" | "restart") => void;
  pendingProjectId: number | null;
}

export function StackGrid({
  projects,
  isLoading,
  isRefetching,
  onCreate,
  onRefetch,
  onViewDetail,
  onViewLogs,
  onViewVersions,
  onEdit,
  onDelete,
  onAction,
  pendingProjectId,
}: StackGridProps) {
  const [searchQuery, setSearchQuery] = useState("");
  const { data: allContainers = [] } = useContainers();

  const containersByProject = useMemo(() => {
    const map = new Map<string, typeof allContainers>();
    for (const c of allContainers) {
      const projectName = c.labels?.[COMPOSE_PROJECT_LABEL];
      if (!projectName) continue;
      if (!map.has(projectName)) {
        map.set(projectName, []);
      }
      map.get(projectName)!.push(c);
    }
    return map;
  }, [allContainers]);

  const filteredProjects = useMemo(() => {
    if (!searchQuery.trim()) return projects;
    const q = searchQuery.trim().toLowerCase();
    return projects.filter(
      (p) =>
        p.project_name.toLowerCase().includes(q) ||
        (p.description?.toLowerCase().includes(q) ?? false)
    );
  }, [projects, searchQuery]);

  return (
    <Card className="rounded-xl">
      <CardContent className="pt-1 pb-4 px-4 space-y-4">
        {projects.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 text-center">
            <p className="text-muted-foreground">暂无 Compose 编排项目</p>
            <Button onClick={onCreate} className="mt-4">
              <Plus className="h-4 w-4 mr-2" />
              创建编排项目
            </Button>
          </div>
        ) : (
          <>
            {/* 头部工具栏 */}
            <div className="flex items-center gap-3 flex-wrap">
              <div className="flex items-center gap-2">
                <Layers className="h-4 w-4 text-muted-foreground" />
                <h2 className="text-sm font-medium">编排项目</h2>
                <span className="text-xs text-muted-foreground">
                  共 {filteredProjects.length} 个
                </span>
              </div>

              <div className="flex items-center gap-2 flex-1 min-w-[180px]">
                <div className="relative w-56 shrink-0">
                  <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground/60 pointer-events-none" />
                  <Input
                    placeholder="搜索项目名称…"
                    value={searchQuery}
                    onChange={(e) => setSearchQuery(e.target.value)}
                    className="pl-8 pr-7 h-8 w-full rounded-full border border-black/25 bg-background text-sm shadow-none placeholder:text-muted-foreground/60 focus-visible:ring-0"
                    spellCheck={false}
                    autoComplete="off"
                  />
                  {searchQuery && (
                    <button
                      onClick={() => setSearchQuery("")}
                      className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground/60 hover:text-foreground"
                    >
                      <X className="h-4 w-4" />
                    </button>
                  )}
                </div>
              </div>

              <div className="flex items-center gap-2 ml-auto">
                <Button
                  size="sm"
                  variant="outline"
                  className="h-8 text-xs"
                  onClick={onRefetch}
                  disabled={isLoading || isRefetching}
                >
                  <RefreshCw
                    className={`h-3.5 w-3.5 mr-1 ${isRefetching ? "animate-spin" : ""}`}
                  />
                  刷新
                </Button>
                <Button size="sm" className="h-8 text-xs" onClick={onCreate}>
                  <Plus className="h-3.5 w-3.5 mr-1" />
                  创建
                </Button>
              </div>
            </div>

            {filteredProjects.length === 0 ? (
              <p className="text-muted-foreground text-center py-12 text-sm border rounded-xl bg-card">
                未找到匹配的项目
              </p>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
                {filteredProjects.map((project) => (
                  <StackCard
                    key={project.id}
                    project={project}
                    containers={containersByProject.get(project.project_name) || []}
                    onViewDetail={onViewDetail}
                    onViewLogs={onViewLogs}
                    onViewVersions={onViewVersions}
                    onEdit={onEdit}
                    onDelete={onDelete}
                    onAction={onAction}
                    isActionPending={pendingProjectId === project.id}
                  />
                ))}
              </div>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}
