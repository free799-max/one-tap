/**
 * 添加应用加速弹窗
 *
 * 只展示当前未开启加速、且可以注入 DNS 的应用，按自动化方案分组，
 * 支持一次选择多个应用。开启动作由外层逐个提交，避免并发重启应用。
 */

import { useEffect, useMemo, useState } from "react";
import {
  AlertTriangle,
  Check,
  ChevronDown,
  Loader2,
  Search,
  SearchX,
  Zap,
} from "lucide-react";

import { AppIcon } from "@/pages/apps/AppIcon";
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
import type { AccelerationApp } from "@/api/settings";

interface AccelerationAppDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  apps: AccelerationApp[];
  isPending?: boolean;
  onConfirm: (instanceIds: number[]) => void;
}

interface AppGroup {
  key: string;
  label: string;
  hint: string | null;
  apps: AccelerationApp[];
}

function groupApps(apps: AccelerationApp[]): AppGroup[] {
  const groups = new Map<string, AppGroup>();
  for (const app of apps) {
    const key = app.orchestration_group_id
      ? `group:${app.orchestration_group_id}`
      : "standalone";
    const group =
      groups.get(key) ??
      {
        key,
        label:
          key === "standalone"
            ? "独立应用"
            : (app.orchestration_name ?? "自动化方案"),
        hint: key === "standalone" ? null : app.orchestration_group_name,
        apps: [],
      };
    group.apps.push(app);
    groups.set(key, group);
  }
  const standalone = groups.get("standalone");
  const named = [...groups.values()]
    .filter((group) => group.key !== "standalone")
    .sort((a, b) => a.label.localeCompare(b.label, "zh-CN"));
  return standalone ? [...named, standalone] : named;
}

function matchesQuery(app: AccelerationApp, query: string): boolean {
  if (!query) return true;
  return [
    app.display_name,
    app.instance_name,
    app.app_name ?? "",
    app.project_name ?? "",
    app.orchestration_name ?? "",
    app.orchestration_group_name ?? "",
  ].some((text) => text.toLowerCase().includes(query));
}

