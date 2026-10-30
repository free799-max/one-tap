/**
 * 升级编排实例组
 *
 * 升级只更新应用镜像：挂载目录、端口、环境变量沿用当前实例配置；
 * 模板新增应用按默认配置部署，模板已移除应用保留运行。
 * 后端会在开始升级前保存当前配置快照。
 */

import { useEffect, useMemo, useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";
import { AppIcon } from "../apps/AppIcon";
import { useApps } from "@/hooks/useApps";
import { useDeployTasks } from "@/hooks/useDeployTasks";
import {
  useUpgradeOrchestrationInstance,
  type AppOrchestration,
  type OrchestrationInstanceDetail,
} from "@/hooks/useOrchestrations";
import { cn, formatVersion } from "@/lib/utils";
import {
  AlertTriangle,
  ArrowUpCircle,
  Loader2,
  ShieldCheck,
} from "lucide-react";

interface AutomationUpgradeDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  orchestration: AppOrchestration | null;
  group: OrchestrationInstanceDetail | null;
}

/** 从镜像地址提取标签用于展示；没有标签时退回完整镜像名。 */
function imageTag(image?: string | null): string | null {
  if (!image) return null;
  const lastSegment = image.split("/").pop() ?? "";
  const colon = lastSegment.indexOf(":");
  return colon >= 0 ? lastSegment.slice(colon + 1) : image;
}

