/**
 * 方案版本徽标
 *
 * 展示 `v{version}`；方案带更新说明时，点击徽标可展开查看。
 */

import { Badge } from "@/components/ui/badge";
import {
  Popover,
  PopoverContent,
  PopoverTitle,
  PopoverTrigger,
} from "@/components/ui/popover";
import { cn } from "@/lib/utils";
import { Info } from "lucide-react";
import type { AppOrchestration } from "@/hooks/useOrchestrations";

interface OrchestrationVersionBadgeProps {
  orchestration: Pick<AppOrchestration, "display_name" | "version" | "changelog">;
  /** 是否在徽标中附带方案名称（多方案场景下便于区分） */
  showName?: boolean;
  className?: string;
}

export function OrchestrationVersionBadge({
  orchestration,
  showName = false,
  className,
}: OrchestrationVersionBadgeProps) {
  const version = orchestration.version || "未知";
  const changelog = orchestration.changelog?.trim();

  if (!changelog) {
    return (
      <Badge
        variant="secondary"
        className={cn("font-normal tabular-nums", className)}
        title="方案版本"
      >
        {showName && <span className="truncate">{orchestration.display_name}</span>}
        v{version}
      </Badge>
    );
  }

  return (
    <Popover>
      <PopoverTrigger
        className="shrink-0 rounded-full outline-none focus-visible:ring-2 focus-visible:ring-ring"
        title="查看本次更新内容"
      >
        <Badge
          variant="secondary"
          className={cn(
            "cursor-pointer gap-1 font-normal tabular-nums",
            className
          )}
        >
          {showName && (
            <span className="max-w-[10rem] truncate">
              {orchestration.display_name}
            </span>
          )}
          v{version}
          <Info className="h-3 w-3 opacity-60" />
        </Badge>
      </PopoverTrigger>
      <PopoverContent className="w-80">
        <PopoverTitle className="flex items-center gap-2 pr-6">
          <span className="truncate">{orchestration.display_name}</span>
          <Badge variant="outline" className="shrink-0 font-normal tabular-nums">
            v{version}
          </Badge>
        </PopoverTitle>
        <div className="mt-2 max-h-64 overflow-y-auto whitespace-pre-wrap text-xs leading-relaxed text-muted-foreground">
          {changelog}
        </div>
      </PopoverContent>
    </Popover>
  );
}
