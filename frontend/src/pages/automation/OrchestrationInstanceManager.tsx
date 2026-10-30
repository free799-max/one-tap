/**
 * 实例组树
 *
 * 按实例组组织应用：实例组为父节点，选中后展开组内应用。
 * 组节点右侧提供修复、重命名与删除操作。
 */

import { useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogFooter,
} from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import {
  AlertTriangle,
  ArrowUpCircle,
  MoreHorizontal,
  Pencil,
  Trash2,
  Wrench,
} from "lucide-react";
import { AppIcon } from "../apps/AppIcon";
import { cn, formatVersion } from "@/lib/utils";
import type { App } from "@/hooks/useApps";
import type {
  AppCompositionItem,
  OrchestrationInstanceApp,
  OrchestrationInstanceGroup,
} from "@/hooks/useOrchestrations";
import { APP_STATUS_META } from "./app-status";
import { getAppRoleLabel } from "./composition-meta";

interface OrchestrationInstanceManagerProps {
  instances: OrchestrationInstanceGroup[];
  selectedId: number | null;
  selectedAppId: number | null;
  appMap: Record<string, App>;
  composition?: AppCompositionItem[];
  onSelect: (id: number) => void;
  onSelectApp: (app: OrchestrationInstanceApp) => void;
  onRename: (id: number, name: string) => void;
  onDelete: (id: number) => void;
  onRedeploy: (id: number) => void;
  onUpgrade: (id: number) => void;
  isPending?: boolean;
}

/** 组内应用节点 */
function AppNode({
  app,
  appMap,
  composition,
  selected,
  onSelect,
}: {
  app: OrchestrationInstanceApp;
  appMap: Record<string, App>;
  composition: AppCompositionItem[];
  selected: boolean;
  onSelect: () => void;
}) {
  const meta = APP_STATUS_META[app.status] ?? APP_STATUS_META.stopped;
  const catalog = appMap[app.app_name];
  const role = getAppRoleLabel(
    composition.find((item) => item.app_name === app.app_name)
  );

  return (
    <button
      type="button"
      aria-current={selected ? "true" : undefined}
      onClick={onSelect}
      className={cn(
        "flex w-full items-center gap-2.5 rounded-lg border py-1.5 pl-2 pr-2 text-left transition-colors",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
        selected
          ? "border-border bg-card shadow-sm"
          : "border-transparent hover:bg-card/70"
      )}
    >
      <AppIcon
        app={
          {
            name: app.app_name,
            display_name: app.display_name,
            icon: app.icon,
          } as App
        }
        className="h-7 w-7 shrink-0 rounded-[24%] object-cover shadow-sm ring-1 ring-foreground/5"
      />

      <div className="min-w-0 flex-1">
        <div
          className="truncate text-sm font-medium text-foreground/90"
          title={catalog?.display_name || app.display_name}
        >
          {app.display_name}
        </div>
        <div className="mt-0.5 flex items-center gap-1 text-xs">
          <span className={cn("h-1.5 w-1.5 rounded-full", meta.dot)} />
          <span className={meta.text}>{meta.label}</span>
          {role && (
            <span className="truncate text-muted-foreground/70">· {role}</span>
          )}
        </div>
      </div>
    </button>
  );
}

