/**
 * JSON Schema 表单工具函数
 *
 * 用于从 Schema 提取默认值、为密码字段自动生成密码等。
 */

import { generatePassword } from "@/lib/utils";

/** 带 items 的 Schema 属性定义 */
export interface SchemaPropertyWithItems extends Record<string, unknown> {
  format?: string;
  type?: string;
  items?: {
    properties?: Record<string, SchemaPropertyWithItems>;
  };
  default?: unknown;
}

/**
 * 从 JSON Schema 中提取默认值。
 *
 * - 有 default 的字段使用 default 值
 * - required 且无 default 的字段用空字符串占位，避免 schema 校验立即失败
 */
export function extractDefaults(
  schema: Record<string, unknown>
): Record<string, unknown> {
  const defaults: Record<string, unknown> = {};
  const properties = schema?.properties as
    | Record<string, { default?: unknown }>
    | undefined;
  const required = new Set((schema?.required as string[] | undefined) || []);
  if (properties) {
    for (const [key, prop] of Object.entries(properties)) {
      if (prop && "default" in prop) {
        defaults[key] = prop.default;
      } else if (required.has(key)) {
        defaults[key] = "";
      }
    }
  }
  return defaults;
}

/**
 * 遍历 defaults，为空密码字段自动生成密码。
 *
 * 包括：format === "password" 的字段，以及 key/value 数组中 key 包含
 * password/pass 且 value 为空的项。
 */
export function fillEmptyPasswords(
  schema: Record<string, unknown>,
  defaults: Record<string, unknown>
): Record<string, unknown> {
  const properties = schema?.properties as
    | Record<string, SchemaPropertyWithItems>
    | undefined;
  if (!properties) return defaults;

  const filled = structuredClone(defaults);

  for (const [key, prop] of Object.entries(properties)) {
    if (prop.format === "password") {
      const value = filled[key];
      if (value === "" || value === undefined || value === null) {
        filled[key] = generatePassword();
      }
      continue;
    }

    if (
      prop.type === "array" &&
      prop.items?.properties &&
      "key" in prop.items.properties &&
      "value" in prop.items.properties
    ) {
      const rows = Array.isArray(filled[key])
        ? (filled[key] as Record<string, unknown>[])
        : [];
      filled[key] = rows.map((row) => {
        const keyValue = String(row?.key || "").toLowerCase();
        const value = row?.value;
        if (
          (keyValue.includes("password") || keyValue.includes("pass")) &&
          (value === "" || value === undefined || value === null)
        ) {
          return { ...row, value: generatePassword() };
        }
        return row;
      });
    }
  }

  return filled;
}

/**
 * 统计配置中需要分配的端口数量。
 *
 * 顺序与后端 app_service._validate_and_prepare 一致：
 * 先按 schema.properties 的 key 顺序统计单端口字段
 * （type 为 integer 且 key 含 port，且 config 中值不为 null），
 * 再按 ports 数组顺序统计 local_port 不为 null 的项。
 */
export function countSchemaPorts(
  schema: Record<string, unknown>,
  config: Record<string, unknown>
): number {
  // 固定端口应用（如 DNS 必须用 53）不参与顺序分配，也不消耗建议端口
  if (schema?.["x-fixed-ports"]) return 0;
  let count = 0;
  const properties = schema?.properties as
    | Record<string, { type?: string }>
    | undefined;
  if (properties) {
    for (const [key, prop] of Object.entries(properties)) {
      if (
        key.toLowerCase().includes("port") &&
        prop?.type === "integer" &&
        config[key] !== null &&
        config[key] !== undefined
      ) {
        count += 1;
      }
    }
  }
  const portsValue = config["ports"];
  if (Array.isArray(portsValue)) {
    for (const entry of portsValue) {
      if (!entry || typeof entry !== "object") continue;
      const localPort = (entry as Record<string, unknown>)["local_port"];
      if (localPort !== null && localPort !== undefined) {
        count += 1;
      }
    }
  }
  return count;
}

/**
 * 将建议端口按顺序写入配置的端口字段（不修改原对象）。
 *
 * 写入顺序与后端 _validate_and_prepare 一致（同 countSchemaPorts）：
 * 先单端口字段，再 ports 数组的 local_port。
 * 返回新配置与下一个可用的建议端口下标，供多个应用连续分配。
 */
