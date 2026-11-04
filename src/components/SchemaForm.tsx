/**
 * JSON Schema 动态表单组件
 *
 * 按容器分组渲染：schema.containers 定义容器、设置块及字段。
 */

import { useState } from "react";
import { Input } from "@/components/ui/input";
import { PasswordInput } from "@/components/ui/password-input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { DataDirPathInput, SOURCE_SELECT_WIDTH } from "@/components/DataDirPathInput";
import {
  ContainerImageField,
  type ContainerImageConfig,
} from "@/components/ContainerImageField";
import { generatePassword } from "@/lib/utils";
import { useStorageDirectories } from "@/hooks/useSettings";
import type { StorageDirectory } from "@/api/settings";
import { Minus, Plus, ChevronUp, ChevronDown, RefreshCw } from "lucide-react";

/** 数组字段是否包含目录选择（本地路径列需要更宽） */
function hasDirectoryItem(prop: SchemaProperty): boolean {
  const itemProps = prop.items?.properties ?? {};
  return Object.values(itemProps).some(
    (item) => item?.format === "directory"
  );
}

/** 数组行网格：含目录字段时本地路径列更宽；设备映射只有两列，用三列网格 */
function arrayGridClass(hasDirectory: boolean, settingType?: string): string {
  if (hasDirectory) {
    return "grid-cols-[minmax(0,5fr)_minmax(0,2.3fr)_90px_auto]";
  }
  if (settingType === "devices") {
    return "grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]";
  }
  return "grid-cols-[1fr_1fr_90px_auto]";
}

/** Schema 属性定义 */
export interface SchemaProperty {
  type?: string;
  title?: string;
  description?: string;
  default?: unknown;
  enum?: unknown[];
  minimum?: number;
  maximum?: number;
  format?: string;
  items?: {
    type?: string;
    properties?: Record<string, SchemaProperty>;
    required?: string[];
  };
}

/** 容器内设置块 */
export interface ContainerSetting {
  type: "ports" | "volumes" | "env" | "devices" | "advanced";
  title: string;
  description?: string;
  fields: string[];
}

/** 容器配置 */
export interface ContainerConfig {
  name: string;
  title: string;
  description?: string;
  settings: ContainerSetting[];
}

interface SchemaFormProps {
  /** JSON Schema */
  schema: {
    properties?: Record<string, SchemaProperty>;
    required?: string[];
    containers?: ContainerConfig[];
  };
  /** 当前表单数据 */
  data: Record<string, unknown>;
  /** 表单数据变化回调 */
  onChange: (data: Record<string, unknown>) => void;
  /** 主容器的默认镜像（管理端方案或应用市场默认值） */
  image?: string;
  /** 各 Compose 服务的默认镜像，用于逐容器镜像配置 */
  containerImages?: Record<string, string>;
  /** 目录字段选中来源后子路径为空时自动填入的默认值（如实例名称） */
  defaultDirectorySubPath?: string;
  /**
   * 相对路径是否归属应用数据目录（默认 true）。
   * 公共配置等共享目录场景传 false，来源下拉默认留空。
   */
  relativeToAppDataDir?: boolean;
}

function withContainerImage(
  data: Record<string, unknown>,
  containerName: string,
  value: ContainerImageConfig
): Record<string, unknown> {
  const overrides = (data.container_images || {}) as Record<
    string,
    ContainerImageConfig
  >;
  return {
    ...data,
    container_images: { ...overrides, [containerName]: value },
  };
}

/**
 * 目录字段相关服务，由 SchemaForm 顶层统一获取后逐级下发，
 * 避免每个字段实例各自调用 useStorageDirectories。
 */
interface DirectoryServices {
  /** 应用数据目录（is_app_data 的启用记录）；未配置时为 null */
  appDataDir: StorageDirectory | null;
  /** 已启用的普通数据目录，供目录字段的来源下拉使用 */
  storageDirectories: StorageDirectory[];
  /** 相对路径是否归属应用数据目录 */
  relativeToAppDataDir: boolean;
}