export function OrchestrationInstanceManager({
  instances,
  selectedId,
  selectedAppId,
  appMap,
  composition = [],
  onSelect,
  onSelectApp,
  onRename,
  onDelete,
  onRedeploy,
  onUpgrade,
  isPending = false,
}: OrchestrationInstanceManagerProps) {
  const [renameInstance, setRenameInstance] =
    useState<OrchestrationInstanceGroup | null>(null);
  const [deleteInstance, setDeleteInstance] =
    useState<OrchestrationInstanceGroup | null>(null);
  const [renameValue, setRenameValue] = useState("");

  const handleOpenRename = (instance: OrchestrationInstanceGroup) => {
    setRenameInstance(instance);
    setRenameValue(instance.instance_name);
  };

  const handleConfirmRename = () => {
    if (!renameInstance || !renameValue.trim()) return;
    onRename(renameInstance.id, renameValue.trim());
    setRenameInstance(null);
  };

  const handleConfirmDelete = () => {
    if (!deleteInstance) return;
    onDelete(deleteInstance.id);
    setDeleteInstance(null);
  };

  if (instances.length === 0) {
    return null;
  }

  return (
    <>
      <div
        aria-label="实例组与应用"
        className="space-y-1"
      >
        {instances.map((instance) => {
          const expanded = instance.id === selectedId;
          const running = instance.apps.filter(
            (app) => app.status === "running"
          ).length;
          const total = instance.apps.length;
          const allRunning = total > 0 && running === total;

          return (
            <div key={instance.id}>
              <div
                className={cn(
                  "group/instance flex items-center gap-0.5 rounded-lg pr-1 transition-colors",
                  expanded ? "bg-muted/60" : "hover:bg-muted/40"
                )}
              >
                <button
                  type="button"
                  aria-expanded={expanded}
                  onClick={() => onSelect(instance.id)}
                  disabled={isPending}
                  className={cn(
                    "flex min-w-0 flex-1 items-center gap-1.5 rounded-lg px-2 py-2 text-left transition-colors",
                    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring",
                    expanded
                      ? "text-foreground"
                      : "text-foreground/75 hover:text-foreground"
                  )}
                >
                  <span className="flex min-w-0 flex-1 flex-col items-start gap-0.5">
                    <span className="flex w-full min-w-0 items-center gap-1.5">
                      <span
                        className={cn(
                          "truncate text-base tracking-tight",
                          expanded ? "font-semibold" : "font-medium"
                        )}
                        title={instance.instance_name}
                      >
                        {instance.instance_name}
                      </span>
                      <Badge
                        variant="outline"
                        className="h-4 shrink-0 px-1.5 py-0 text-[10px] font-normal tabular-nums text-muted-foreground"
                        title="实例部署时的方案版本"
                      >
                        {formatVersion(instance.orchestration_version)}
                      </Badge>
                    </span>
                  </span>
                  <span
                    className={cn(
                      "ml-auto flex shrink-0 items-center gap-1 rounded-full bg-background/80 px-2 py-0.5 text-xs font-medium tabular-nums ring-1 ring-border/50",
                      allRunning
                        ? "text-emerald-600 dark:text-emerald-400"
                        : "text-amber-600 dark:text-amber-400"
                    )}
                    title={`${running}/${total} 个应用运行中`}
                  >
                    <span
                      className={cn(
                        "h-1.5 w-1.5 rounded-full",
                        allRunning ? "bg-emerald-500" : "bg-amber-500"
                      )}
                    />
                    {running}/{total}
                  </span>
                </button>

                <DropdownMenu>
                  <DropdownMenuTrigger asChild>
                    <Button
                      variant="ghost"
                      size="icon"
                      className="shrink-0 text-muted-foreground/60 hover:text-foreground"
                      disabled={isPending}
                      aria-label={`${instance.instance_name} 操作`}
                      title="实例组操作"
                    >
                      <MoreHorizontal
                        className="h-[18px] w-[18px]"
                        strokeWidth={2.5}
                      />
                    </Button>
                  </DropdownMenuTrigger>
                  <DropdownMenuContent
                    align="end"
                    alignOffset={-16}
                    className="w-28 min-w-0 rounded-md p-1"
                  >
                    <DropdownMenuItem
                      onClick={() => onUpgrade(instance.id)}
                      className="gap-2 px-2 py-1.5 text-xs"
                    >
                      <ArrowUpCircle className="h-3.5 w-3.5" />
                      升级方案
                    </DropdownMenuItem>
                    <DropdownMenuItem
                      onClick={() => onRedeploy(instance.id)}
                      className="gap-2 px-2 py-1.5 text-xs"
                    >
                      <Wrench className="h-3.5 w-3.5" />
                      修复
                    </DropdownMenuItem>
                    <DropdownMenuItem
                      onClick={() => handleOpenRename(instance)}
                      className="gap-2 px-2 py-1.5 text-xs"
                    >
                      <Pencil className="h-3.5 w-3.5" />
                      重命名
                    </DropdownMenuItem>
                    <DropdownMenuItem
                      variant="destructive"
                      onClick={() => setDeleteInstance(instance)}
                      className="gap-2 px-2 py-1.5 text-xs"
                    >
                      <Trash2 className="h-3.5 w-3.5" />
                      删除
                    </DropdownMenuItem>
                  </DropdownMenuContent>
                </DropdownMenu>
              </div>

              {expanded && (
                <div
                  aria-label={`${instance.instance_name} 应用`}
                  className="mb-1 ml-3"
                >
                  {instance.apps.length === 0 ? (
                    <p className="px-2 py-2.5 text-xs text-muted-foreground/80">
                      该实例组暂无应用
                    </p>
                  ) : (
                    <div className="space-y-0.5 pt-0.5">
                      {instance.apps.map((app) => (
                        <AppNode
                          key={app.id}
                          app={app}
                          appMap={appMap}
                          composition={composition}
                          selected={app.id === selectedAppId}
                          onSelect={() => onSelectApp(app)}
                        />
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>

      <Dialog
        open={!!renameInstance}
        onOpenChange={() => setRenameInstance(null)}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>重命名实例组</DialogTitle>
          </DialogHeader>
          <div className="py-2">
            <Input
              value={renameValue}
              onChange={(e) => setRenameValue(e.target.value)}
              placeholder="实例组名称"
              autoFocus
            />
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setRenameInstance(null)}
              disabled={isPending}
            >
              取消
            </Button>
            <Button
              onClick={handleConfirmRename}
              disabled={!renameValue.trim() || isPending}
            >
              保存
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog
        open={!!deleteInstance}
        onOpenChange={() => setDeleteInstance(null)}
      >
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <AlertTriangle className="h-5 w-5 text-destructive" />
              删除实例组
            </DialogTitle>
          </DialogHeader>
          <div className="space-y-3 py-2 text-sm text-muted-foreground">
            <p>
              确认删除实例组
              <span className="font-medium text-foreground">
                「{deleteInstance?.instance_name}」
              </span>
              ？删除后可通过「恢复」找回应用数据。
            </p>

            {deleteInstance && deleteInstance.apps.length > 0 && (
              <div className="flex flex-wrap gap-1.5">
                {deleteInstance.apps.map((app) => (
                  <span
                    key={app.id}
                    className="inline-flex items-center gap-1.5 rounded-full border bg-muted/40 px-2 py-1 text-xs text-foreground/80"
                  >
                    <AppIcon
                      app={
                        {
                          name: app.app_name,
                          display_name: app.display_name,
                          icon: app.icon,
                        } as App
                      }
                      className="h-4 w-4"
                    />
                    {app.display_name}
                  </span>
                ))}
              </div>
            )}

            <div className="space-y-2 rounded-lg border border-destructive/25 bg-destructive/5 px-3 py-2.5 text-xs leading-relaxed">
              <p className="font-medium text-destructive">删除说明</p>
              <ul className="list-disc space-y-1 pl-4">
                <li>容器和 Docker Compose 项目会被停止并移除。</li>
                <li>
                  各应用的数据目录会移入备份目录，不会直接删除，之后可通过
                  「恢复」找回。
                </li>
                <li>共享的媒体、下载文件不受影响。</li>
                <li>删除在后台执行，可在弹出的任务进度中查看。</li>
              </ul>
            </div>
          </div>
          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setDeleteInstance(null)}
              disabled={isPending}
            >
              取消
            </Button>
            <Button
              variant="destructive"
              onClick={handleConfirmDelete}
              disabled={isPending}
            >
              删除
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </>
  );
}
