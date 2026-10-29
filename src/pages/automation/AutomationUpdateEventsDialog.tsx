/**
 * 方案更新记录对话框
 *
 * 汇总「方案模板更新」与「实例组升级」两类事件，并在顶部展示
 * 最近一次目录同步状态与手动同步入口。
 */

import { useMemo } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  useManualSync,
  useSyncStatus,
  useUpdateEvents,
  type AppOrchestration,
  type OrchestrationUpdateEvent,
} from "@/hooks/useOrchestrations";
import { cn, formatDate, formatRelativeTime } from "@/lib/utils";
import {
  AlertCircle,
  CheckCircle2,
  Clock3,
  History,
  Loader2,
  RefreshCw,
  XCircle,
} from "lucide-react";

interface AutomationUpdateEventsDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  orchestrations?: AppOrchestration[];
}

const MODE_LABELS: Record<string, string> = {
  auto: "自动",
  manual: "手动",
};

const SCOPE_LABELS: Record<string, string> = {
  template: "方案",
  instance: "实例",
};

function versionLabel(version?: string | null) {
  return version ? `v${version}` : "未知版本";
}

function EventItem({
  event,
  displayName,
}: {
  event: OrchestrationUpdateEvent;
  displayName: string;
}) {
  const applied = event.status === "applied";
  const changelog = event.detail?.changelog?.trim();
  const added = event.detail?.apps_added ?? [];
  const removed = event.detail?.apps_removed ?? [];
  const retained = event.detail?.apps_retained ?? [];
  const selected = event.detail?.selected_apps ?? [];

  return (
    <div className="rounded-xl border border-border bg-card p-3.5 shadow-sm">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="truncate text-sm font-medium text-foreground">
          {displayName}
        </span>
        {(event.from_version || event.to_version) && (
          <span className="text-xs font-medium tabular-nums text-muted-foreground">
            {versionLabel(event.from_version)}
            <span className="mx-1 text-muted-foreground/60">→</span>
            {versionLabel(event.to_version)}
          </span>
        )}
        <Badge variant="outline" className="font-normal">
          {SCOPE_LABELS[event.scope] ?? event.scope}
        </Badge>
        <Badge variant="secondary" className="font-normal">
          {MODE_LABELS[event.mode] ?? event.mode}
        </Badge>
        <Badge
          variant={applied ? "secondary" : "destructive"}
          className={cn(
            "gap-1 font-normal",
            applied &&
              "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
          )}
        >
          {applied ? (
            <CheckCircle2 className="h-3 w-3" />
          ) : (
            <XCircle className="h-3 w-3" />
          )}
          {applied ? "已应用" : "失败"}
        </Badge>
        <span
          className="ml-auto shrink-0 text-xs text-muted-foreground/80"
          title={formatDate(event.created_at)}
        >
          {formatRelativeTime(event.created_at)}
        </span>
      </div>

      {event.message && (
        <p
          className={cn(
            "mt-2 whitespace-pre-wrap break-words text-xs leading-relaxed",
            applied ? "text-muted-foreground" : "text-destructive"
          )}
        >
          {event.message}
        </p>
      )}

      {added.length > 0 && (
        <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
          <span className="font-medium text-emerald-600 dark:text-emerald-400">
            新增应用：
          </span>
          {added.join("、")}
        </p>
      )}
      {removed.length > 0 && (
        <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
          <span className="font-medium text-destructive">移除应用：</span>
          {removed.join("、")}
        </p>
      )}
      {retained.length > 0 && (
        <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
          <span className="font-medium text-amber-600 dark:text-amber-400">
            方案已移除（保留运行）：
          </span>
          {retained.join("、")}
        </p>
      )}
      {selected.length > 0 && (
        <p className="mt-1 text-xs leading-relaxed text-muted-foreground">
          <span className="font-medium text-foreground/70">升级应用：</span>
          {selected.join("、")}
        </p>
      )}

      {changelog && (
        <div className="mt-2 max-h-40 overflow-y-auto whitespace-pre-wrap rounded-lg bg-muted/40 px-3 py-2 text-xs leading-relaxed text-muted-foreground">
          {changelog}
        </div>
      )}
    </div>
  );
}