interface FieldInputProps {
  propKey: string;
  prop: SchemaProperty;
  value: unknown;
  required: boolean;
  onChange: (key: string, value: unknown) => void;
  hideLabel?: boolean;
  directoryServices: DirectoryServices;
  defaultDirectorySubPath?: string;
}

function FieldInput({
  propKey,
  prop,
  value,
  required,
  onChange,
  hideLabel = false,
  directoryServices,
  defaultDirectorySubPath,
}: FieldInputProps) {
  const label = prop.title || propKey;

  const handleChange = (next: unknown) => {
    onChange(propKey, next);
  };

  const labelNode = (
    <Label
      htmlFor={propKey}
      className="text-xs font-medium"
      aria-required={required}
      title={required ? "必填" : undefined}
    >
      {label}
      {required && (
        <span className="ml-0.5 text-destructive" aria-hidden="true">
          *
        </span>
      )}
    </Label>
  );

  if (prop.type === "boolean") {
    return (
      <div className="flex items-center gap-2 pl-2">
        <input
          id={propKey}
          type="checkbox"
          checked={Boolean(value)}
          onChange={(e) => handleChange(e.target.checked)}
          className="h-3.5 w-3.5 rounded border-input"
        />
        <Label
          htmlFor={propKey}
          className="text-[13px] font-medium"
          aria-required={required}
          title={required ? "必填" : undefined}
        >
          {label}
          {required && (
            <span className="ml-0.5 text-destructive" aria-hidden="true">
              *
            </span>
          )}
        </Label>
      </div>
    );
  }

  if (prop.enum && prop.enum.length > 0) {
    return (
      <div className="space-y-1">
        {!hideLabel && labelNode}
        <select
          id={propKey}
          value={String(value ?? "")}
          onChange={(e) => handleChange(e.target.value)}
          className="flex h-8 w-full rounded-md border border-input bg-white px-2 py-1 text-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
        >
          {prop.enum.map((option) => (
            <option key={String(option)} value={String(option)}>
              {String(option)}
            </option>
          ))}
        </select>
      </div>
    );
  }

  const isPasswordField = prop.format === "password";
  const isDirectoryField = prop.format === "directory";

  const inputType =
    prop.type === "integer" || prop.type === "number" ? "number" : "text";

  const inputNode = (
    <Input
      id={propKey}
      type={inputType}
      value={value === undefined || value === null ? "" : String(value)}
      onChange={(e) => {
        const raw = e.target.value;
        if (prop.type === "integer") {
          handleChange(raw === "" ? "" : parseInt(raw, 10));
        } else if (prop.type === "number") {
          handleChange(raw === "" ? "" : parseFloat(raw));
        } else {
          handleChange(raw);
        }
      }}
      placeholder={hideLabel ? label : prop.description}
      className="h-8 rounded-md border-input bg-white px-2 text-sm shadow-none"
    />
  );

  // 目录选择：目录来源下拉 + 子路径输入框 + 浏览按钮
  if (isDirectoryField) {
    const { appDataDir, storageDirectories, relativeToAppDataDir } =
      directoryServices;
    const currentValue =
      value === undefined || value === null ? "" : String(value);

    return (
      <div className="space-y-1">
        {!hideLabel && labelNode}
        <DataDirPathInput
          id={propKey}
          value={currentValue}
          onChange={(path) => handleChange(path)}
          placeholder={hideLabel ? label : prop.description}
          defaultSubPath={defaultDirectorySubPath}
          appDataDir={appDataDir}
          storageDirectories={storageDirectories}
          relativeToAppDataDir={relativeToAppDataDir}
        />
      </div>
    );
  }

  // 密码生成：输入框（默认隐藏，可切换明文）+ 生成按钮
  if (isPasswordField) {
    return (
      <div className="space-y-1">
        {!hideLabel && labelNode}
        <div className="flex items-center gap-2">
          <PasswordInput
            id={propKey}
            value={value === undefined || value === null ? "" : String(value)}
            onChange={(e) => handleChange(e.target.value)}
            placeholder={hideLabel ? label : prop.description}
            className="h-8 rounded-md border-input bg-white px-2 text-sm shadow-none"
            containerClassName="min-w-0 flex-1"
          />
          <Button
            type="button"
            variant="secondary"
            size="icon"
            className="h-8 w-8 shrink-0 rounded-md"
            onClick={() => handleChange(generatePassword())}
            title="重新生成密码"
          >
            <RefreshCw className="h-4 w-4" />
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-1">
      {!hideLabel && labelNode}
      {inputNode}
    </div>
  );
}

/** 数组字段的标准列顺序；本地路径优先于容器路径，权限放最后。 */
const ARRAY_DISPLAY_ORDERS = [
  ["local_port", "container_port", "protocol"],
  ["host_path", "container_path", "mode"],
  ["host_path", "container_path"],
  ["key", "value"],
];

function orderedArrayEntries(
  itemProps: Record<string, SchemaProperty>
): [string, SchemaProperty][] {
  const entries = Object.entries(itemProps);
  const keys = new Set(entries.map(([key]) => key));
  const preferred = ARRAY_DISPLAY_ORDERS.find(
    (order) =>
      order.length === entries.length && order.every((key) => keys.has(key))
  );
  if (preferred) {
    return preferred.map(
      (key) => [key, itemProps[key]] as [string, SchemaProperty]
    );
  }
  return entries.length <= 1 ? entries : [...entries.slice(1), entries[0]];
}

interface ArrayFieldProps {
  propKey: string;
  prop: SchemaProperty;
  value: unknown;
  required: boolean;
  onChange: (key: string, value: unknown) => void;
  directoryServices: DirectoryServices;
  defaultDirectorySubPath?: string;
}

function ArrayField({
  propKey,
  prop,
  value,
  required,
  onChange,
  directoryServices,
  defaultDirectorySubPath,
}: ArrayFieldProps) {
  const rows = Array.isArray(value) ? (value as Record<string, unknown>[]) : [];
  const items = prop.items || {};
  const itemProps = items.properties || {};
  const itemRequired = new Set(items.required || []);

  const defaultRow = (): Record<string, unknown> => {
    const row: Record<string, unknown> = {};
    for (const [key, p] of Object.entries(itemProps)) {
      if (p && "default" in p) {
        row[key] = p.default;
      } else if (itemRequired.has(key)) {
        row[key] = "";
      }
    }
    return row;
  };

  const updateRows = (newRows: Record<string, unknown>[]) => {
    onChange(propKey, newRows);
  };

  const addRow = (index: number) => {
    const newRows = [...rows];
    newRows.splice(index + 1, 0, defaultRow());
    updateRows(newRows);
  };

  const removeRow = (index: number) => {
    const newRows = rows.filter((_, i) => i !== index);
    updateRows(newRows);
  };

  const updateRow = (index: number, key: string, val: unknown) => {
    const newRows = rows.map((row, i) =>
      i === index ? { ...row, [key]: val } : row
    );
    updateRows(newRows);
  };

  const itemPropEntries = Object.entries(itemProps);
  const isTwoColumnRow = itemPropEntries.length === 2;
  const displayEntries = orderedArrayEntries(itemProps);

  // 使用 display: contents 让每行子元素直接参与外层统一 grid：
  // grid-cols-[1fr_1fr_90px_auto]
  // - 3 字段（ports/volumes）：本地端口/路径、容器端口/路径、协议/权限、按钮
  // - 2 字段（env）：key 占 1 列，value 跨 2-3 列，按钮在第 4 列，不留空白
  return (
    <>
      {rows.length > 0 && (
        <div className="contents">
          {displayEntries.map(([key, p]) => (
            <div
              key={`${propKey}-header-${key}`}
              className={`px-1 text-[11px] font-medium text-muted-foreground ${
                isTwoColumnRow && key === "value" ? "col-span-2" : "col-span-1"
              }`}
            >
              {p.format === "directory" ? (
                // 目录字段拆成「来源下拉 + 子路径」两段，表头按同样比例对齐
                <div className="flex items-center gap-2">
                  <span
                    className="shrink-0 truncate"
                    style={{ width: SOURCE_SELECT_WIDTH }}
                  >
                    {p.title || key}
                    {itemRequired.has(key) && (
                      <span className="ml-0.5 text-destructive" aria-hidden="true">
                        *
                      </span>
                    )}
                  </span>
                  <span
                    className="min-w-0 flex-1 truncate"
                    title="本地路径 = 应用数据目录 / Compose 项目名 / 子目录；项目名由实例名称决定，用于区分同一应用的多个部署"
                  >
                    子目录
                  </span>
                </div>
              ) : (
                <>
                  {p.title || key}
                  {itemRequired.has(key) && (
                    <span className="ml-0.5 text-destructive" aria-hidden="true">
                      *
                    </span>
                  )}
                </>
              )}
            </div>
          ))}
          <div className="col-span-1" />
        </div>
      )}
      {rows.map((row, index) => (
        <div key={`${propKey}-row-${index}`} className="contents">
          {displayEntries.map(([key, p]) => {
            // key/value 数组中，根据 key 的值推断 value 的语义
            let prop = p;
            if (
              key === "value" &&
              "key" in itemProps &&
              row.key !== undefined
            ) {
              const keyValue = String(row.key).toLowerCase();
              if (keyValue.includes("password") || keyValue.includes("pass")) {
                prop = { ...p, format: "password" };
              }
            }
            const colSpan =
              isTwoColumnRow && key === "value" ? "col-span-2" : "col-span-1";
            return (
              <div key={key} className={colSpan}>
                <FieldInput
                  propKey={key}
                  prop={prop}
                  value={row[key]}
                  required={itemRequired.has(key)}
                  onChange={(_, val) => updateRow(index, key, val)}
                  hideLabel
                  directoryServices={directoryServices}
                  defaultDirectorySubPath={defaultDirectorySubPath}
                />
              </div>
            );
          })}
          <div className="col-span-1 flex items-center justify-end gap-2 mr-2">
            <Button
              type="button"
              variant="secondary"
              size="icon"
              className="h-8 w-8 rounded-sm"
              onClick={() => removeRow(index)}
              disabled={required && rows.length <= 1}
            >
              <Minus className="h-4 w-4" />
            </Button>
            <Button
              type="button"
              variant="secondary"
              size="icon"
              className="h-8 w-8 rounded-sm"
              onClick={() => addRow(index)}
            >
              <Plus className="h-4 w-4" />
            </Button>
          </div>
        </div>
      ))}
      {rows.length === 0 && (
        <div className="contents">
          <div className="col-start-4 col-span-1 flex items-center justify-end gap-2 mr-2">
            <Button
              type="button"
              variant="secondary"
              size="icon"
              className="h-8 w-8 rounded-sm"
              onClick={() => addRow(0)}
            >
              <Plus className="h-4 w-4" />
            </Button>
          </div>
        </div>
      )}
    </>
  );
}

/** 新格式：按容器分组渲染 */
function ContainerSchemaForm({
  schema,
  data,
  onChange,
  image,
  containerImages,
  directoryServices,
  defaultDirectorySubPath,
}: {
  schema: SchemaFormProps["schema"];
  data: Record<string, unknown>;
  onChange: (data: Record<string, unknown>) => void;
  image?: string;
  containerImages?: Record<string, string>;
  directoryServices: DirectoryServices;
  defaultDirectorySubPath?: string;
}) {
  const properties = schema.properties || {};
  const requiredSet = new Set(schema.required || []);
  const containers = schema.containers || [];

  // 记录用户手动收起的区块；schema 异步变化时新出现的区块默认展开，
  // 避免 useState(new Set(allKeys)) 初始化后不再同步的问题
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());

  const toggleSection = (key: string) => {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const handleChange = (key: string, value: unknown) => {
    onChange({ ...data, [key]: value });
  };

  const renderFields = (fields: string[], settingType?: string) => {
    // 端口/存储空间：3 字段，协议/权限列占 90px，按钮在 auto 列
    // 环境变量：2 字段，按钮直接放 auto 列，由相同按钮组自然对齐，不留空白列
    // 设备映射：2 字段且没有协议列，用两列 + 按钮的三列网格
    // 存储空间含目录选择，第一列需要更宽，避免目录来源和子路径被挤得看不清
    const hasDirectory = fields.some((key) => {
      const prop = properties[key];
      return prop?.type === "array" && prop.items && hasDirectoryItem(prop);
    });
    const gridClass = arrayGridClass(Boolean(hasDirectory), settingType);
    return (
      <div className={`grid ${gridClass} gap-2`}>
        {fields
          .filter((key) => properties[key])
          .map((key) => {
            const prop = properties[key];
            if (prop.type === "array" && prop.items) {
              return (
                <ArrayField
                  key={key}
                  propKey={key}
                  prop={prop}
                  value={data[key]}
                  required={requiredSet.has(key)}
                  onChange={handleChange}
                  directoryServices={directoryServices}
                  defaultDirectorySubPath={defaultDirectorySubPath}
                />
              );
            }
            return (
              <div key={key} className="col-span-4">
                <FieldInput
                  propKey={key}
                  prop={prop}
                  value={data[key]}
                  required={requiredSet.has(key)}
                  onChange={handleChange}
                  hideLabel
                  directoryServices={directoryServices}
                  defaultDirectorySubPath={defaultDirectorySubPath}
                />
              </div>
            );
          })}
      </div>
    );
  };

  return (
    <div className="space-y-4">
      {containers.map((container) => {
        // 逐容器镜像：主容器（第一个服务）优先用外部传入的默认镜像
        // （管理端方案或应用市场默认值），其余容器用模板解析出的镜像
        const mainContainerName = containers[0]?.name;
        const defaultContainerImage =
          container.name === mainContainerName && image
            ? image
            : containerImages?.[container.name] ??
              (containers.length === 1 ? image : undefined);

        return (
          <section
            key={container.name}
            className="rounded-xl border bg-card p-4 shadow-sm"
          >
            <div className="mb-3">
              <h3 className="text-sm font-semibold text-card-foreground">
                {container.title}
              </h3>
              {container.description && (
                <p className="text-xs text-muted-foreground leading-snug">
                  {container.description}
                </p>
              )}
            </div>

            {defaultContainerImage !== undefined && (
              <ContainerImageField
                containerName={container.name}
                defaultImage={defaultContainerImage}
                value={
                  (data.container_images as
                    | Record<string, ContainerImageConfig>
                    | undefined)?.[container.name]
                }
                onChange={(value) =>
                  onChange(withContainerImage(data, container.name, value))
                }
              />
            )}

            <div className="divide-y">
              {container.settings
                .filter(
                  (setting) =>
                    setting.fields &&
                    setting.fields.some((key) => properties[key])
                )
                .map((setting) => {
                  // image_tag 已在容器顶部单独渲染，不再在 advanced 分组中重复渲染
                  const fields = setting.fields.filter(
                    (key) => key !== "image_tag"
                  );
                  if (fields.length === 0) return null;

                  const sectionKey = `${container.name}-${setting.type}`;
                  const isExpanded = !collapsed.has(sectionKey);
                  return (
                    <div
                      key={sectionKey}
                      className="py-3 first:pt-0 last:pb-0"
                    >
                      <button
                        type="button"
                        onClick={() => toggleSection(sectionKey)}
                        className="flex items-center gap-1.5 text-left group"
                      >
                        {isExpanded ? (
                          <ChevronUp className="h-3.5 w-3.5 text-primary transition-transform" />
                        ) : (
                          <ChevronDown className="h-3.5 w-3.5 text-primary transition-transform" />
                        )}
                        <h4 className="text-xs font-semibold text-primary group-hover:underline">
                          {setting.title}
                        </h4>
                      </button>
                      {isExpanded && (
                        <div className="mt-2">
                          {setting.description && (
                            <p className="mb-2 text-xs text-muted-foreground leading-snug">
                              {setting.description}
                            </p>
                          )}
                          {renderFields(fields, setting.type)}
                        </div>
                      )}
                    </div>
                  );
                })}
            </div>
          </section>
        );
      })}
    </div>
  );
}

/** 无容器分组时按 properties 平铺渲染 */
function PlainSchemaForm({
  schema,
  data,
  onChange,
  image,
  containerImages,
  directoryServices,
  defaultDirectorySubPath,
}: {
  schema: SchemaFormProps["schema"];
  data: Record<string, unknown>;
  onChange: (data: Record<string, unknown>) => void;
  image?: string;
  containerImages?: Record<string, string>;
  directoryServices: DirectoryServices;
  defaultDirectorySubPath?: string;
}) {
  const properties = schema.properties || {};
  const requiredSet = new Set(schema.required || []);

  const handleChange = (key: string, value: unknown) => {
    onChange({ ...data, [key]: value });
  };

  const defaultContainerImage = Object.entries(containerImages || {})[0];
  const imageContainerName = defaultContainerImage?.[0] ?? "main";
  const defaultImage = image ?? defaultContainerImage?.[1];

  return (
    <div className="space-y-4">
      {defaultImage && (
        <ContainerImageField
          containerName={imageContainerName}
          defaultImage={defaultImage}
          value={
            (data.container_images as
              | Record<string, ContainerImageConfig>
              | undefined)?.[imageContainerName]
          }
          onChange={(value) =>
            onChange(withContainerImage(data, imageContainerName, value))
          }
        />
      )}
      {Object.entries(properties)
        .filter(([key]) => key !== "image_tag" || !defaultImage)
        .map(([key, prop]) => {
          // 数组类型（如环境变量、端口映射）走 ArrayField，避免被当成普通文本渲染
          if (prop.type === "array" && prop.items) {
            const required = requiredSet.has(key);
            return (
              <div key={key} className="space-y-1">
                <Label
                  className="text-xs font-medium"
                  aria-required={required}
                  title={required ? "必填" : undefined}
                >
                  {prop.title || key}
                  {required && (
                    <span className="ml-0.5 text-destructive" aria-hidden="true">
                      *
                    </span>
                  )}
                </Label>
                {prop.description && (
                  <p className="text-xs text-muted-foreground leading-snug">
                    {prop.description}
                  </p>
                )}
                <div
                  className={`grid ${arrayGridClass(hasDirectoryItem(prop))} gap-2`}
                >
                  <ArrayField
                    propKey={key}
                    prop={prop}
                    value={data[key]}
                    required={required}
                    onChange={handleChange}
                    directoryServices={directoryServices}
                    defaultDirectorySubPath={defaultDirectorySubPath}
                  />
                </div>
              </div>
            );
          }
          return (
            <FieldInput
              key={key}
              propKey={key}
              prop={prop}
              value={data[key]}
              required={requiredSet.has(key)}
              onChange={handleChange}
              directoryServices={directoryServices}
              defaultDirectorySubPath={defaultDirectorySubPath}
            />
          );
        })}
    </div>
  );
}

export function SchemaForm({
  schema,
  data,
  onChange,
  image,
  containerImages,
  defaultDirectorySubPath,
  relativeToAppDataDir = true,
}: SchemaFormProps) {
  const hasContainers = schema?.containers && schema.containers.length > 0;

  // 目录字段依赖的存储目录列表统一在顶层获取一次：
  // is_app_data 的启用记录作为应用数据目录来源，其余启用记录为数据目录
  const { data: storageDirectoriesData } = useStorageDirectories();
  const enabledDirectories = (storageDirectoriesData ?? []).filter(
    (d) => d.enabled
  );
  const directoryServices: DirectoryServices = {
    appDataDir: enabledDirectories.find((d) => d.is_app_data) ?? null,
    storageDirectories: enabledDirectories.filter((d) => !d.is_app_data),
    relativeToAppDataDir,
  };

  if (!hasContainers) {
    return (
      <PlainSchemaForm
        schema={schema}
        data={data}
        onChange={onChange}
        image={image}
        containerImages={containerImages}
        directoryServices={directoryServices}
        defaultDirectorySubPath={defaultDirectorySubPath}
      />
    );
  }

  return (
    <ContainerSchemaForm
      schema={schema}
      data={data}
      onChange={onChange}
      image={image}
      containerImages={containerImages}
      directoryServices={directoryServices}
      defaultDirectorySubPath={defaultDirectorySubPath}
    />
  );
}
