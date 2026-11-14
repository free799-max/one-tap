import { Badge } from "@/components/ui/badge";
import type { McpCatalogEntry } from "@/hooks/useMcp";
import { cn } from "@/lib/utils";

const RISK_STYLES: Record<string, string> = {
  read: "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
  write: "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-400",
  destructive: "border-destructive/30 bg-destructive/10 text-destructive",
  secret: "border-destructive/30 bg-destructive/10 text-destructive",
  control_plane: "border-destructive/30 bg-destructive/10 text-destructive",
  unsupported: "border-border bg-muted text-muted-foreground",
  unknown: "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-400",
};

const RISK_LABELS: Record<string, string> = {
  read: "只读",
  write: "写入",
  destructive: "破坏性",
  secret: "敏感",
  control_plane: "控制面",
  unsupported: "不支持",
  unknown: "未知",
};

const KIND_LABELS: Record<string, string> = {
  onetap_api: "OneTap API",
  local_tool: "本地工具",
  remote_tool: "远程 MCP",
};

const METHOD_STYLES: Record<string, string> = {
  GET: "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
  HEAD: "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400",
  POST: "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-400",
  PUT: "border-sky-500/30 bg-sky-500/10 text-sky-700 dark:text-sky-400",
  PATCH: "border-sky-500/30 bg-sky-500/10 text-sky-700 dark:text-sky-400",
  DELETE: "border-destructive/30 bg-destructive/10 text-destructive",
  MCP: "border-violet-500/30 bg-violet-500/10 text-violet-700 dark:text-violet-400",
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

export function McpMethodBadge({ method }: { method: string }) {
  return (
    <Badge
      variant="outline"
      className={cn(
        "min-w-14 justify-center font-mono text-[10px] font-semibold",
        METHOD_STYLES[method] ?? "border-border bg-muted text-muted-foreground"
      )}
    >
      {method || "MCP"}
    </Badge>
  );
}

export function McpEntryStatus({ entry }: { entry: McpCatalogEntry }) {
  const reason =
    entry.blocked_reason ||
    (entry.status_reason === "未授权" ? "" : entry.status_reason) ||
    (!entry.invocable ? "当前接口不可调用" : "");
  return (
    <div className="min-w-0 space-y-1">
      <div className="flex flex-wrap gap-1">
        <Badge
          variant="outline"
          className={cn(
            "font-normal",
            entry.enabled
              ? "border-sky-500/30 bg-sky-500/10 text-sky-700 dark:text-sky-400"
              : "bg-muted text-muted-foreground"
          )}
        >
          {entry.enabled ? "已授权" : "未授权"}
        </Badge>
        <Badge
          variant="outline"
          className={cn(
            "font-normal",
            entry.effective
              ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400"
              : "bg-muted text-muted-foreground"
          )}
        >
          {entry.effective ? "实际生效" : "未生效"}
        </Badge>
      </div>
      {reason && (
        <p
          className="break-words text-[11px] leading-relaxed text-muted-foreground"
          title={reason}
        >
          {reason}
        </p>
      )}
    </div>
  );
}
