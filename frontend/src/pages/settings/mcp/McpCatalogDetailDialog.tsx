import { useState } from "react";
import {
  AlertCircle,
  Braces,
  FileJson,
  Loader2,
  RefreshCw,
  ShieldAlert,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
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
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  useMcpCatalogDetail,
  type McpCatalogDetail,
  type McpCatalogEntry,
} from "@/hooks/useMcp";
import { McpSchemaType, McpSchemaView } from "./McpSchemaView";
import {
  McpEntryStatus,
  McpKindBadge,
  McpMethodBadge,
  McpRiskBadge,
} from "./McpCatalogBadges";

type JsonObject = Record<string, unknown>;

function asObject(value: unknown): JsonObject | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as JsonObject)
    : null;
}

function formatJson(value: unknown) {
  try {
    return JSON.stringify(value, null, 2) ?? "null";
  } catch {
    return String(value);
  }
}

function formatValue(value: unknown) {
  if (value === undefined || value === null || value === "") return "-";
  if (typeof value === "string") return value;
  return formatJson(value);
}

function parameterSchema(parameter: JsonObject, components: JsonObject) {
  const schema = asObject(parameter.schema) ?? {};
  const reference = schema.$ref;
  if (typeof reference !== "string" || !reference.startsWith("#/components/")) {
    return schema;
  }
  const target = reference
    .slice(2)
    .split("/")
    .reduce<unknown>(
      (current, part) => asObject(current)?.[part],
      { components }
    );
  return { ...asObject(target), ...schema };
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

function ParametersSection({ detail }: { detail: McpCatalogDetail }) {
  if (detail.parameters.length === 0) {
    return (
      <Section title="参数">
        <p className="text-xs text-muted-foreground">该接口没有声明参数。</p>
      </Section>
    );
  }

  return (
    <Section
      title={`参数 (${detail.parameters.length})`}
      description="参数位置标注在名称下方；嵌套类型可在原始 JSON 中查看。"
    >
      <div className="hidden overflow-hidden rounded-lg border sm:block">
        <Table className="table-fixed">
          <TableHeader>
            <TableRow>
              <TableHead className="w-[19%]">名称</TableHead>
              <TableHead className="w-[16%]">类型</TableHead>
              <TableHead className="w-[9%]">必填</TableHead>
              <TableHead className="w-[14%]">默认值</TableHead>
              <TableHead className="w-[17%]">枚举</TableHead>
              <TableHead className="w-[25%]">说明</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {detail.parameters.map((parameter, index) => {
              const schema = parameterSchema(parameter, detail.components);
              const enumValues = Array.isArray(schema.enum) ? schema.enum : [];
              const name = formatValue(parameter.name);
              return (
                <TableRow key={`${name}-${index}`}>
                  <TableCell className="align-top whitespace-normal">
                    <code className="break-all font-mono text-xs font-medium">
                      {name}
                    </code>
                    <div className="mt-1 text-[11px] text-muted-foreground">
                      {formatValue(parameter.in)}
                    </div>
                  </TableCell>
                  <TableCell className="align-top break-all whitespace-normal">
                    <McpSchemaType
                      schema={schema}
                      components={detail.components}
                    />
                  </TableCell>
                  <TableCell className="align-top whitespace-normal">
                    {parameter.required === true ? "是" : "否"}
                  </TableCell>
                  <TableCell className="align-top break-all whitespace-normal font-mono text-[11px]">
                    {formatValue(schema.default)}
                  </TableCell>
                  <TableCell className="align-top break-all whitespace-normal font-mono text-[11px]">
                    {enumValues.length > 0
                      ? enumValues.map(formatValue).join(", ")
                      : "-"}
                  </TableCell>
                  <TableCell className="align-top whitespace-normal text-xs leading-relaxed text-muted-foreground">
                    {formatValue(
                      parameter.description ?? schema.description
                    )}
                  </TableCell>
                </TableRow>
              );
            })}
          </TableBody>
        </Table>
      </div>

      <div className="space-y-2 sm:hidden">
        {detail.parameters.map((parameter, index) => {
          const schema = parameterSchema(parameter, detail.components);
          const enumValues = Array.isArray(schema.enum) ? schema.enum : [];
          return (
            <div
              key={`${formatValue(parameter.name)}-${index}`}
              className="space-y-2 rounded-lg border bg-muted/20 p-3"
            >
              <div className="flex flex-wrap items-center gap-2">
                <code className="break-all font-mono text-xs font-medium">
                  {formatValue(parameter.name)}
                </code>
                <Badge variant="secondary">{formatValue(parameter.in)}</Badge>
                {parameter.required === true && <Badge>必填</Badge>}
              </div>
              <dl className="grid grid-cols-[64px_minmax(0,1fr)] gap-x-2 gap-y-1.5 text-xs">
                <dt className="text-muted-foreground">类型</dt>
                <dd className="min-w-0 break-all">
                  <McpSchemaType
                    schema={schema}
                    components={detail.components}
                  />
                </dd>
                <dt className="text-muted-foreground">默认值</dt>
                <dd className="min-w-0 break-all font-mono">
                  {formatValue(schema.default)}
                </dd>
                <dt className="text-muted-foreground">枚举</dt>
                <dd className="min-w-0 break-all font-mono">
                  {enumValues.length > 0
                    ? enumValues.map(formatValue).join(", ")
                    : "-"}
                </dd>
                <dt className="text-muted-foreground">说明</dt>
                <dd className="min-w-0 leading-relaxed text-muted-foreground">
                  {formatValue(parameter.description ?? schema.description)}
                </dd>
              </dl>
            </div>
          );
        })}
      </div>
    </Section>
  );
}

function ContentSchemas({
  content,
  components,
}: {
  content: unknown;
  components: JsonObject;
}) {
  const media = asObject(content) ?? {};
  const entries = Object.entries(media);
  if (entries.length === 0) {
    return <p className="text-xs text-muted-foreground">未声明内容 Schema。</p>;
  }
  return (
    <div className="space-y-3">
      {entries.map(([mediaType, value]) => {
        const mediaObject = asObject(value) ?? {};
        return (
          <div key={mediaType} className="min-w-0 space-y-2">
            <Badge variant="secondary" className="max-w-full font-mono font-normal break-all">
              {mediaType}
            </Badge>
            <McpSchemaView
              schema={mediaObject.schema ?? {}}
              components={components}
            />
          </div>
        );
      })}
    </div>
  );
}

function ApiSchemas({ detail }: { detail: McpCatalogDetail }) {
  return (
    <>
      <ParametersSection detail={detail} />
      <Section title="请求体">
        {detail.request_body ? (
          <div className="space-y-3">
            <div className="flex flex-wrap items-center gap-2">
              {detail.request_body.required === true && <Badge>必填</Badge>}
              {typeof detail.request_body.description === "string" && (
                <p className="text-xs leading-relaxed text-muted-foreground">
                  {detail.request_body.description}
                </p>
              )}
            </div>
            <ContentSchemas
              content={detail.request_body.content}
              components={detail.components}
            />
          </div>
        ) : (
          <p className="text-xs text-muted-foreground">该接口没有请求体。</p>
        )}
      </Section>
      <Section title={`响应 (${Object.keys(detail.responses).length})`}>
        {Object.keys(detail.responses).length === 0 ? (
          <p className="text-xs text-muted-foreground">未声明响应。</p>
        ) : (
          <div className="space-y-3">
            {Object.entries(detail.responses).map(([status, value]) => {
              const response = asObject(value) ?? {};
              const content = asObject(response.content) ?? {};
              return (
                <div key={status} className="min-w-0 rounded-lg border p-3">
                  <div className="mb-2 flex flex-wrap items-center gap-2">
                    <Badge variant="outline" className="font-mono">
                      {status}
                    </Badge>
                    <span className="text-xs text-muted-foreground">
                      {formatValue(response.description)}
                    </span>
                  </div>
                  {Object.keys(content).length > 0 ? (
                    <ContentSchemas
                      content={content}
                      components={detail.components}
                    />
                  ) : (
                    <CodeBlock
                      code={formatJson(response)}
                      showCopy={false}
                      className="max-h-56"
                    />
                  )}
                </div>
              );
            })}
          </div>
        )}
      </Section>
    </>
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
            {entry?.title || entry?.name || "接口详情"}
          </DialogTitle>
          <DialogDescription className="break-words leading-relaxed">
            {entry?.description || "查看接口契约、Schema 与权限状态。"}
          </DialogDescription>
        </DialogHeader>

        {detailQuery.isLoading ? (
          <div className="flex min-h-48 flex-1 items-center justify-center gap-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            正在加载接口详情...
          </div>
        ) : detailQuery.error || !detail ? (
          <div className="flex min-h-48 flex-1 flex-col items-center justify-center gap-3 text-center">
            <AlertCircle className="h-6 w-6 text-destructive" aria-hidden="true" />
            <p className="text-sm text-destructive">接口详情加载失败，请重试。</p>
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
                <McpMethodBadge method={detail.method} />
                <McpKindBadge kind={detail.kind} />
                <McpRiskBadge risk={detail.risk} kind={detail.kind} />
                <Badge variant="outline">{detail.transport}</Badge>
                <Badge variant="outline">
                  {detail.required_role === "admin"
                    ? "管理员"
                    : detail.required_role === "user"
                      ? "登录用户"
                      : "公开"}
                </Badge>
              </div>
              <dl className="grid min-w-0 gap-3 text-xs sm:grid-cols-2">
                <div className="min-w-0">
                  <dt className="mb-1 text-muted-foreground">
                    {detail.kind === "onetap_api" ? "operationId" : "原生工具名"}
                  </dt>
                  <dd className="break-all font-mono text-foreground">{detail.name}</dd>
                </div>
                <div className="min-w-0">
                  <dt className="mb-1 text-muted-foreground">公开工具名</dt>
                  <dd className="break-all font-mono text-foreground">
                    {detail.public_name}
                  </dd>
                </div>
                <div className="min-w-0">
                  <dt className="mb-1 text-muted-foreground">来源 / 领域</dt>
                  <dd className="break-words text-foreground">
                    {detail.source_name} / {detail.domain}
                  </dd>
                </div>
                <div className="min-w-0">
                  <dt className="mb-1 text-muted-foreground">路径</dt>
                  <dd className="break-all font-mono text-foreground">
                    {detail.path || "MCP 工具，无 HTTP 路径"}
                  </dd>
                </div>
              </dl>
              <div>
                <div className="mb-1 text-xs text-muted-foreground">权限状态</div>
                <McpEntryStatus entry={detail} />
              </div>
              {detail.tags.length > 0 && (
                <div className="flex flex-wrap gap-1">
                  {detail.tags.map((tag) => (
                    <Badge key={tag} variant="secondary" className="font-normal">
                      {tag}
                    </Badge>
                  ))}
                </div>
              )}
            </Section>

            {detail.kind === "remote_tool" && detail.risk === "unknown" && (
              <div className="flex items-start gap-2 rounded-xl border border-amber-500/30 bg-amber-500/10 p-3 text-xs leading-relaxed text-amber-800 dark:text-amber-300">
                <ShieldAlert className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
                <span>
                  {detail.annotations
                    ? "上游 annotations 未提供可读性风险提示，当前风险未知。"
                    : "上游未声明 annotations，无法自动判断只读或写入风险。"}
                  新增授权前需要管理员确认。
                </span>
              </div>
            )}

            {detail.kind === "onetap_api" ? (
              <ApiSchemas detail={detail} />
            ) : (
              <ToolSchemas detail={detail} />
            )}

            {detail.security && (
              <Section title="安全要求 (security)">
                <CodeBlock code={formatJson(detail.security)} className="max-h-56" />
              </Section>
            )}

            <Section title="原始 JSON" description="完整详情包含 OpenAPI components，可用于核对引用。">
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
