/**
 * 数据目录路径输入组件
 *
 * 结构：目录来源下拉 + 子路径输入框 + 浏览按钮。
 * 值约定（受控 value/onChange 传完整路径字符串）：
 * - 选中存储目录（含应用数据目录）：value = `mount_path/子路径`（容器视角绝对路径）
 * - 相对路径：默认按应用数据目录下的子路径处理，与后端部署解析保持一致；
 *   共享目录场景（relativeToAppDataDir=false）下拉留空，相对值仅作为子路径展示
 * - 无法匹配任何已配置目录的绝对路径：下拉留空，原样保留不静默改写
 *   （手输路径由后端部署校验兜底）
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { DirectoryPicker } from "@/components/DirectoryPicker";
import type { StorageDirectory } from "@/api/settings";
import {
  useSettingsDirectories,
  useSettingsCreateDirectory,
  useSettingsRenameDirectory,
  useSettingsDeleteDirectory,
} from "@/hooks/useSettings";
import { FolderOpen } from "lucide-react";

/** 目录来源解析结果 */
type ParsedSource =
  | { kind: "app"; dir: StorageDirectory; subPath: string }
  | { kind: "storage"; dir: StorageDirectory; subPath: string }
  | { kind: "custom" };

/** 去掉路径末尾多余的斜杠 */
function trimTrailingSlash(path: string): string {
  return path.replace(/\/+$/, "") || "/";
}

/** 判断 value 是否等于 base 或位于 base 之下，是则返回剩余子路径 */
function matchPrefix(value: string, base: string): string | null {
  const normalizedBase = trimTrailingSlash(base);
  if (!normalizedBase || normalizedBase === "/") return null;
  if (value === normalizedBase) return "";
  if (value.startsWith(`${normalizedBase}/`)) {
    return value.slice(normalizedBase.length + 1);
  }
  return null;
}

/**
 * 解析当前值所属的目录来源（应用数据目录按 is_app_data 记录的 mount_path 前缀匹配）。
 * relativeToAppDataDir=false 时相对路径不归属应用数据目录，下拉留空、按自定义路径展示。
 */