export function AccelerationAppDialog({
  open,
  onOpenChange,
  apps,
  isPending,
  onConfirm,
}: AccelerationAppDialogProps) {
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<number[]>([]);
  const [collapsed, setCollapsed] = useState<string[]>([]);

  useEffect(() => {
    if (!open) return;
    setQuery("");
    setSelected([]);
    setCollapsed([]);
  }, [open]);

  const usable = useMemo(() => apps.filter((app) => app.can_enable), [apps]);
  const blocked = apps.length - usable.length;
  const queryText = query.trim().toLowerCase();
  const filtered = useMemo(
    () => usable.filter((app) => matchesQuery(app, queryText)),
    [usable, queryText]
  );
  const groups = useMemo(() => groupApps(filtered), [filtered]);

  const toggleApp = (instanceId: number) => {
    setSelected((prev) =>
      prev.includes(instanceId)
        ? prev.filter((id) => id !== instanceId)
        : [...prev, instanceId]
    );
  };

  const toggleGroup = (group: AppGroup) => {
    const ids = group.apps.map((app) => app.instance_id);
    const allSelected = ids.every((id) => selected.includes(id));
    setSelected((prev) =>
      allSelected
        ? prev.filter((id) => !ids.includes(id))
        : [...new Set([...prev, ...ids])]
    );
  };

  const toggleCollapse = (key: string) => {
    setCollapsed((prev) =>
      prev.includes(key) ? prev.filter((item) => item !== key) : [...prev, key]
    );
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Zap className="h-4 w-4 text-muted-foreground" />
            添加应用加速
          </DialogTitle>
          <DialogDescription>
            只给选中的应用注入 DNS，并重启该应用使配置生效；不会修改宿主机或其他应用。
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <div className="flex items-center gap-2 rounded-lg border px-3 py-2">
            <Search className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
            <input
              autoFocus
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder="搜索应用、实例或自动化方案"
              aria-label="搜索应用"
              className="h-6 min-w-0 flex-1 bg-transparent text-sm outline-none placeholder:text-muted-foreground"
            />
            {query ? (
              <button
                type="button"
                onClick={() => setQuery("")}
                className="text-[11px] text-muted-foreground hover:text-foreground"
              >
                清空
              </button>
            ) : (
              <span className="shrink-0 text-[11px] text-muted-foreground">
                {usable.length} 个可添加
              </span>
            )}
          </div>

          <div className="max-h-[420px] space-y-3 overflow-y-auto pr-1 [scrollbar-width:thin]">
            {groups.length === 0 ? (
              <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed py-12 text-muted-foreground">
                <SearchX className="h-6 w-6 opacity-60" />
                <p className="text-sm">
                  {usable.length === 0 ? "没有可添加的应用" : "没有匹配的应用"}
                </p>
                <p className="text-xs">
                  {usable.length === 0
                    ? "先在应用市场部署应用，或等待当前应用完成 Compose 项目初始化。"
                    : "换个关键词试试。"}
                </p>
              </div>
            ) : (
              groups.map((group) => {
                const isCollapsed = collapsed.includes(group.key);
                const allSelected = group.apps.every((app) =>
                  selected.includes(app.instance_id)
                );
                return (
                  <div key={group.key} className="space-y-2">
                    <div className="flex items-center justify-between gap-2">
                      <button
                        type="button"
                        onClick={() => toggleCollapse(group.key)}
                        className="flex min-w-0 items-center gap-1.5 text-xs font-medium text-foreground"
                      >
                        <ChevronDown
                          className={`h-3.5 w-3.5 text-muted-foreground transition-transform ${
                            isCollapsed ? "-rotate-90" : ""
                          }`}
                        />
                        <span className="truncate">{group.label}</span>
                        {group.hint && (
                          <span className="truncate text-[11px] font-normal text-muted-foreground">
                            {group.hint}
                          </span>
                        )}
                        <span className="shrink-0 text-[11px] font-normal text-muted-foreground">
                          {group.apps.length} 个
                        </span>
                      </button>
                      <button
                        type="button"
                        onClick={() => toggleGroup(group)}
                        className="shrink-0 text-[11px] text-primary hover:underline"
                      >
                        {allSelected ? "取消全选" : "全选本组"}
                      </button>
                    </div>

                    {!isCollapsed && (
                      <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
                        {group.apps.map((app) => {
                          const isSelected = selected.includes(app.instance_id);
                          return (
                            <button
                              key={app.instance_id}
                              type="button"
                              onClick={() => toggleApp(app.instance_id)}
                              className={`group flex items-start gap-2.5 rounded-xl border p-2.5 text-left transition-all ${
                                isSelected
                                  ? "border-primary/60 bg-primary/[0.06] shadow-sm"
                                  : "border-border bg-white hover:border-foreground/25 hover:shadow-sm"
                              }`}
                            >
                              <AppIcon
                                app={{
                                  name: app.app_name ?? app.instance_name,
                                  icon: app.icon,
                                }}
                                className="h-8 w-8 shrink-0 ring-1 ring-black/5"
                              />
                              <span className="min-w-0 flex-1">
                                <span
                                  className={`block truncate text-[13px] leading-tight ${
                                    isSelected
                                      ? "font-semibold text-primary"
                                      : "font-medium text-foreground"
                                  }`}
                                >
                                  {app.display_name}
                                </span>
                                <span className="mt-0.5 block truncate text-[11px] text-muted-foreground">
                                  {app.project_name ?? app.instance_name}
                                </span>
                                <span className="mt-1 flex flex-wrap items-center gap-1">
                                  {app.status === "running" ? (
                                    <Badge className="bg-emerald-500/10 px-1.5 py-0 text-[10px] text-emerald-700">
                                      运行中
                                    </Badge>
                                  ) : (
                                    <Badge
                                      variant="secondary"
                                      className="px-1.5 py-0 text-[10px]"
                                    >
                                      {app.status}
                                    </Badge>
                                  )}
                                  {app.services.length > 0 && (
                                    <span className="truncate text-[10px] text-muted-foreground">
                                      {app.services.slice(0, 2).join(", ")}
                                      {app.services.length > 2
                                        ? ` +${app.services.length - 2}`
                                        : ""}
                                    </span>
                                  )}
                                </span>
                              </span>
                              <span
                                className={`mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full border ${
                                  isSelected
                                    ? "border-primary bg-primary text-primary-foreground"
                                    : "border-border opacity-60 group-hover:opacity-100"
                                }`}
                              >
                                {isSelected && (
                                  <Check
                                    className="h-2.5 w-2.5"
                                    strokeWidth={3}
                                  />
                                )}
                              </span>
                            </button>
                          );
                        })}
                      </div>
                    )}
                  </div>
                );
              })
            )}
          </div>

          {blocked > 0 && (
            <p className="flex items-start gap-1.5 text-[11px] text-muted-foreground">
              <AlertTriangle className="mt-0.5 h-3 w-3 shrink-0 text-amber-500" />
              {blocked} 个应用暂不支持加速：未关联 Compose 项目，或所有服务都使用了
              host / service 网络模式。
            </p>
          )}
        </div>

        <DialogFooter>
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={isPending}
          >
            取消
          </Button>
          <Button
            onClick={() => onConfirm(selected)}
            disabled={selected.length === 0 || isPending}
          >
            {isPending ? (
              <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
            ) : (
              <Zap className="mr-1 h-3.5 w-3.5" />
            )}
            开启加速
            {selected.length > 0 ? `（${selected.length}）` : ""}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
