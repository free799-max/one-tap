/**
 * 自动更新配置弹窗
 *
 * 「配置」页选择允许自动更新的服务，避免所有服务一刀切；
 * 「记录」页展示自动升级的历史结果。
 */

import { useEffect, useMemo, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import {
  useAutoUpdateTargets,
  useSystemConfig,
  useUpdateSystemConfig,
} from "@/hooks/useSettings";
import { useUpdateEvents } from "@/hooks/useOrchestrations";
import type { App } from "@/hooks/useApps";
import { AppIcon } from "@/pages/apps/AppIcon";
import type { AutoUpdateTarget } from "@/api/settings";
import type { OrchestrationUpdateEvent } from "@/api/orchestrations";
import { cn, formatDate, formatRelativeTime, formatVersion } from "@/lib/utils";
import {
  AlertCircle,
  ArrowRight,
  CheckCircle2,
  History,
  ListChecks,
  Loader2,
  RefreshCw,
  XCircle,
} from "lucide-react";

interface AutoUpdateConfigDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

const STATUS_LABELS: Record<string, string> = {
  running: "运行中",
  stopped: "已停止",
  error: "异常",
  deploying: "部署中",
  upgrading: "升级中",
};

const STATUS_STYLES: Record<string, string> = {
  running:
    "border-emerald-500/30 bg-emerald-500/5 text-emerald-600 dark:text-emerald-400",
  stopped: "border-border bg-muted/40 text-muted-foreground",
  error: "border-destructive/30 bg-destructive/5 text-destructive",
  deploying:
    "border-amber-500/30 bg-amber-500/5 text-amber-600 dark:text-amber-400",
  upgrading:
    "border-amber-500/30 bg-amber-500/5 text-amber-600 dark:text-amber-400",
};

function StatusBadge({ status }: { status: string }) {
  return (
    <Badge
      variant="outline"
      className={cn(
        "gap-1 font-normal",
        STATUS_STYLES[status] ?? "border-border bg-muted/40 text-muted-foreground"
      )}
    >
      <span className="h-1.5 w-1.5 rounded-full bg-current" />
      {STATUS_LABELS[status] ?? status}
    </Badge>
  );
}

function TargetRow({
  target,
  checked,
  onToggle,
}: {
  target: AutoUpdateTarget;
  checked: boolean;
  onToggle: (key: string) => void;
}) {
  const disabled = !target.selectable;
  const meta = !target.selectable
    ? target.reason
    : target.type === "group" && target.apps.length > 0
      ? `包含：${target.apps.join("、")}`
      : target.complex
        ? target.reason
        : null;

  return (
    <button
      type="button"
      aria-pressed={checked}
      disabled={disabled}
      onClick={() => onToggle(target.key)}
      className={cn(
        "relative flex min-h-[112px] w-full cursor-pointer flex-col gap-2 rounded-xl border bg-card p-3.5 text-left shadow-sm transition-all",
        checked
          ? "border-primary/45 bg-primary/[0.03] ring-1 ring-primary/15"
          : "border-border",
        disabled
          ? "cursor-not-allowed opacity-60"
          : "hover:border-primary/35 hover:shadow-md"
      )}
    >
      <div className="flex min-w-0 items-start gap-3">
        <AppIcon
          app={
            {
              name: target.icon_name || target.name,
              display_name: target.display_name,
              icon: target.icon,
            } as App
          }
          className="h-10 w-10 shrink-0 rounded-xl border border-border/60 bg-white p-1 shadow-sm"
        />
        <div className="min-w-0 flex-1">
          <div className="truncate text-sm font-semibold text-foreground">
            {target.display_name}
          </div>
          <div className="truncate text-[11px] text-muted-foreground/70">
            {target.name}
          </div>
          <div className="mt-1.5 flex flex-wrap items-center gap-1">
            <StatusBadge status={target.status} />
            {target.upgrade_available && (
              <Badge variant="secondary" className="font-normal">
                有新版本
              </Badge>
            )}
            {target.complex && (
              <Badge
                variant="outline"
                className="border-amber-500/40 font-normal text-amber-600 dark:text-amber-400"
              >
                {target.type === "group" ? "组合部署" : "多容器"}
              </Badge>
            )}
          </div>
        </div>

        <div
          className="flex shrink-0 items-center gap-1 pt-0.5 text-xs font-medium whitespace-nowrap tabular-nums text-muted-foreground"
          title={`${formatVersion(target.current_version)} → ${formatVersion(target.target_version)}`}
        >
          <span>{formatVersion(target.current_version)}</span>
          <ArrowRight className="h-3 w-3 text-muted-foreground/50" />
          <span>{formatVersion(target.target_version)}</span>
        </div>
      </div>

      {meta && (
        <p
          className="line-clamp-1 text-xs leading-relaxed text-muted-foreground/80"
          title={meta}
        >
          {meta}
        </p>
      )}
    </button>
  );
}

function TargetGroup({
  title,
  targets,
  selected,
  onToggle,
}: {
  title: string;
  targets: AutoUpdateTarget[];
  selected: Set<string>;
  onToggle: (key: string) => void;
}) {
  if (targets.length === 0) return null;
  return (
    <section className="space-y-2">
      <h3 className="flex items-center gap-2 text-xs font-medium tracking-wide text-muted-foreground">
        {title}
        <span className="rounded-full bg-muted px-1.5 py-0.5 text-[10px] tabular-nums text-muted-foreground/80">
          {targets.length}
        </span>
      </h3>
      <div className="grid gap-2 sm:grid-cols-2">
        {targets.map((target) => (
          <TargetRow
            key={target.key}
            target={target}
            checked={selected.has(target.key)}
            onToggle={onToggle}
          />
        ))}
      </div>
    </section>
  );
}

function RecordItem({
  event,
  displayName,
}: {
  event: OrchestrationUpdateEvent;
  displayName: string;
}) {
  const applied = event.status === "applied";
  const detail = event.detail ?? {};
  const apps = detail.selected_apps ?? [];
  const singleApp = detail.app_name ? [detail.app_name] : [];
  const updatedApps = apps.length > 0 ? apps : singleApp;
  const versionOnly = detail.mode === "version_only";

  return (
    <div className="rounded-xl border border-border bg-card p-3.5 shadow-sm">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <span className="truncate text-sm font-medium text-foreground">
          {displayName}
        </span>
        {(event.from_version || event.to_version) && (
          <span className="text-xs font-medium tabular-nums text-muted-foreground">
            {formatVersion(event.from_version)}
            <span className="mx-1 text-muted-foreground/60">→</span>
            {formatVersion(event.to_version)}
          </span>
        )}
        <Badge variant="outline" className="font-normal">
          自动
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
          {applied ? "已更新" : "失败"}
        </Badge>
        <span
          className="ml-auto shrink-0 text-xs text-muted-foreground/80"
          title={formatDate(event.created_at)}
        >
          {formatRelativeTime(event.created_at)}
        </span>
      </div>

      {versionOnly ? (
        <p className="mt-2 text-xs text-muted-foreground">
          版本已对齐，无需替换镜像
        </p>
      ) : (
        updatedApps.length > 0 && (
          <p className="mt-2 text-xs leading-relaxed text-muted-foreground">
            <span className="font-medium text-foreground/70">更新应用：</span>
            {updatedApps.join("、")}
          </p>
        )
      )}

      {(detail.image_from || detail.image_to) && (
        <p
          className="mt-1 truncate text-xs text-muted-foreground/80"
          title={`${detail.image_from ?? "未知"} → ${detail.image_to ?? "未知"}`}
        >
          镜像：{detail.image_from ?? "未知"} → {detail.image_to ?? "未知"}
        </p>
      )}

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
    </div>
  );
}

export function AutoUpdateConfigDialog({
  open,
  onOpenChange,
}: AutoUpdateConfigDialogProps) {
  const { data: config } = useSystemConfig();
  const targetsQuery = useAutoUpdateTargets(open);
  const updateConfig = useUpdateSystemConfig();
  const [tab, setTab] = useState("config");
  const [selected, setSelected] = useState<string[]>([]);
  const [initialized, setInitialized] = useState(false);

  useEffect(() => {
    if (!open) {
      setInitialized(false);
      setTab("config");
      return;
    }
    if (config && !initialized) {
      setSelected(config.auto_update_targets ?? []);
      setInitialized(true);
    }
  }, [open, config, initialized]);

  const targets = useMemo(
    () => targetsQuery.data?.targets ?? [],
    [targetsQuery.data]
  );
  const selectedSet = useMemo(() => new Set(selected), [selected]);
  const groupTargets = useMemo(
    () => targets.filter((target) => target.type === "group"),
    [targets]
  );
  const appTargets = useMemo(
    () => targets.filter((target) => target.type === "app"),
    [targets]
  );
  const selectableTargets = useMemo(
    () => targets.filter((target) => target.selectable),
    [targets]
  );
  const recommendedKeys = useMemo(
    () =>
      selectableTargets
        .filter((target) => !target.complex)
        .map((target) => target.key),
    [selectableTargets]
  );

  const recordsQuery = useUpdateEvents(
    { scope: "instance", mode: "auto", limit: 100 },
    open
  );

  const groupNames = useMemo(
    () =>
      Object.fromEntries(
        groupTargets.map((target) => [
          target.id,
          `${target.display_name} · ${target.name}`,
        ])
      ),
    [groupTargets]
  );

  const toggle = (key: string) => {
    setSelected((prev) =>
      prev.includes(key)
        ? prev.filter((item) => item !== key)
        : [...prev, key]
    );
  };

  const handleSave = () => {
    updateConfig.mutate(
      { auto_update_targets: selected },
      { onSuccess: () => onOpenChange(false) }
    );
  };

  const selectedCount = selected.length;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[90vh] flex-col overflow-hidden sm:max-w-3xl">
        <DialogHeader className="flex-shrink-0">
          <DialogTitle className="flex items-center gap-2">
            <ListChecks className="h-4 w-4 text-primary" />
            自动更新配置
          </DialogTitle>
          <DialogDescription>
            只有选中的服务会在夜间时段自动更新；组合部署按方案整体升级。
          </DialogDescription>
        </DialogHeader>

        <Tabs
          value={tab}
          onValueChange={setTab}
          className="min-h-0 flex-1"
        >
          <TabsList className="w-full">
            <TabsTrigger value="config">
              更新配置
              {targets.length > 0 ? ` (${selectedCount}/${targets.length})` : ""}
            </TabsTrigger>
            <TabsTrigger value="records">
              更新记录
              {recordsQuery.data
                ? ` (${recordsQuery.data.length})`
                : ""}
            </TabsTrigger>
          </TabsList>

          <TabsContent
            value="config"
            className="min-h-0 flex-1 space-y-3 overflow-y-auto pt-3 pr-1"
          >
            <div className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-border bg-muted/30 px-3 py-2.5 text-xs text-muted-foreground">
              <span>
                未选择任何服务时不会自动更新。复杂服务只替换主服务镜像，
                建议确认后再开启。
              </span>
              <div className="flex items-center gap-2">
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  className="h-7 px-2"
                  onClick={() => setSelected(recommendedKeys)}
                  disabled={recommendedKeys.length === 0}
                >
                  选择推荐项
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  className="h-7 px-2"
                  onClick={() => setSelected([])}
                  disabled={selectedCount === 0}
                >
                  清空
                </Button>
              </div>
            </div>

            {targetsQuery.isLoading ? (
              <div className="flex items-center justify-center gap-2 py-12 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />
                加载服务列表…
              </div>
            ) : targetsQuery.error ? (
              <div className="flex flex-col items-center gap-2 py-12 text-center">
                <AlertCircle className="h-6 w-6 text-destructive/70" />
                <p className="text-sm text-destructive">
                  {(targetsQuery.error as { displayMessage?: string })
                    ?.displayMessage || "服务列表加载失败"}
                </p>
              </div>
            ) : targets.length === 0 ? (
              <div className="flex flex-col items-center gap-2 py-12 text-center">
                <ListChecks className="h-6 w-6 text-muted-foreground/50" />
                <p className="text-sm text-muted-foreground">
                  暂无可配置的 OneTap 托管服务
                </p>
                <p className="text-xs text-muted-foreground/80">
                  通过应用市场或自动化方案部署的服务会出现在这里
                </p>
              </div>
            ) : (
              <div className="space-y-4 pb-2">
                <TargetGroup
                  title="组合部署"
                  targets={groupTargets}
                  selected={selectedSet}
                  onToggle={toggle}
                />
                <TargetGroup
                  title="单应用"
                  targets={appTargets}
                  selected={selectedSet}
                  onToggle={toggle}
                />
              </div>
            )}
          </TabsContent>

          <TabsContent
            value="records"
            className="min-h-0 flex-1 space-y-3 overflow-y-auto pt-3 pr-1"
          >
            <div className="flex items-center justify-between rounded-xl border border-border bg-muted/30 px-3 py-2.5 text-xs text-muted-foreground">
              <span>最近 100 条自动更新记录</span>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                className="h-7 gap-1.5 px-2"
                onClick={() => recordsQuery.refetch()}
                disabled={recordsQuery.isFetching}
              >
                <RefreshCw
                  className={cn(
                    "h-3.5 w-3.5",
                    recordsQuery.isFetching && "animate-spin"
                  )}
                />
                刷新
              </Button>
            </div>

            {recordsQuery.isLoading ? (
              <div className="flex items-center justify-center gap-2 py-12 text-sm text-muted-foreground">
                <Loader2 className="h-4 w-4 animate-spin" />
                加载更新记录…
              </div>
            ) : recordsQuery.error ? (
              <div className="flex flex-col items-center gap-2 py-12 text-center">
                <AlertCircle className="h-6 w-6 text-destructive/70" />
                <p className="text-sm text-destructive">
                  {(recordsQuery.error as { displayMessage?: string })
                    ?.displayMessage || "更新记录加载失败"}
                </p>
              </div>
            ) : !recordsQuery.data || recordsQuery.data.length === 0 ? (
              <div className="flex flex-col items-center gap-2 py-12 text-center">
                <History className="h-6 w-6 text-muted-foreground/50" />
                <p className="text-sm text-muted-foreground">
                  暂无自动更新记录
                </p>
                <p className="text-xs text-muted-foreground/80">
                  开启自动更新并选择服务后，升级结果会记录在这里
                </p>
              </div>
            ) : (
              <div className="space-y-2.5 pb-2">
                {recordsQuery.data.map((event) => {
                  const displayName = event.orchestration_name.startsWith(
                    "app:"
                  )
                    ? `应用：${event.orchestration_name.slice(4)}`
                    : (event.group_id != null
                        ? groupNames[event.group_id]
                        : null) ||
                      event.orchestration_name;
                  return (
                    <RecordItem
                      key={event.id}
                      event={event}
                      displayName={displayName}
                    />
                  );
                })}
              </div>
            )}
          </TabsContent>
        </Tabs>

        {tab === "config" && (
          <DialogFooter className="flex-shrink-0">
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
            >
              取消
            </Button>
            <Button
              type="button"
              onClick={handleSave}
              disabled={updateConfig.isPending || !initialized}
            >
              {updateConfig.isPending ? "保存中…" : "保存配置"}
            </Button>
          </DialogFooter>
        )}
      </DialogContent>
    </Dialog>
  );
}
