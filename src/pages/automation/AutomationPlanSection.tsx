/**
 * 自动化方案展示块
 *
 * 单方案分类居中展示（大图标 + 方案名 + 说明 + 流程）；
 * 多方案分类纵向堆叠同样的结构，每个方案各自带部署入口，
 * 不按方案数量排成多列——3 个方案时两列会空一格，列数越多越难扫读。
 */

import type { ReactNode } from "react";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { Boxes, Import, Rocket } from "lucide-react";
import type { AppOrchestration } from "@/hooks/useOrchestrations";

interface AutomationPlanSectionProps {
  orchestration: AppOrchestration;
  onDeploy: (orchestration: AppOrchestration) => void;
  onImport?: (orchestration: AppOrchestration) => void;
  /** 应用流程节点，由分类页用 AppIcon + StageFlow 渲染 */
  flow?: ReactNode;
  /** 该方案已部署的实例数 */
  instanceCount?: number;
  /**
   * single：分类下唯一的方案，居中大图标展示（版本徽标在页头显示）；
   * stacked：多方案纵向堆叠，使用紧凑标题行并自带版本徽标。
   */
  variant?: "single" | "stacked";
  className?: string;
}

export function AutomationPlanSection({
  orchestration,
  onDeploy,
  onImport,
  flow,
  instanceCount = 0,
  variant = "stacked",
  className,
}: AutomationPlanSectionProps) {
  const single = variant === "single";
  const requiredCount = orchestration.app_composition.filter(
    (item) => item.relation === "required"
  ).length;
  const optionalCount = orchestration.app_composition.filter(
    (item) => item.relation === "optional" || item.relation === "suggested"
  ).length;
  const iconClass = single ? "h-16 w-16 rounded-2xl" : "h-11 w-11 rounded-xl";

  return (
    <section className={cn("flex flex-col", className)}>
      <div
        className={
          single
            ? "flex flex-col items-center text-center"
            : "flex items-start gap-4"
        }
      >
        {orchestration.icon ? (
          <img
            src={
              orchestration.icon.startsWith("http") ||
              orchestration.icon.startsWith("/")
                ? orchestration.icon
                : `/api/orchestrations/${orchestration.name}/icon`
            }
            alt=""
            className={cn(
              "shrink-0 border border-border/60 bg-white object-contain",
              iconClass
            )}
          />
        ) : (
          <div
            className={cn(
              "flex shrink-0 items-center justify-center border border-border/60 bg-gradient-to-br from-primary/15 to-primary/5 text-primary",
              iconClass
            )}
          >
            <Boxes className={single ? "h-8 w-8" : "h-5 w-5"} />
          </div>
        )}

        <div
          className={cn(
            "min-w-0",
            single ? "flex flex-col items-center" : "flex-1"
          )}
        >
          <div
            className={cn(
              "flex flex-wrap items-center gap-2",
              single && "justify-center"
            )}
          >
            <h3
              className={cn(
                "font-semibold text-foreground",
                single ? "text-lg" : "text-base"
              )}
            >
              {orchestration.display_name}
            </h3>
            {!single && (
              <Badge
                variant="outline"
                className="h-4 shrink-0 px-1.5 py-0 text-[10px] font-normal tabular-nums text-muted-foreground"
              >
                v{orchestration.version}
              </Badge>
            )}
            {instanceCount > 0 && (
              <Badge
                variant="secondary"
                className="h-4 shrink-0 px-1.5 py-0 text-[10px] font-normal"
              >
                已部署 {instanceCount} 个
              </Badge>
            )}
          </div>
          <p
            className={cn(
              "mt-2 text-sm text-muted-foreground",
              single
                ? "max-w-md text-center leading-relaxed"
                : "line-clamp-2"
            )}
          >
            {orchestration.description || "暂无描述"}
          </p>
        </div>
      </div>

      {flow && (
        <div
          className={cn(
            "rounded-xl border border-border/60 bg-muted/20",
            single ? "mt-7 px-4 py-7 sm:px-8" : "mt-5 px-4 py-6 sm:px-6"
          )}
        >
          {flow}
        </div>
      )}

      <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
        <p className="text-xs text-muted-foreground">
          {requiredCount > 0 && <span>必选应用 {requiredCount} 个</span>}
          {requiredCount > 0 && optionalCount > 0 && (
            <span className="mx-1">·</span>
          )}
          {optionalCount > 0 && <span>可选应用 {optionalCount} 个</span>}
        </p>
        <div className="flex items-center gap-2">
          {onImport && (
            <Button
              variant="outline"
              size="sm"
              onClick={() => onImport(orchestration)}
              className="h-8 gap-1.5 rounded-full px-4 text-xs"
            >
              <Import className="h-3.5 w-3.5" />
              导入
            </Button>
          )}
          <Button
            size="sm"
            onClick={() => onDeploy(orchestration)}
            className="h-8 gap-1.5 rounded-full px-4 text-xs"
          >
            <Rocket className="h-3.5 w-3.5" />
            部署
          </Button>
        </div>
      </div>
    </section>
  );
}