export function applyPortSuggestions(
  schema: Record<string, unknown>,
  config: Record<string, unknown>,
  ports: number[],
  offset: number
): { config: Record<string, unknown>; nextOffset: number } {
  const next = structuredClone(config);
  if (schema?.["x-fixed-ports"]) {
    return { config: next, nextOffset: offset };
  }
  let cursor = offset;

  const properties = schema?.properties as
    | Record<string, { type?: string }>
    | undefined;
  if (properties) {
    for (const [key, prop] of Object.entries(properties)) {
      if (
        key.toLowerCase().includes("port") &&
        prop?.type === "integer" &&
        next[key] !== null &&
        next[key] !== undefined &&
        cursor < ports.length
      ) {
        next[key] = ports[cursor];
        cursor += 1;
      }
    }
  }

  const portsValue = next["ports"];
  if (Array.isArray(portsValue)) {
    for (const entry of portsValue) {
      if (!entry || typeof entry !== "object") continue;
      const record = entry as Record<string, unknown>;
      if (record["local_port"] === null || record["local_port"] === undefined) {
        continue;
      }
      if (cursor >= ports.length) break;
      record["local_port"] = ports[cursor];
      cursor += 1;
    }
  }

  return { config: next, nextOffset: cursor };
}

/** Schema containers 中的设置项定义 */
interface SchemaSetting {
  type?: string;
  fields?: string[];
}

interface SchemaContainer {
  name?: string;
  settings?: SchemaSetting[];
}

/** 各合并字段对应的去重键 */
const DEDUP_KEY_BY_SETTING_TYPE: Record<string, string> = {
  volumes: "container_path",
  env: "key",
};

/** 从 schema 的 containers 定义中提取指定类型设置项的字段名列表 */
function getSettingFields(
  schema: Record<string, unknown> | undefined,
  settingType: string,
  containerName?: string
): string[] {
  const containers = (schema?.containers as SchemaContainer[] | undefined) || [];
  const fields: string[] = [];
  for (const container of containers) {
    if (containerName && container.name !== containerName) continue;
    for (const setting of container.settings || []) {
      if (setting.type === settingType) {
        fields.push(...(setting.fields || []));
      }
    }
  }
  return fields;
}

/** 找到应用主容器定义：优先 name 等于应用名，否则取第一个容器 */
function findMainContainer(
  schema: Record<string, unknown> | undefined,
  appName: string
): SchemaContainer | undefined {
  const containers = (schema?.containers as SchemaContainer[] | undefined) || [];
  if (containers.length === 0) return undefined;
  return containers.find((c) => c.name === appName) || containers[0];
}

/** 合并两个对象数组，按 dedupKey 去重，primary 中的项优先 */
function mergeArrayByKey(
  primary: unknown,
  extra: unknown[],
  dedupKey: string
): Record<string, unknown>[] {
  const merged: Record<string, unknown>[] = [];
  const seen = new Set<unknown>();
  const primaryItems = Array.isArray(primary) ? primary : [];
  for (const item of [...primaryItems, ...extra]) {
    if (!item || typeof item !== "object") continue;
    const record = item as Record<string, unknown>;
    const marker = record[dedupKey];
    if (seen.has(marker)) continue;
    seen.add(marker);
    merged.push(record);
  }
  return merged;
}

/**
 * 为共享配置项打标：volumes 项附加 shared 标记，应用模板据此跳过
 * project_name 前缀，使相对路径直接相对于 Docker 挂载目录。
 */
function markSharedItems(
  items: unknown[],
  settingType: string
): unknown[] {
  if (settingType !== "volumes") return items;
  return items.map((item) =>
    item && typeof item === "object"
      ? { ...(item as Record<string, unknown>), shared: true }
      : item
  );
}

/**
 * 将编排共享配置合并进单个应用的配置。
 *
 * 与浅合并不同，共享的 volumes（存储映射）/ env（环境变量）数组会追加到
 * 应用主容器对应的字段中，按 container_path / key 去重，应用自身配置优先。
 * 共享 volumes 项会附加 shared 标记（模板跳过 project_name 前缀）。
 * 与后端 services/orchestration/shared_config.py 的逻辑保持一致。
 */
export function mergeSharedConfig(
  sharedConfig: Record<string, unknown>,
  appConfig: Record<string, unknown>,
  sharedSchema: Record<string, unknown> | undefined,
  appSchema: Record<string, unknown> | undefined,
  appName: string
): Record<string, unknown> {
  const merged: Record<string, unknown> = { ...sharedConfig, ...appConfig };
  if (Object.keys(sharedConfig).length === 0) return merged;

  const mainContainer = findMainContainer(appSchema, appName);

  for (const [settingType, dedupKey] of Object.entries(
    DEDUP_KEY_BY_SETTING_TYPE
  )) {
    const sharedFields = getSettingFields(sharedSchema, settingType);
    if (sharedFields.length === 0) continue;
    const appFields = getSettingFields(appSchema, settingType, mainContainer?.name);
    if (appFields.length === 0) continue;
    for (const sharedField of sharedFields) {
      const sharedItems = sharedConfig[sharedField];
      if (!Array.isArray(sharedItems)) continue;
      for (const appField of appFields) {
        merged[appField] = mergeArrayByKey(
          appConfig[appField],
          markSharedItems(sharedItems, settingType),
          dedupKey
        );
      }
    }
  }

  return merged;
}
