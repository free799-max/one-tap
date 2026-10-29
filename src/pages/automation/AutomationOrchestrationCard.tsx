/**
 * 自动化组合模板卡片
 */

import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import type { AppOrchestration } from "@/hooks/useOrchestrations";

interface AutomationOrchestrationCardProps {
  orchestration: AppOrchestration;
  onDeploy: (orchestration: AppOrchestration) => void;
}

export function AutomationOrchestrationCard({
  orchestration,
  onDeploy,
}: AutomationOrchestrationCardProps) {
  const requiredCount = orchestration.app_composition.filter(
    (item) => item.relation === "required"
  ).length;
  const optionalCount = orchestration.app_composition.filter(
    (item) => item.relation === "optional" || item.relation === "suggested"
  ).length;

  return (
    <Card className="rounded-xl p-5 border border-border/80 shadow-sm hover:shadow-[0_4px_14px_rgba(0,0,0,0.08)] hover:border-border transition-all h-full flex flex-col">
      <CardContent className="p-0 flex flex-col h-full">
        <div className="flex items-start gap-4">
          <div className="shrink-0">
            {orchestration.icon ? (
              <img
                src={
                  orchestration.icon.startsWith("http") ||
                  orchestration.icon.startsWith("/")
                    ? orchestration.icon
                    : `/api/orchestrations/${orchestration.name}/icon`
                }
                alt={orchestration.display_name}
                className="h-14 w-14 rounded-xl object-contain bg-white border border-border/60"
              />
            ) : (
              <div className="h-14 w-14 rounded-xl bg-muted flex items-center justify-center text-2xl border border-border/60">
                🚀
              </div>
            )}
          </div>

          <div className="flex-1 min-w-0">
            <h3 className="text-base font-semibold text-foreground truncate">
              {orchestration.display_name}
            </h3>
            <p className="text-sm text-muted-foreground line-clamp-2 mt-1">
              {orchestration.description || "暂无描述"}
            </p>
          </div>
        </div>

        <div className="mt-3 text-xs text-muted-foreground">
          {requiredCount > 0 && (
            <span>必选应用 {requiredCount} 个</span>
          )}
          {requiredCount > 0 && optionalCount > 0 && (
            <span className="mx-1">·</span>
          )}
          {optionalCount > 0 && (
            <span>可选应用 {optionalCount} 个</span>
          )}
        </div>

        <div className="mt-auto pt-4 flex justify-end">
          <Button
            variant="outline"
            size="sm"
            onClick={() => onDeploy(orchestration)}
            className="rounded-full px-4 h-7 text-xs border-primary/30 text-primary hover:bg-primary/5 hover:text-primary hover:border-primary/40"
          >
            部署
          </Button>
        </div>
      </CardContent>
    </Card>
  );
}