function EventList({
  events,
  isLoading,
  error,
  emptyText,
  nameMap,
}: {
  events: OrchestrationUpdateEvent[] | undefined;
  isLoading: boolean;
  error: unknown;
  emptyText: string;
  nameMap: Record<string, string>;
}) {
  if (isLoading) {
    return (
      <div className="flex items-center justify-center gap-2 py-12 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        加载更新记录…
      </div>
    );
  }

  if (error) {
    const message =
      (error as { displayMessage?: string } | null)?.displayMessage ||
      "更新记录加载失败";
    return (
      <div className="flex flex-col items-center gap-2 py-12 text-center">
        <AlertCircle className="h-6 w-6 text-destructive/70" />
        <p className="text-sm text-destructive">{message}</p>
        <p className="text-xs text-muted-foreground">
          后端可能尚未提供更新记录接口
        </p>
      </div>
    );
  }

  if (!events || events.length === 0) {
    return (
      <div className="flex flex-col items-center gap-2 py-12 text-center">
        <History className="h-6 w-6 text-muted-foreground/50" />
        <p className="text-sm text-muted-foreground">{emptyText}</p>
      </div>
    );
  }

  return (
    <div className="space-y-2.5">
      {events.map((event) => (
        <EventItem
          key={event.id}
          event={event}
          displayName={
            event.orchestration_name === "sync"
              ? "方案同步"
              : nameMap[event.orchestration_name] || event.orchestration_name
          }
        />
      ))}
    </div>
  );
}

export function AutomationUpdateEventsDialog({
  open,
  onOpenChange,
  orchestrations = [],
}: AutomationUpdateEventsDialogProps) {
  const templateQuery = useUpdateEvents(
    { scope: "template", limit: 30 },
    open
  );
  const instanceQuery = useUpdateEvents(
    { scope: "instance", limit: 30 },
    open
  );
  const { data: syncStatus, isLoading: syncLoading } = useSyncStatus(open);
  const syncMutation = useManualSync();

  const nameMap = useMemo(
    () =>
      Object.fromEntries(
        orchestrations.map((item) => [item.name, item.display_name])
      ),
    [orchestrations]
  );

  const lastResult = syncStatus?.last_result;
  const lastSyncText = syncStatus?.last_sync_at
    ? formatRelativeTime(syncStatus.last_sync_at)
    : "暂无记录";

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-3xl max-h-[88vh] flex flex-col overflow-hidden">
        <DialogHeader className="flex-shrink-0">
          <DialogTitle className="flex items-center gap-2">
            <History className="h-4 w-4 text-primary" />
            更新记录
          </DialogTitle>
          <DialogDescription>
            查看方案模板的版本变化，以及实例组的升级历史。
          </DialogDescription>
        </DialogHeader>

        <div className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-border bg-muted/30 px-3 py-2.5">
          <div className="min-w-0 space-y-1 text-xs">
            <div className="flex items-center gap-1.5 text-muted-foreground">
              <Clock3 className="h-3.5 w-3.5 shrink-0" />
              <span>
                最近同步：
                {syncLoading ? "加载中…" : lastSyncText}
              </span>
              {syncStatus?.last_sync_at && (
                <span className="hidden text-muted-foreground/60 sm:inline">
                  （{formatDate(syncStatus.last_sync_at)}）
                </span>
              )}
            </div>
            {syncStatus?.last_error ? (
              <p className="break-words text-destructive">
                上次同步失败：{syncStatus.last_error}
              </p>
            ) : lastResult && !lastResult.skipped ? (
              <p className="text-muted-foreground/80">
                上次同步新增 {lastResult.created ?? 0} 个，更新{" "}
                {lastResult.updated ?? 0} 个
              </p>
            ) : null}
          </div>
          <Button
            size="sm"
            variant="outline"
            className="shrink-0 gap-1.5"
            onClick={() => syncMutation.mutate()}
            disabled={syncMutation.isPending}
          >
            <RefreshCw
              className={cn(
                "h-3.5 w-3.5",
                syncMutation.isPending && "animate-spin"
              )}
            />
            {syncMutation.isPending ? "同步中…" : "立即同步"}
          </Button>
        </div>

        <Tabs defaultValue="template" className="min-h-0 flex-1">
          <TabsList className="w-full">
            <TabsTrigger value="template">
              方案更新
              {templateQuery.data ? ` (${templateQuery.data.length})` : ""}
            </TabsTrigger>
            <TabsTrigger value="instance">
              实例升级
              {instanceQuery.data ? ` (${instanceQuery.data.length})` : ""}
            </TabsTrigger>
          </TabsList>

          <TabsContent
            value="template"
            className="min-h-0 max-h-[50vh] overflow-y-auto pt-3 pr-1"
          >
            <EventList
              events={templateQuery.data}
              isLoading={templateQuery.isLoading}
              error={templateQuery.error}
              emptyText="暂无方案更新记录，方案同步后会自动记录版本变化"
              nameMap={nameMap}
            />
          </TabsContent>
          <TabsContent
            value="instance"
            className="min-h-0 max-h-[50vh] overflow-y-auto pt-3 pr-1"
          >
            <EventList
              events={instanceQuery.data}
              isLoading={instanceQuery.isLoading}
              error={instanceQuery.error}
              emptyText="暂无实例升级记录"
              nameMap={nameMap}
            />
          </TabsContent>
        </Tabs>
      </DialogContent>
    </Dialog>
  );
}
