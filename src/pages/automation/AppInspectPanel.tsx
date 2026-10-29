/**
 * 应用配置健康检测面板
 *
 * 手动触发实时检测，按分组渲染检测项，支持勾选可修复项或一键修复全部。
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import type { ApiError } from "@/api/types";
import {
  useAppInspect,
  useFixAppInspect,
  type AppInspectItem,
  type InspectItemStatus,
  type OrchestrationInstanceApp,
} from "@/hooks/useOrchestrations";
import { cn } from "@/lib/utils";
import {
  Check,
  CircleSlash,
  Loader2,
  RefreshCw,
  Stethoscope,
  Wrench,
} from "lucide-react";

const STATUS_DOT: Record<InspectItemStatus, string> = {
  ok: "bg-emerald-500",
  warn: "bg-amber-500",
  error: "bg-red-500",
};

const STATUS_LABEL: Record<InspectItemStatus, string> = {
  ok: "正常",
  warn: "建议优化",
  error: "异常",
};

const STATUS_TEXT: Record<InspectItemStatus, string> = {
  ok: "text-emerald-600",
  warn: "text-amber-600",
  error: "text-red-600",
};

function InspectItemRow({
  item,
  checked,
  onToggle,
}: {
  item: AppInspectItem;
  checked: boolean;
  onToggle: (id: string) => void;
}) {
  const hasValues = item.current_value || item.expected_value;
  return (
    <div
      role={item.fixable ? "button" : undefined}
      tabIndex={item.fixable ? 0 : undefined}
      onClick={item.fixable ? () => onToggle(item.id) : undefined}
      onKeyDown={
        item.fixable
          ? (e) => {
              if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                onToggle(item.id);
              }
            }
          : undefined
      }
      className={cn(
        "flex items-start gap-3 rounded-lg border px-3 py-2.5",
        item.fixable && "cursor-pointer transition-colors",
        item.fixable && checked
          ? "border-primary/50 bg-primary/5"
          : "border-border",
        item.fixable && !checked && "hover:bg-muted/50"
      )}
    >
      {item.fixable && (
        <span
          className={cn(
            "mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded border",
            checked
              ? "border-primary bg-primary text-primary-foreground"
              : "border-input bg-background"
          )}
        >
          {checked && <Check className="h-3 w-3" />}
        </span>
      )}
      <span
        className={cn(
          "mt-1.5 h-2 w-2 shrink-0 rounded-full",
          STATUS_DOT[item.status] ?? "bg-muted-foreground"
        )}
      />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <span className="text-sm font-medium">{item.title}</span>
          <span className={cn("text-xs", STATUS_TEXT[item.status])}>
            {STATUS_LABEL[item.status] ?? item.status}
          </span>
          {item.fixable && (
            <span className="rounded bg-primary/10 px-1.5 py-0.5 text-[10px] font-medium text-primary">
              可自动修复
            </span>
          )}
        </div>
        {item.message && (
          <p className="mt-0.5 text-xs text-muted-foreground break-words">
            {item.message}
          </p>
        )}
        {hasValues && (
          <p className="mt-1 text-xs text-muted-foreground font-mono break-all">
            {item.current_value && <>当前：{item.current_value}</>}
            {item.current_value && item.expected_value && "　"}
            {item.expected_value && <>期望：{item.expected_value}</>}
          </p>
        )}
      </div>
    </div>
  );
}

export function AppInspectPanel({
  instanceId,
  app,
}: {
  instanceId: number;
  app: OrchestrationInstanceApp;
}) {
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const inspectQuery = useAppInspect(instanceId, app.app_name);
  const fixMutation = useFixAppInspect();
  const autoInspectedRef = useRef(false);
  const refetchInspect = inspectQuery.refetch;

  useEffect(() => {
    setSelected(new Set());
  }, [app.app_name]);

  // 打开「检测优化」时自动跑一次检测，用户无需再手动点「开始检测」
  useEffect(() => {
    if (autoInspectedRef.current) return;
    autoInspectedRef.current = true;
    refetchInspect();
  }, [refetchInspect]);

  const report = inspectQuery.data;
  const inspectError = inspectQuery.error as ApiError | null;
  const isInspecting = inspectQuery.isFetching;
  const fixableItems = report?.items.filter((i) => i.fixable) ?? [];

  const summary = useMemo(() => {
    const counts: Record<InspectItemStatus, number> = {
      ok: 0,
      warn: 0,
      error: 0,
    };
    for (const item of report?.items ?? []) {
      counts[item.status] = (counts[item.status] ?? 0) + 1;
    }
    return counts;
  }, [report]);

  const groups = useMemo(() => {
    const order: string[] = [];
    const map = new Map<string, AppInspectItem[]>();
    for (const item of report?.items ?? []) {
      const key = item.category || "";
      if (!map.has(key)) {
        map.set(key, []);
        order.push(key);
      }
      map.get(key)!.push(item);
    }
    return order.map((key) => ({ key, items: map.get(key)! }));
  }, [report]);

  const handleToggle = (id: string) => {
    setSelected((prev) => {
      const next = new Set(prev);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      return next;
    });
  };

  const handleFix = () => {
    if (selected.size === 0) return;
    fixMutation.mutate(
      { instanceId, appName: app.app_name, itemIds: [...selected] },
      {
        onSuccess: () => {
          setSelected(new Set());
          inspectQuery.refetch();
        },
      }
    );
  };

  const handleFixAll = () => {
    if (fixableItems.length === 0) return;
    fixMutation.mutate(
      {
        instanceId,
        appName: app.app_name,
        itemIds: fixableItems.map((item) => item.id),
      },
      {
        onSuccess: () => {
          setSelected(new Set());
          inspectQuery.refetch();
        },
      }
    );
  };

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-2">
        <p className="text-xs text-muted-foreground">
          检测 {app.display_name} 的运行配置是否符合最佳实践，标记为「可自动修复」的项可直接修复。
        </p>
        <Button
          size="sm"
          variant="outline"
          className="h-8 shrink-0 gap-1.5 rounded-lg"
          onClick={() => inspectQuery.refetch()}
          disabled={isInspecting || fixMutation.isPending}
        >
          {isInspecting ? (
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
          ) : (
            <RefreshCw className="h-3.5 w-3.5" />
          )}
          {isInspecting ? "检测中…" : report ? "重新检测" : "开始检测"}
        </Button>
      </div>

      {isInspecting && (
        <div className="flex items-center gap-2 rounded-lg border border-border bg-muted/30 px-3 py-6 text-sm text-muted-foreground justify-center">
          <Loader2 className="h-4 w-4 animate-spin" />
          正在检测，可能需要几秒钟…
        </div>
      )}

      {!isInspecting && inspectError && (
        <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2.5 text-xs text-red-600 dark:border-red-900/50 dark:bg-red-950/30">
          检测失败：{inspectError.displayMessage || "请求出错，请稍后重试"}
        </div>
      )}

      {!isInspecting && !inspectError && !report && (
        <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-border py-8 text-muted-foreground">
          <Stethoscope className="h-6 w-6" />
          <span className="text-xs">点击「开始检测」检查应用配置健康状况</span>
        </div>
      )}

      {!isInspecting && report && !report.supported && (
        <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-border py-8 text-muted-foreground">
          <CircleSlash className="h-6 w-6" />
          <span className="text-xs">该应用暂不支持检测</span>
        </div>
      )}

      {!isInspecting && report && report.supported && (
        <>
          {report.items.length === 0 ? (
            <div className="rounded-lg border border-dashed border-border py-6 text-center text-xs text-muted-foreground">
              暂无检测项
            </div>
          ) : (
            <>
              <div className="flex flex-wrap items-center gap-x-4 gap-y-1 px-0.5 text-xs">
                <span className="text-emerald-600">
                  {summary.ok} 项正常
                </span>
                {summary.warn > 0 && (
                  <span className="text-amber-600">
                    {summary.warn} 项建议优化
                  </span>
                )}
                {summary.error > 0 && (
                  <span className="text-red-600">
                    {summary.error} 项异常
                  </span>
                )}
              </div>

              <div className="space-y-4">
                {groups.map(({ key, items }) => (
                  <div key={key} className="space-y-2">
                    {key && (
                      <p className="px-0.5 text-xs font-medium text-muted-foreground">
                        {key}
                      </p>
                    )}
                    {items.map((item) => (
                      <InspectItemRow
                        key={item.id}
                        item={item}
                        checked={selected.has(item.id)}
                        onToggle={handleToggle}
                      />
                    ))}
                  </div>
                ))}
              </div>
            </>
          )}

          {fixableItems.length > 0 && (
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="text-xs text-muted-foreground">
                {selected.size > 0
                  ? `已选 ${selected.size} 项`
                  : `${fixableItems.length} 项可自动修复，可直接全部修复`}
              </span>
              <div className="flex items-center gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  className="h-8 gap-1.5 rounded-lg"
                  onClick={handleFixAll}
                  disabled={fixMutation.isPending || isInspecting}
                >
                  {fixMutation.isPending ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Wrench className="h-3.5 w-3.5" />
                  )}
                  修复全部（{fixableItems.length}）
                </Button>
                <Button
                  size="sm"
                  className="h-8 gap-1.5 rounded-lg"
                  onClick={handleFix}
                  disabled={
                    selected.size === 0 ||
                    fixMutation.isPending ||
                    isInspecting
                  }
                >
                  修复选中（{selected.size}）
                </Button>
              </div>
            </div>
          )}
        </>
      )}
    </div>
  );
}
