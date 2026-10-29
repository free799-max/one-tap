/**
 * 从删除备份恢复自动化实例组
 *
 * 列出删除实例组时生成的应用数据备份，选择后把数据目录移回并按
 * 备份中记录的配置重新部署（沿用原账号密码，保证旧数据可登录）。
 */

import { useEffect, useMemo, useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useToast } from "@/components/ui/toast";
import {
  useOrchestrationBackups,
  useRestoreOrchestrationBackup,
  type AppOrchestration,
} from "@/hooks/useOrchestrations";
import { useApps } from "@/hooks/useApps";
import { useDeployTasks } from "@/hooks/useDeployTasks";
import { cn, slugify } from "@/lib/utils";
import { AppIcon } from "../apps/AppIcon";
import {
  AlertTriangle,
  ArchiveRestore,
  History,
  Loader2,
  RotateCcw,
} from "lucide-react";

interface AutomationRestoreDialogProps {
  orchestration: AppOrchestration | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

function formatDeletedAt(value?: string | null): string {
  if (!value) return "时间未知";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN", { hour12: false });
}

export function AutomationRestoreDialog({
  orchestration,
  open,
  onOpenChange,
}: AutomationRestoreDialogProps) {
  const toast = useToast();
  const { startTask } = useDeployTasks();
  const { data: backups = [], isLoading } = useOrchestrationBackups(
    orchestration?.name ?? null,
    open
  );
  const { data: allApps = [] } = useApps();
  const restoreMutation = useRestoreOrchestrationBackup();

  const appMap = useMemo(
    () => Object.fromEntries(allApps.map((app) => [app.name, app])),
    [allApps]
  );

  const [selectedName, setSelectedName] = useState<string | null>(null);
  const [instanceName, setInstanceName] = useState("");

  useEffect(() => {
    if (!open) return;
    setSelectedName(null);
    setInstanceName("");
  }, [open]);

  useEffect(() => {
    if (!open) return;
    if (backups.length === 0) {
      setSelectedName(null);
      return;
    }
    if (backups.some((backup) => backup.name === selectedName)) return;
    const first = backups[0];
    setSelectedName(first.name);
    setInstanceName(first.instance_name);
  }, [open, backups, selectedName]);

  const selected = backups.find((backup) => backup.name === selectedName) ?? null;

  const handleSelect = (name: string) => {
    const backup = backups.find((item) => item.name === name);
    setSelectedName(name);
    if (backup) setInstanceName(backup.instance_name);
  };

  const handleRestore = () => {
    if (!selected) {
      toast.error("请选择要恢复的备份");
      return;
    }
    const projectName = slugify(instanceName);
    if (!projectName) {
      toast.error("请输入有效的实例名称");
      return;
    }
    restoreMutation.mutate(
      { backup: selected.name, instance_name: projectName },
      {
        onSuccess: (response) => {
          for (const taskId of response.task_ids) {
            startTask(taskId);
          }
          onOpenChange(false);
        },
      }
    );
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <RotateCcw className="h-5 w-5 text-primary" />
            恢复实例组
          </DialogTitle>
        </DialogHeader>

        <div className="space-y-4 py-1">
          {isLoading ? (
            <div className="flex items-center justify-center gap-2 py-10 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              正在读取备份…
            </div>
          ) : backups.length === 0 ? (
            <div className="flex flex-col items-center justify-center gap-2 py-10 text-center">
              <ArchiveRestore className="h-8 w-8 text-muted-foreground/40" />
              <p className="text-sm text-muted-foreground">
                没有可恢复的备份
              </p>
              <p className="text-xs text-muted-foreground/70">
                删除实例组后，应用数据会自动备份到这里。
              </p>
            </div>
          ) : (
            <>
              <div className="space-y-2">
                <div className="flex items-center gap-1.5 text-sm font-medium">
                  <History className="h-4 w-4 text-muted-foreground" />
                  选择备份
                </div>
                <div className="max-h-56 space-y-2 overflow-y-auto pr-1">
                  {backups.map((backup) => (
                    <button
                      key={backup.name}
                      type="button"
                      onClick={() => handleSelect(backup.name)}
                      className={cn(
                        "w-full rounded-lg border px-3 py-2.5 text-left transition-colors",
                        backup.name === selectedName
                          ? "border-primary/60 bg-primary/5"
                          : "border-border hover:border-primary/40 hover:bg-muted/40"
                      )}
                    >
                      <div className="flex items-center justify-between gap-3">
                        <span className="truncate text-sm font-medium">
                          {backup.instance_name || backup.name}
                        </span>
                        <span className="shrink-0 text-xs text-muted-foreground">
                          {formatDeletedAt(backup.deleted_at)}
                        </span>
                      </div>
                      <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                        {backup.apps.map((app) => {
                          const appInfo = appMap[app.app_name];
                          return (
                            <span
                              key={`${backup.name}-${app.app_name}`}
                              className="inline-flex items-center gap-1 rounded-full bg-muted px-1.5 py-0.5 text-[11px] text-muted-foreground"
                            >
                              {appInfo ? (
                                <AppIcon
                                  app={appInfo}
                                  className="h-3.5 w-3.5"
                                />
                              ) : null}
                              {appInfo?.display_name || app.app_name}
                            </span>
                          );
                        })}
                      </div>
                    </button>
                  ))}
                </div>
              </div>

              <div className="space-y-1.5">
                <Label htmlFor="restore-instance-name">实例名称</Label>
                <Input
                  id="restore-instance-name"
                  value={instanceName}
                  onChange={(event) =>
                    setInstanceName(
                      event.target.value
                        .toLowerCase()
                        .replace(/[^a-z0-9_\s-]+/g, "")
                        .replace(/\s+/g, "-")
                    )
                  }
                  placeholder="restored-stack"
                />
                <p className="text-xs text-muted-foreground">
                  仅支持小写字母、数字、- 和 _；可沿用原实例名称或填写新名称。
                </p>
              </div>

              <div className="flex gap-2 rounded-lg border border-amber-500/30 bg-amber-500/5 px-3 py-2.5 text-xs leading-relaxed text-amber-700 dark:text-amber-400">
                <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
                <div className="space-y-1">
                  <p>
                    恢复会把备份中的应用数据目录移回，并按备份里的配置和账号密码
                    重新部署，随后备份目录将被消费。
                  </p>
                  <p>
                    共享存储（媒体/下载）按备份记录沿用原路径，不会修改或移动。
                    若填写新的实例名称，共享路径仍指向原目录。
                  </p>
                </div>
              </div>
            </>
          )}
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={restoreMutation.isPending}
          >
            取消
          </Button>
          <Button
            onClick={handleRestore}
            disabled={!selected || restoreMutation.isPending}
          >
            {restoreMutation.isPending ? (
              <>
                <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
                正在恢复…
              </>
            ) : (
              "恢复并部署"
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
