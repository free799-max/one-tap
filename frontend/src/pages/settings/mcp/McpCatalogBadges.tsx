import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

const RISK_STYLES: Record<string, string> = {
  read: "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
  write: "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-400",
  destructive: "border-destructive/30 bg-destructive/10 text-destructive",
  unknown: "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-400",
};

const RISK_LABELS: Record<string, string> = {
  read: "只读",
  write: "写入",
  destructive: "破坏性",
  unknown: "未知",
};

const KIND_LABELS: Record<string, string> = {
  local_tool: "OneTap 网关",
  remote_tool: "原生 MCP 透传",
};

export function McpRiskBadge({
  risk,
  kind,
  className,
}: {
  risk: string;
  kind?: string;
  className?: string;
}) {
  const label =
    kind === "remote_tool" && risk === "unknown"
      ? "未声明"
      : (RISK_LABELS[risk] ?? risk);
  return (
    <Badge
      variant="outline"
      className={cn("font-normal", RISK_STYLES[risk], className)}
      title={
        kind === "remote_tool" && risk === "unknown"
          ? "上游未声明风险 annotations，授权前需要确认"
          : undefined
      }
    >
      {label}
    </Badge>
  );
}

export function McpKindBadge({ kind }: { kind: string }) {
  return (
    <Badge variant="secondary" className="font-normal">
      {KIND_LABELS[kind] ?? kind}
    </Badge>
  );
}
