import { useState } from "react";
import {
  AlertCircle,
  Braces,
  FileJson,
  Loader2,
  RefreshCw,
  ShieldAlert,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { CodeBlock } from "@/components/ui/code-block";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  useMcpCatalogDetail,
  type McpCatalogDetail,
  type McpCatalogEntry,
} from "@/hooks/useMcp";
import { McpSchemaView } from "./McpSchemaView";
import {
  McpKindBadge,
  McpRiskBadge,
} from "./McpCatalogBadges";

function formatJson(value: unknown) {
  try {
    return JSON.stringify(value, null, 2) ?? "null";
  } catch {
    return String(value);
  }
}

function Section({
  title,
  description,
  children,
}: {
  title: string;
  description?: string;
  children: React.ReactNode;
}) {
  return (
    <section className="min-w-0 space-y-2 rounded-xl border bg-card p-3">
      <div>
        <h3 className="text-sm font-medium text-foreground">{title}</h3>
        {description && (
          <p className="mt-0.5 text-xs text-muted-foreground">{description}</p>
        )}
      </div>
      {children}
    </section>
  );
}

function ToolSchemas({ detail }: { detail: McpCatalogDetail }) {
  return (
    <>
      <Section title="输入 Schema (input_schema)">
        {detail.input_schema ? (
          <McpSchemaView schema={detail.input_schema} />
        ) : (
          <p className="text-xs text-muted-foreground">未声明输入 Schema。</p>
        )}
      </Section>
      <Section title="输出 Schema (output_schema)">
        {detail.output_schema ? (
          <McpSchemaView schema={detail.output_schema} />
        ) : (
          <p className="text-xs text-muted-foreground">未声明输出 Schema。</p>
        )}
      </Section>
      {detail.annotations && (
        <Section title="工具 annotations">
          <CodeBlock
            code={formatJson(detail.annotations)}
            className="max-h-64"
          />
        </Section>
      )}
    </>
  );
}

export function McpCatalogDetailDialog({
  entry,
  onClose,
}: {
  entry: McpCatalogEntry | null;
  onClose: () => void;
}) {
  const detailQuery = useMcpCatalogDetail(entry?.entry_id ?? null, entry !== null);
  const [showRaw, setShowRaw] = useState(false);
  const detail = detailQuery.data;

  return (
    <Dialog open={entry !== null} onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="flex max-h-[92dvh] flex-col overflow-hidden sm:max-w-4xl">
        <DialogHeader className="shrink-0 pr-7">
          <DialogTitle className="break-words leading-snug">
            {entry?.title || entry?.name || "工具详情"}
          </DialogTitle>
          <DialogDescription className="break-words leading-relaxed">
            {entry?.description || "查看原生 MCP 工具的 Schema 与风险信息。"}
          </DialogDescription>
        </DialogHeader>

        {detailQuery.isLoading ? (
          <div className="flex min-h-48 flex-1 items-center justify-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            正在加载工具详情...
          </div>
        ) : detailQuery.error || !detail ? (
          <div className="flex min-h-48 flex-1 flex-col items-center justify-center gap-3 text-center">
            <AlertCircle className="h-6 w-6 text-destructive" aria-hidden="true" />
            <p className="text-sm text-destructive">工具详情加载失败，请重试。</p>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => detailQuery.refetch()}
            >
              <RefreshCw aria-hidden="true" />
              重新加载
            </Button>
          </div>
        ) : (
          <div className="min-h-0 flex-1 space-y-3 overflow-y-auto pr-1">
            <Section title="基本信息">
              <div className="flex flex-wrap items-center gap-1.5">
                <McpKindBadge kind={detail.kind} />
                <McpRiskBadge risk={detail.risk} kind={detail.kind} />
              </div>
              <dl className="grid min-w-0 gap-3 text-xs sm:grid-cols-2">
                <div className="min-w-0">
                  <dt className="mb-1 text-muted-foreground">原生工具名</dt>
                  <dd className="break-all font-mono text-foreground">{detail.name}</dd>
                </div>
                <div className="min-w-0">
                  <dt className="mb-1 text-muted-foreground">公开工具名</dt>
                  <dd className="break-all font-mono text-sky-700 dark:text-sky-400">
                    {detail.public_name}
                  </dd>
                </div>
                <div className="min-w-0">
                  <dt className="mb-1 text-muted-foreground">来源</dt>
                  <dd className="break-words text-foreground">{detail.source_name}</dd>
                </div>
                <div className="min-w-0">
                  <dt className="mb-1 text-muted-foreground">领域</dt>
                  <dd className="break-words text-foreground">{detail.domain}</dd>
                </div>
              </dl>
            </Section>

            {detail.risk === "unknown" && (
              <div className="flex items-start gap-2 rounded-xl border border-amber-500/30 bg-amber-500/10 p-3 text-xs leading-relaxed text-amber-800 dark:text-amber-300">
                <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
                <span>
                  {detail.annotations
                    ? "上游 annotations 未提供可读性风险提示，当前风险未知。"
                    : "上游未声明 annotations，无法自动判断只读或写入风险。"}
                </span>
              </div>
            )}

            <ToolSchemas detail={detail} />

            <Section
              title="原始 JSON"
              description="完整详情包含输入/输出 Schema 与 annotations，可用于核对上游声明。"
            >
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => setShowRaw((current) => !current)}
                aria-expanded={showRaw}
              >
                {showRaw ? (
                  <Braces aria-hidden="true" />
                ) : (
                  <FileJson aria-hidden="true" />
                )}
                {showRaw ? "收起原始 JSON" : "查看并复制原始 JSON"}
              </Button>
              {showRaw && (
                <CodeBlock code={formatJson(detail)} className="mt-2 max-h-80" />
              )}
            </Section>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