function parseSource(
  value: string,
  appDataDir: StorageDirectory | null,
  storageDirectories: StorageDirectory[],
  relativeToAppDataDir: boolean
): ParsedSource {
  if (value.startsWith("/")) {
    // 优先匹配挂载路径最长的目录（应用数据目录与普通数据目录一同参与匹配）
    const candidates = appDataDir
      ? [appDataDir, ...storageDirectories]
      : storageDirectories;
    const sorted = [...candidates].sort(
      (a, b) => b.mount_path.length - a.mount_path.length
    );
    for (const dir of sorted) {
      const sub = matchPrefix(value, dir.mount_path);
      if (sub !== null) {
        return dir.is_app_data
          ? { kind: "app", dir, subPath: sub }
          : { kind: "storage", dir, subPath: sub };
      }
    }
  }
  // 相对路径按应用数据目录下的子路径处理，与后端部署时的解析保持一致
  if (relativeToAppDataDir && appDataDir && value && !value.startsWith("/")) {
    return {
      kind: "app",
      dir: appDataDir,
      subPath: value.replace(/^\.?\//, ""),
    };
  }
  // 绝对路径未匹配任何已配置目录：按自定义路径原样保留
  return { kind: "custom" };
}

interface DataDirPathInputProps {
  id?: string;
  /** 完整路径字符串 */
  value: string;
  onChange: (value: string) => void;
  placeholder?: string;
  /** 选中目录来源后，子路径为空时自动填入的默认值（如实例名称） */
  defaultSubPath?: string;
  /** 应用数据目录（is_app_data 的启用记录）；为 null 时不提供该来源选项 */
  appDataDir: StorageDirectory | null;
  /** 已启用的普通数据目录列表 */
  storageDirectories: StorageDirectory[];
  /**
   * 相对路径是否归属应用数据目录（默认 true）。
   * 公共配置里的共享目录传 false：下拉默认留空，只展示子路径。
   */
  relativeToAppDataDir?: boolean;
}

export function DataDirPathInput({
  id,
  value,
  onChange,
  placeholder,
  defaultSubPath,
  appDataDir,
  storageDirectories,
  relativeToAppDataDir = true,
}: DataDirPathInputProps) {
  const [pickerOpen, setPickerOpen] = useState(false);

  // 目录浏览/新建/重命名/删除统一走设置端点（后端容器内视角）
  const settingsCreateDirectory = useSettingsCreateDirectory();
  const settingsRenameDirectory = useSettingsRenameDirectory();
  const settingsDeleteDirectory = useSettingsDeleteDirectory();

  const source = useMemo(
    () =>
      parseSource(value, appDataDir, storageDirectories, relativeToAppDataDir),
    [value, appDataDir, storageDirectories, relativeToAppDataDir]
  );

  // 自定义路径无法对应任何已配置目录时，下拉留空（不展示「自定义路径」）
  const selectValue =
    source.kind === "custom"
      ? ""
      : source.kind === "app"
      ? "app"
      : String(source.dir.id);

  // 子路径输入框展示值：自定义模式展示原始值（不静默改写），
  // 存储目录/应用数据目录模式展示去掉 mount_path 前缀后的子路径
  const subPath = source.kind === "custom" ? value : source.subPath;

  /** 由来源与子路径拼装完整 value */
  const buildValue = useCallback(
    (nextSource: string, nextSub: string): string => {
      const sub = nextSub.replace(/^\/+/, "");
      const dir =
        nextSource === "app"
          ? appDataDir
          : storageDirectories.find((d) => String(d.id) === nextSource);
      if (!dir) return nextSub;
      const base = trimTrailingSlash(dir.mount_path);
      return sub ? `${base}/${sub}` : base;
    },
    [appDataDir, storageDirectories]
  );

  const handleSourceChange = (nextSource: string) => {
    if (!nextSource) return;
    // 切换来源时保留已有子路径：相对值（如共享目录的 host_path）可直接作为新来源的子路径；
    // 自定义绝对路径无法映射到新来源，才回退到默认子路径
    const currentSub =
      source.kind === "custom"
        ? value.startsWith("/")
          ? defaultSubPath ?? ""
          : value.replace(/^\/+/, "") || defaultSubPath || ""
        : source.subPath || defaultSubPath || "";
    onChange(buildValue(nextSource, currentSub));
  };

  const handleSubPathChange = (nextSub: string) => {
    if (source.kind === "custom") {
      // 自定义路径：子路径输入框即完整值，原样保留
      onChange(nextSub);
      return;
    }
    onChange(buildValue(selectValue, nextSub));
  };

  // 已选目录来源但子路径为空时，首次进入自动补上默认子路径（如实例名称）；
  // 之后用户主动清空子路径则保留空值，不再反复填回
  const defaultSubPathApplied = useRef(false);
  useEffect(() => {
    if (defaultSubPathApplied.current || !defaultSubPath) return;
    // 绝对自定义路径保持原样
    if (source.kind === "custom" && value.startsWith("/")) {
      defaultSubPathApplied.current = true;
      return;
    }
    if (source.kind === "custom") {
      // 公共配置等共享目录场景：相对值只作为子路径展示，来源下拉保持留空
      if (!relativeToAppDataDir) {
        defaultSubPathApplied.current = true;
        if (value !== defaultSubPath) onChange(defaultSubPath);
        return;
      }
      // 相对路径属于应用数据目录，等目录列表加载后再替换默认子路径
      if (!appDataDir) return;
      defaultSubPathApplied.current = true;
      onChange(buildValue("app", defaultSubPath));
      return;
    }
    defaultSubPathApplied.current = true;
    // 相对默认值或空子路径都替换为实例名称，用户已填写的路径保持不变
    if (!source.subPath || !value.startsWith("/")) {
      onChange(buildValue(selectValue, defaultSubPath));
    }
  }, [
    value,
    source,
    selectValue,
    defaultSubPath,
    appDataDir,
    relativeToAppDataDir,
    onChange,
    buildValue,
  ]);

  const currentDir = source.kind === "custom" ? null : source.dir;
  const sourceLabel =
    source.kind === "custom"
      ? ""
      : `${source.dir.name}（${source.dir.mount_path}）`;

  return (
    <div className="space-y-1">
      <div className="flex items-center gap-2">
        <select
          aria-label="目录来源"
          title={sourceLabel}
          value={selectValue}
          onChange={(e) => handleSourceChange(e.target.value)}
          className="h-8 min-w-0 w-[58%] shrink-0 rounded-md border border-input bg-white px-2 py-1 text-sm transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring"
        >
          {appDataDir && (
            <option value="app">应用数据目录（{appDataDir.mount_path}）</option>
          )}
          {storageDirectories.map((dir) => (
            <option key={dir.id} value={String(dir.id)}>
              {dir.name}（{dir.mount_path}）
            </option>
          ))}
          {source.kind === "custom" && <option value="" hidden />}
        </select>
        <Input
          id={id}
          type="text"
          title={value}
          value={subPath}
          onChange={(e) => handleSubPathChange(e.target.value)}
          placeholder={
            source.kind === "custom"
              ? placeholder
              : "子路径（可选）"
          }
          className="h-8 min-w-0 flex-1 rounded-md border-input bg-white px-2 text-sm shadow-none"
        />
        <Button
          type="button"
          variant="secondary"
          size="icon"
          className="h-8 w-8 shrink-0 rounded-md"
          onClick={() => setPickerOpen(true)}
          title="选择目录"
        >
          <FolderOpen className="h-4 w-4" />
        </Button>
      </div>
      {currentDir && !currentDir.visible && (
        <p className="text-xs text-amber-600">
          该目录在后端容器内不可见，无法浏览子目录，请将宿主机路径挂载进后端容器。
        </p>
      )}

      <DirectoryPicker
        open={pickerOpen}
        onOpenChange={setPickerOpen}
        rootPath={currentDir ? currentDir.mount_path : "/"}
        initialPath={
          value.startsWith("/") ? value : currentDir?.mount_path
        }
        returnRelative={false}
        onSelect={(path) => onChange(path)}
        useDirectoriesQuery={useSettingsDirectories}
        createDirectory={settingsCreateDirectory}
        renameDirectory={settingsRenameDirectory}
        deleteDirectory={settingsDeleteDirectory}
      />
    </div>
  );
}
