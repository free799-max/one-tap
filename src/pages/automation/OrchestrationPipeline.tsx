/**
 * 编排模板流水线可视化
 *
 * 将应用组合按 group 合并为流程阶段，以「编号步骤卡片 + 连接箭头」
 * 横向展示整条自动化链路；同组多应用（如多个播放器）并排展示，
 * 并根据 conflict_with 标注「二选一」或「可多选」。
 */

import { Fragment } from "react";
import { ArrowRight } from "lucide-react";
import { AppIcon } from "../apps/AppIcon";
import type { App } from "@/hooks/useApps";
import type { AppCompositionItem } from "@/hooks/useOrchestrations";
import { cn } from "@/lib/utils";
import { RELATION_LABELS } from "./composition-meta";

/** 流程阶段：同一 group 的连续应用合并为同一阶段（如多个播放器） */
interface Stage {
  group: string | null;
  items: AppCompositionItem[];
}

/**
 * 将应用组合按 group 合并为流程阶段：
 * 连续且 group 相同的应用归为同一阶段，其余应用各自独立成阶段。
 */
function buildStages(composition: AppCompositionItem[]): Stage[] {
  const stages: Stage[] = [];
  for (const item of composition) {
    const last = stages[stages.length - 1];
    if (item.group && last && last.group === item.group) {
      last.items.push(item);
    } else {
      stages.push({ group: item.group ?? null, items: [item] });
    }
  }
  return stages;
}

/**
 * 判断同组应用是否互斥（二选一）：
 * 仅当组内每个应用都通过 conflict_with 与其它同组应用互斥时才成立。
 */
function isMutuallyExclusive(items: AppCompositionItem[]): boolean {
  if (items.length < 2) return false;
  return items.every((a) =>
    items.every(
      (b) => a === b || (a.conflict_with?.includes(b.app_name) ?? false)
    )
  );
}

function StageAppNode({
  item,
  appMap,
}: {
  item: AppCompositionItem;
  appMap: Record<string, App>;
}) {
  const app = appMap[item.app_name];
  return (
    <div className="flex w-16 flex-col items-center gap-1.5">
      {app ? (
        <AppIcon
          app={app}
          className="h-11 w-11 rounded-[24%] object-cover shadow-sm ring-1 ring-foreground/5"
        />
      ) : (
        <div className="h-11 w-11 rounded-[24%] bg-gradient-to-br from-primary/15 to-primary/5 shadow-sm ring-1 ring-foreground/5" />
      )}
      <span className="w-full text-center text-[11px] leading-tight text-foreground/80 line-clamp-2">
        {app?.display_name || item.app_name}
      </span>
    </div>
  );
}

export function OrchestrationPipeline({
  composition,
  appMap,
}: {
  composition: AppCompositionItem[];
  appMap: Record<string, App>;
}) {
  const stages = buildStages(composition);

  return (
    <div className="flex flex-wrap items-stretch gap-y-3">
      {stages.map((stage, index) => {
        const relation = stage.items[0]?.relation;
        const isRequired = !stage.group && relation === "required";
        const mutex = isMutuallyExclusive(stage.items);
        const caption = stage.group
          ? `${stage.group} · ${mutex ? "二选一" : "可多选"}`
          : RELATION_LABELS[relation ?? "optional"];

        return (
          <Fragment key={`${stage.group ?? "single"}-${index}`}>
            {index > 0 && (
              <div className="flex items-center px-1.5 text-muted-foreground/40">
                <ArrowRight className="h-4 w-4" />
              </div>
            )}
            <div
              className={cn(
                "flex flex-col gap-2.5 rounded-xl border px-3 py-2.5",
                isRequired
                  ? "border-primary/25 bg-primary/[0.04]"
                  : "border-border/70 bg-muted/30"
              )}
            >
              <div className="flex items-center gap-1.5">
                <span
                  className={cn(
                    "flex h-4 w-4 items-center justify-center rounded-full text-[10px] font-semibold leading-none",
                    isRequired
                      ? "bg-primary text-primary-foreground"
                      : "bg-muted-foreground/15 text-muted-foreground"
                  )}
                >
                  {index + 1}
                </span>
                <span
                  className={cn(
                    "text-[11px] leading-none",
                    isRequired ? "text-primary/80" : "text-muted-foreground"
                  )}
                >
                  {caption}
                </span>
              </div>
              <div className="flex items-start gap-2.5">
                {stage.items.map((item) => (
                  <StageAppNode
                    key={item.app_name}
                    item={item}
                    appMap={appMap}
                  />
                ))}
              </div>
            </div>
          </Fragment>
        );
      })}
    </div>
  );
}
