import type { SchemaProperty } from "@/components/SchemaForm";

/** 根据字段 schema 类型生成合适的默认值 */
export function getDefaultValue(prop: SchemaProperty): unknown {
  if (prop && "default" in prop) {
    return prop.default;
  }
  if (prop.enum && prop.enum.length > 0) {
    return prop.enum[0];
  }
  switch (prop.type) {
    case "integer":
    case "number":
      return 0;
    case "boolean":
      return false;
    case "string":
    default:
      return "";
  }
}
