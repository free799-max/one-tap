import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

type JsonObject = Record<string, unknown>;

function asObject(value: unknown): JsonObject | null {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? (value as JsonObject)
    : null;
}

function stringify(value: unknown) {
  if (value === undefined) return "-";
  if (typeof value === "string") return value;
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

function resolvePointer(root: unknown, reference: string) {
  if (!reference.startsWith("#/")) return undefined;
  return reference
    .slice(2)
    .split("/")
    .map((part) => part.replace(/~1/g, "/").replace(/~0/g, "~"))
    .reduce<unknown>((current, part) => asObject(current)?.[part], root);
}

function resolvedObject(schema: unknown, root: unknown): JsonObject {
  const object = asObject(schema) ?? {};
  const reference = object.$ref;
  if (typeof reference !== "string") return object;
  const target = asObject(resolvePointer(root, reference));
  if (!target) return object;
  const siblings = Object.fromEntries(
    Object.entries(object).filter(([key]) => key !== "$ref")
  );
  return { ...target, ...siblings };
}

function schemaTypeLabel(schema: unknown, root: unknown, depth = 0): string {
  const object = asObject(schema);
  if (!object) return typeof schema;
  if (depth > 5) return "schema";
  const reference = object.$ref;
  if (typeof reference === "string") {
    const target = resolvePointer(root, reference);
    if (target !== undefined) {
      return schemaTypeLabel(
        { ...resolvedObject(object, root), $ref: undefined },
        root,
        depth + 1
      );
    }
    return reference.split("/").pop() ?? "引用";
  }
  const type = object.type;
  const base = Array.isArray(type)
    ? type.map(String).join(" | ")
    : typeof type === "string"
      ? type
      : object.properties
        ? "object"
        : object.items
          ? "array"
          : object.enum
            ? "enum"
            : object.oneOf || object.anyOf || object.allOf
              ? "组合"
              : "any";
  if (base === "array" && object.items) {
    return `array<${schemaTypeLabel(object.items, root, depth + 1)}>`;
  }
  const format = typeof object.format === "string" ? object.format : "";
  return format ? `${base} (${format})` : base;
}

function InlineValue({ label, value }: { label: string; value: unknown }) {
  if (value === undefined) return null;
  return (
    <div className="flex min-w-0 flex-wrap items-start gap-1.5 text-[11px]">
      <span className="shrink-0 text-muted-foreground">{label}</span>
      <code className="min-w-0 break-all rounded bg-muted px-1.5 py-0.5 font-mono text-foreground">
        {stringify(value)}
      </code>
    </div>
  );
}

interface McpSchemaTypeProps {
  schema: unknown;
  components?: JsonObject;
  root?: unknown;
}

export function McpSchemaType({
  schema,
  components,
  root,
}: McpSchemaTypeProps) {
  const currentRoot = root ?? (components ? { components } : schema);
  return (
    <span className="font-mono text-[11px] text-muted-foreground">
      {schemaTypeLabel(schema, currentRoot)}
    </span>
  );
}

interface McpSchemaViewProps extends McpSchemaTypeProps {
  depth?: number;
  seenRefs?: string[];
}

export function McpSchemaView({
  schema,
  components,
  root,
  depth = 0,
  seenRefs = [],
}: McpSchemaViewProps) {
  const currentRoot = root ?? (components ? { components } : schema);
  const original = asObject(schema);
  if (!original) {
    return (
      <code className="break-all rounded bg-muted px-1.5 py-0.5 font-mono text-xs">
        {stringify(schema)}
      </code>
    );
  }

  const reference = original.$ref;
  if (typeof reference === "string") {
    if (seenRefs.includes(reference)) {
      return (
        <Badge variant="outline" className="font-mono font-normal">
          递归引用 {reference.split("/").pop()}
        </Badge>
      );
    }
    const target = resolvePointer(currentRoot, reference);
    if (target !== undefined) {
      const siblings = Object.fromEntries(
        Object.entries(original).filter(([key]) => key !== "$ref")
      );
      return (
        <McpSchemaView
          schema={{ ...asObject(target), ...siblings }}
          root={currentRoot}
          depth={depth}
          seenRefs={[...seenRefs, reference]}
        />
      );
    }
  }

  const object = resolvedObject(original, currentRoot);
  const properties = asObject(object.properties) ?? {};
  const required = new Set(
    Array.isArray(object.required) ? object.required.map(String) : []
  );
  const description =
    typeof object.description === "string" ? object.description : "";
  const enumValues = Array.isArray(object.enum) ? object.enum : [];
  const compositions = [
    ["allOf", "全部满足"],
    ["oneOf", "任选其一"],
    ["anyOf", "任一满足"],
  ] as const;

  if (depth > 8) {
    return (
      <pre className="max-h-48 overflow-auto rounded-md bg-muted p-2 font-mono text-[11px] whitespace-pre-wrap break-all">
        {JSON.stringify(object, null, 2)}
      </pre>
    );
  }

  return (
    <div className={cn("min-w-0 space-y-1.5", depth > 0 && "text-xs")}>
      <div className="flex min-w-0 flex-wrap items-center gap-1.5">
        <Badge variant="outline" className="font-mono text-[10px] font-normal">
          <McpSchemaType schema={object} root={currentRoot} />
        </Badge>
        {object.nullable === true && (
          <span className="text-[11px] text-muted-foreground">可为 null</span>
        )}
      </div>
      {description && (
        <p className="text-xs leading-relaxed whitespace-pre-wrap text-muted-foreground">
          {description}
        </p>
      )}
      <InlineValue label="默认值" value={object.default} />
      <InlineValue label="示例" value={object.example} />
      {enumValues.length > 0 && (
        <div className="flex min-w-0 flex-wrap items-center gap-1 text-[11px]">
          <span className="text-muted-foreground">枚举</span>
          {enumValues.map((value, index) => (
            <code
              key={`${stringify(value)}-${index}`}
              className="break-all rounded bg-muted px-1.5 py-0.5 font-mono"
            >
              {stringify(value)}
            </code>
          ))}
        </div>
      )}

      {Object.keys(properties).length > 0 && (
        <div className="space-y-1.5 pt-1">
          {Object.entries(properties).map(([name, propertySchema]) => (
            <div
              key={name}
              className="min-w-0 rounded-md border border-border/70 bg-background/60 p-2"
            >
              <div className="mb-1 flex flex-wrap items-center gap-1.5">
                <code className="break-all font-mono text-xs font-medium text-foreground">
                  {name}
                </code>
                {required.has(name) && (
                  <Badge variant="secondary" className="h-4 px-1 text-[10px]">
                    必填
                  </Badge>
                )}
              </div>
              <McpSchemaView
                schema={propertySchema}
                root={currentRoot}
                depth={depth + 1}
                seenRefs={seenRefs}
              />
            </div>
          ))}
        </div>
      )}

      {object.items !== undefined && (
        <div className="min-w-0 rounded-md border border-dashed p-2">
          <div className="mb-1 text-[11px] font-medium text-muted-foreground">
            数组项
          </div>
          <McpSchemaView
            schema={object.items}
            root={currentRoot}
            depth={depth + 1}
            seenRefs={seenRefs}
          />
        </div>
      )}

      {asObject(object.additionalProperties) && (
        <div className="min-w-0 rounded-md border border-dashed p-2">
          <div className="mb-1 text-[11px] font-medium text-muted-foreground">
            附加属性值
          </div>
          <McpSchemaView
            schema={object.additionalProperties}
            root={currentRoot}
            depth={depth + 1}
            seenRefs={seenRefs}
          />
        </div>
      )}

      {compositions.map(([key, label]) => {
        const variants = Array.isArray(object[key]) ? object[key] : [];
        if (variants.length === 0) return null;
        return (
          <div key={key} className="space-y-1.5 rounded-md border border-dashed p-2">
            <div className="text-[11px] font-medium text-muted-foreground">
              {label}
            </div>
            {variants.map((variant, index) => (
              <div key={index} className="rounded bg-muted/30 p-2">
                <div className="mb-1 text-[10px] text-muted-foreground">
                  Schema {index + 1}
                </div>
                <McpSchemaView
                  schema={variant}
                  root={currentRoot}
                  depth={depth + 1}
                  seenRefs={seenRefs}
                />
              </div>
            ))}
          </div>
        );
      })}
    </div>
  );
}