export function AutomationUpgradeDialog({
  open,
  onOpenChange,
  orchestration,
  group,
}: AutomationUpgradeDialogProps) {
  const toast = useToast();
  const { startTask } = useDeployTasks();
  const { data: allApps = [] } = useApps();
  const upgradeMutation = useUpgradeOrchestrationInstance();
  const [selected, setSelected] = useState<Set<string>>(new Set());

  const appMap = useMemo(
    () => Object.fromEntries(allApps.map((app) => [app.name, app])),
    [allApps]
  );
  const deployedApps = useMemo(
    () => new Set(group?.apps.map((app) => app.app_name) ?? []),
    [group]
  );
  const deployedAppMap = useMemo(
    () => new Map((group?.apps ?? []).map((app) => [app.app_name, app])),
    [group]
  );
  const composition = useMemo(
    () => orchestration?.app_composition ?? [],
    [orchestration]
  );
  const removedApps = useMemo(
    () =>
      (group?.apps ?? []).filter(
        (app) => !composition.some((item) => item.app_name === app.app_name)
      ),
    [group, composition]
  );

  useEffect(() => {
    if (!open || !group || !orchestration) return;
    const initial = new Set<string>();
    for (const item of orchestration.app_composition) {
      if (deployedApps.has(item.app_name) || item.relation === "required") {
        initial.add(item.app_name);
      }
    }
    setSelected(initial);
  }, [open, group, orchestration, deployedApps]);

  const toggleApp = (appName: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(appName)) next.delete(appName);
      else next.add(appName);
      return next;
    });
  };

  const handleConfirm = () => {
    if (!group) return;
    if (selected.size === 0) {
      toast.error("请至少选择一个要升级的应用");
      return;
    }
    const payload = {
      instance_name: group.instance_name,
      selected_apps: Array.from(selected),
      // 已部署应用的配置由后端沿用，这里只用于模板新增应用的初始化
      app_configs: {},
      shared_config: {},
    };
    upgradeMutation.mutate(
      { id: group.id, data: payload },
      {
        onSuccess: (response) => {
          for (const taskId of response.task_ids) startTask(taskId);
          onOpenChange(false);
        },
      }
    );
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="w-[560px] max-w-[calc(100vw-2rem)] gap-0 rounded p-0">
        <DialogHeader className="border-b border-border px-5 pb-3.5 pt-5">
          <DialogTitle className="flex items-center gap-2 text-base font-bold">
            <ArrowUpCircle className="h-4 w-4 text-primary" />
            升级方案
          </DialogTitle>
          {group && (
            <p className="text-xs text-muted-foreground">
              {formatVersion(group.orchestration_version)}
              <span className="mx-1">→</span>
              {group.template_version || orchestration?.version
                ? formatVersion(group.template_version || orchestration?.version)
                : "最新"}
            </p>
          )}
        </DialogHeader>

        <div className="max-h-[60vh] space-y-4 overflow-auto px-5 py-4">
          {group?.template_changelog?.trim() ? (
            <div className="rounded-xl border border-primary/25 bg-primary/5 px-3 py-2.5">
              <div className="text-xs font-medium text-primary">本次更新内容</div>
              <div className="mt-1.5 whitespace-pre-wrap text-xs leading-relaxed text-muted-foreground">
                {group.template_changelog}
              </div>
            </div>
          ) : null}

          <div className="flex items-start gap-2 rounded-xl border border-emerald-500/25 bg-emerald-500/5 px-3 py-2.5 text-xs leading-relaxed text-muted-foreground">
            <ShieldCheck className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600 dark:text-emerald-400" />
            <p>
              升级只更新镜像，挂载目录、端口和环境变量保持当前配置不变。
              开始前会自动备份当前配置。
            </p>
          </div>

          {removedApps.length > 0 && (
            <div className="flex items-start gap-2 rounded-xl border border-amber-500/30 bg-amber-500/5 px-3 py-2.5 text-xs leading-relaxed text-muted-foreground">
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-600 dark:text-amber-400" />
              <p>
                以下应用已从新方案移除，升级时保留运行、不删除数据：
                {removedApps.map((app) => app.display_name).join("、")}
              </p>
            </div>
          )}

          <div className="space-y-2">
            <div className="text-xs font-medium text-muted-foreground">
              升级应用（{selected.size}/{composition.length}）
            </div>
            <div className="space-y-1.5">
              {composition.map((item) => {
                const app = appMap[item.app_name];
                const deployedApp = deployedAppMap.get(item.app_name);
                const deployed = deployedApps.has(item.app_name);
                const checked = selected.has(item.app_name);
                const currentTag = imageTag(deployedApp?.current_image);
                const targetTag = imageTag(deployedApp?.target_image);
                const versionHint = deployed
                  ? currentTag && targetTag
                    ? currentTag === targetTag
                      ? `镜像已是最新（${currentTag}）· 挂载与端口保持不变`
                      : `镜像 ${currentTag} → ${targetTag} · 挂载与端口保持不变`
                    : "保留现有挂载与端口，只更新镜像"
                  : targetTag
                    ? `新方案新增，按默认配置部署（${targetTag}）`
                    : "新方案新增，按默认配置部署";
                return (
                  <label
                    key={item.app_name}
                    className={cn(
                      "flex cursor-pointer items-center gap-3 rounded-xl border px-3 py-2.5 transition-colors",
                      checked
                        ? "border-primary/30 bg-primary/5"
                        : "border-border hover:bg-muted/40"
                    )}
                  >
                    <input
                      type="checkbox"
                      checked={checked}
                      onChange={() => toggleApp(item.app_name)}
                      className="h-4 w-4 accent-primary"
                    />
                    {app ? (
                      <AppIcon
                        app={app}
                        className="h-8 w-8 rounded-lg border border-border/60 bg-white"
                      />
                    ) : (
                      <div className="flex h-8 w-8 items-center justify-center rounded-lg border border-border/60 bg-muted text-xs text-muted-foreground">
                        {item.app_name.slice(0, 1).toUpperCase()}
                      </div>
                    )}
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-1.5">
                        <span className="truncate text-sm font-medium">
                          {app?.display_name || item.app_name}
                        </span>
                        {item.relation === "required" && (
                          <Badge variant="secondary" className="font-normal">
                            必选
                          </Badge>
                        )}
                      </div>
                      <p className="truncate text-xs text-muted-foreground">
                        {versionHint}
                      </p>
                    </div>
                  </label>
                );
              })}
            </div>
          </div>
        </div>

        <DialogFooter className="-mx-0 -mb-0 border-t border-border bg-muted/20 px-5 py-3.5">
          <Button
            type="button"
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={upgradeMutation.isPending}
          >
            取消
          </Button>
          <Button
            type="button"
            onClick={handleConfirm}
            disabled={upgradeMutation.isPending || selected.size === 0}
            className="gap-1.5"
          >
            {upgradeMutation.isPending ? (
              <Loader2 className="h-4 w-4 animate-spin" />
            ) : (
              <ArrowUpCircle className="h-3.5 w-3.5" />
            )}
            {upgradeMutation.isPending ? "升级中" : "开始升级"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
