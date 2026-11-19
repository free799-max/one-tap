/**
 * 目录选择器弹窗
 *
 * 允许用户从指定根目录开始浏览文件系统，选择目录。
 * 支持相对路径展示/返回，以及创建、重命名、删除目录。
 */

import { useEffect, useMemo, useState } from "react";
import type { UseQueryResult } from "@tanstack/react-query";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Folder,
  ChevronRight,
  Home,
  Loader2,
  Plus,
  Edit3,
  Trash2,
  Search,
} from "lucide-react";
import { toAbsolutePath, toRelativePath } from "@/lib/utils";
import type { ApiError } from "@/api/types";

/** 目录列表查询 hook 类型 */
type UseDirectoriesHook = (
  path: string,
  enabled: boolean
) => UseQueryResult<DirectoryList, Error>;

/** 目录操作 mutation 的简化类型（兼容不同请求体的 useMutation） */
/* eslint-disable @typescript-eslint/no-explicit-any */
interface DirectoryMutationLike {
  mutate: (...args: any[]) => any;
  isPending: boolean;
}
/* eslint-enable @typescript-eslint/no-explicit-any */

interface DirectoryEntry {
  name: string;
  path: string;
  is_directory: boolean;
}

interface DirectoryList {
  path: string;
  entries: DirectoryEntry[];
  exists?: boolean;
}

interface DirectoryPickerProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 浏览根目录（绝对路径） */
  rootPath: string;
  /** 面包屑首项的显示名称 */
  rootLabel?: string;
  /** 初始路径，可为绝对路径或相对 rootPath 的路径 */
  initialPath?: string;
  /** 选择后返回相对路径，否则返回绝对路径 */
  returnRelative?: boolean;
  /** 是否允许选择根目录本身（默认 true） */
  selectableRoot?: boolean;
  /**
   * 输入框用途：
   * - path（默认）：输入相对/绝对路径后回车跳转；
   * - search：只作为当前目录的搜索过滤框，不跳转、不预填路径。
   */
  inputMode?: "path" | "search";
  onSelect: (path: string) => void;
  /** 自定义目录查询 hook */
  useDirectoriesQuery?: UseDirectoriesHook;
  /** 创建目录 mutation 结果 */
  createDirectory?: DirectoryMutationLike;
  /** 重命名目录 mutation 结果 */
  renameDirectory?: DirectoryMutationLike;
  /** 删除目录 mutation 结果 */
  deleteDirectory?: DirectoryMutationLike;
}

export function DirectoryPicker({
  open,
  onOpenChange,
  rootPath,
  rootLabel = "存储根目录",
  initialPath,
  returnRelative = false,
  selectableRoot = true,
  inputMode = "path",
  onSelect,
  useDirectoriesQuery,
  createDirectory,
  renameDirectory,
  deleteDirectory,
}: DirectoryPickerProps) {
  const normalizedRoot = rootPath.replace(/\/+$/, "") || "/";
  const isSearchMode = inputMode === "search";

  const [currentAbsolutePath, setCurrentAbsolutePath] = useState(normalizedRoot);
  const [inputValue, setInputValue] = useState("");
  const [selectedEntry, setSelectedEntry] = useState<DirectoryEntry | null>(null);

  const [isCreating, setIsCreating] = useState(false);
  const [createName, setCreateName] = useState("");

  const [isRenaming, setIsRenaming] = useState(false);
  const [renameName, setRenameName] = useState("");

  const [isDeleteConfirming, setIsDeleteConfirming] = useState(false);

  const createMutation = createDirectory;
  const renameMutation = renameDirectory;
  const deleteMutation = deleteDirectory;

  const { data, isLoading, error, refetch } = useDirectoriesQuery?.(
    currentAbsolutePath,
    open
  ) ?? { data: undefined, isLoading: false, error: null, refetch: () => {} };

  // 打开时根据 initialPath 定位；相对路径按 rootPath 解析
  useEffect(() => {
    if (!open) return;
    const abs = initialPath
      ? toAbsolutePath(initialPath, normalizedRoot)
      : normalizedRoot;
    setCurrentAbsolutePath(abs);
    setInputValue(isSearchMode ? "" : toRelativePath(abs, normalizedRoot));
    setSelectedEntry(null);
    setIsCreating(false);
    setIsRenaming(false);
    setIsDeleteConfirming(false);
  }, [open, initialPath, normalizedRoot, isSearchMode]);

  // 当前路径变化时：路径模式同步输入框，搜索模式清空过滤词
  useEffect(() => {
    setInputValue(
      isSearchMode ? "" : toRelativePath(currentAbsolutePath, normalizedRoot)
    );
    setSelectedEntry(null);
  }, [currentAbsolutePath, normalizedRoot, isSearchMode]);

  const breadcrumbs = useMemo(() => {
    const relative = toRelativePath(currentAbsolutePath, normalizedRoot);
    const parts = relative.split("/").filter(Boolean);
    const crumbs: { name: string; path: string }[] = [
      { name: rootLabel, path: normalizedRoot },
    ];
    let acc = normalizedRoot;
    for (const part of parts) {
      acc = acc === "/" ? `/${part}` : `${acc}/${part}`;
      crumbs.push({ name: part, path: acc });
    }
    return crumbs;
  }, [currentAbsolutePath, normalizedRoot, rootLabel]);

  const navigateTo = (absPath: string) => {
    setCurrentAbsolutePath(absPath);
  };

  // 上级导航不能越过浏览根目录（设置端点只允许根目录及其子目录）
  const navigateUp = () => {
    const parent =
      currentAbsolutePath.replace(/\/[^/]*\/?$/, "") || normalizedRoot;
    const rootPrefix = `${normalizedRoot.replace(/\/+$/, "")}/`;
    if (
      parent === normalizedRoot ||
      (normalizedRoot !== "/" && parent.startsWith(rootPrefix))
    ) {
      navigateTo(parent);
    } else {
      navigateTo(normalizedRoot);
    }
  };

  const handleInputSubmit = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (isSearchMode || e.key !== "Enter") return;
    const trimmed = inputValue.trim();
    if (!trimmed) {
      navigateTo(normalizedRoot);
      return;
    }
    const target = toAbsolutePath(trimmed, normalizedRoot);
    // 输入框允许用户跳到 rootPath 下的任意路径；rootPath 为 "/" 时所有绝对路径均合法
    const rootPrefix = normalizedRoot === "/" ? "/" : `${normalizedRoot}/`;
    if (target !== normalizedRoot && !target.startsWith(rootPrefix)) {
      return;
    }
    navigateTo(target);
  };

  const visibleEntries = useMemo(() => {
    const entries = data?.entries ?? [];
    if (!isSearchMode) return entries;
    const keyword = inputValue.trim().toLowerCase();
    if (!keyword) return entries;
    return entries.filter((entry) =>
      entry.name.toLowerCase().includes(keyword)
    );
  }, [data, isSearchMode, inputValue]);

  const handleSelect = () => {
    const result = returnRelative
      ? toRelativePath(currentAbsolutePath, normalizedRoot)
      : currentAbsolutePath;
    onSelect(result);
    onOpenChange(false);
  };

  const handleCreate = () => {
    if (!createName.trim() || !createMutation) return;
    createMutation.mutate(
      { path: currentAbsolutePath, name: createName.trim() },
      {
        onSuccess: () => {
          const newPath = `${currentAbsolutePath.replace(/\/+$/, "")}/${createName.trim()}`;
          setCreateName("");
          setIsCreating(false);
          navigateTo(newPath);
          refetch();
        },
      }
    );
  };

  const handleRename = () => {
    if (!renameName.trim() || !selectedEntry || !renameMutation) return;
    renameMutation.mutate(
      { old_path: selectedEntry.path, new_name: renameName.trim() },
      {
        onSuccess: () => {
          setRenameName("");
          setIsRenaming(false);
          setSelectedEntry(null);
          refetch();
        },
      }
    );
  };

  const handleDelete = () => {
    if (!selectedEntry || !deleteMutation) return;
    setIsDeleteConfirming(true);
  };

  const confirmDelete = () => {
    if (!selectedEntry || !deleteMutation) return;
    deleteMutation.mutate(
      { path: selectedEntry.path },
      {
        onSuccess: () => {
          if (selectedEntry.path === currentAbsolutePath) {
            const parent = currentAbsolutePath.replace(/\/[^/]*\/?$/, "") || normalizedRoot;
            navigateTo(parent);
          }
          setSelectedEntry(null);
          setIsDeleteConfirming(false);
          refetch();
        },
      }
    );
  };

  const canOperate = Boolean(
    createDirectory || renameDirectory || deleteDirectory
  );

  return (
    <>
      <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg max-h-[80vh] flex flex-col gap-0 p-0 overflow-hidden">
        <DialogHeader className="px-4 pt-4 pb-2 flex-shrink-0">
          <DialogTitle>选择目录</DialogTitle>
        </DialogHeader>

        <div className="px-4 pb-2 flex-shrink-0 space-y-3">
          {/* 工具栏 */}
          {canOperate && (
            <div className="flex items-center gap-2">
              {createDirectory && (
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  className="h-7 gap-1 text-xs"
                  onClick={() => setIsCreating(true)}
                >
                  <Plus className="h-3.5 w-3.5" />
                  新建
                </Button>
              )}
              {renameDirectory && (
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  className="h-7 gap-1 text-xs"
                  disabled={!selectedEntry}
                  onClick={() => {
                    setRenameName(selectedEntry?.name || "");
                    setIsRenaming(true);
                  }}
                >
                  <Edit3 className="h-3.5 w-3.5" />
                  重命名
                </Button>
              )}
              {deleteDirectory && !isDeleteConfirming && (
                <Button
                  type="button"
                  variant="secondary"
                  size="sm"
                  className="h-7 gap-1 text-xs"
                  disabled={!selectedEntry}
                  onClick={handleDelete}
                >
                  <Trash2 className="h-3.5 w-3.5" />
                  删除
                </Button>
              )}
              {deleteDirectory && isDeleteConfirming && (
                <>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="h-7 text-xs"
                    onClick={() => setIsDeleteConfirming(false)}
                  >
                    取消
                  </Button>
                  <Button
                    type="button"
                    variant="destructive"
                    size="sm"
                    className="h-7 text-xs"
                    onClick={confirmDelete}
                    disabled={deleteMutation?.isPending}
                  >
                    {deleteMutation?.isPending ? "删除中..." : "确认删除"}
                  </Button>
                </>
              )}
            </div>
          )}

          {/* 新建目录输入 */}
          {isCreating && (
            <div className="flex items-center gap-2">
              <Input
                value={createName}
                onChange={(e) => setCreateName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleCreate();
                  if (e.key === "Escape") {
                    setIsCreating(false);
                    setCreateName("");
                  }
                }}
                placeholder="新目录名称"
                className="h-8 text-sm"
                autoFocus
              />
              <Button
                type="button"
                size="sm"
                className="h-8"
                onClick={handleCreate}
                disabled={createMutation?.isPending}
              >
                创建
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="h-8"
                onClick={() => {
                  setIsCreating(false);
                  setCreateName("");
                }}
              >
                取消
              </Button>
            </div>
          )}

          {/* 重命名输入 */}
          {isRenaming && selectedEntry && (
            <div className="flex items-center gap-2">
              <Input
                value={renameName}
                onChange={(e) => setRenameName(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter") handleRename();
                  if (e.key === "Escape") {
                    setIsRenaming(false);
                    setRenameName("");
                  }
                }}
                placeholder="新名称"
                className="h-8 text-sm"
                autoFocus
              />
              <Button
                type="button"
                size="sm"
                className="h-8"
                onClick={handleRename}
                disabled={renameMutation?.isPending}
              >
                确认
              </Button>
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="h-8"
                onClick={() => {
                  setIsRenaming(false);
                  setRenameName("");
                }}
              >
                取消
              </Button>
            </div>
          )}

          {/* 面包屑 */}
          <div className="flex items-center gap-2 text-xs text-muted-foreground flex-wrap">
            {breadcrumbs.map((crumb, index) => (
              <button
                key={crumb.path}
                type="button"
                onClick={() => navigateTo(crumb.path)}
                className="hover:text-primary hover:underline flex items-center gap-0.5"
              >
                {index === 0 ? <Home className="h-3 w-3" /> : crumb.name}
                {index < breadcrumbs.length - 1 && (
                  <ChevronRight className="h-3 w-3" />
                )}
              </button>
            ))}
          </div>

          {/* 路径跳转 / 本目录搜索 */}
          <div className="relative">
            <Search className="pointer-events-none absolute left-2 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              onKeyDown={handleInputSubmit}
              placeholder={
                isSearchMode
                  ? "搜索当前目录"
                  : "输入相对路径或绝对路径后回车跳转"
              }
              className="h-8 pl-7 text-sm"
            />
          </div>
        </div>

        {/* 目录列表 */}
        <div className="h-[280px] overflow-y-auto px-4 py-1">
          {isLoading && (
            <div className="flex items-center justify-center py-8 text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin mr-2" />
              加载中…
            </div>
          )}

          {error && !isLoading && (
            <div className="py-6 text-center text-sm text-destructive">
              无法读取该路径：
              {(error as ApiError)?.displayMessage ||
                (error as Error)?.message ||
                "未知错误"}
            </div>
          )}

          {!isLoading && !error && data && (
            <div className="space-y-0.5">
              {currentAbsolutePath !== normalizedRoot && (
                <button
                  type="button"
                  onClick={navigateUp}
                  className="w-full flex items-center gap-2 rounded-md px-2 py-1.5 text-sm text-left hover:bg-muted"
                >
                  <Folder className="h-4 w-4 text-muted-foreground" />
                  ..
                </button>
              )}

              {data.exists === false ? (
                <div className="py-6 text-center text-xs text-muted-foreground">
                  该目录不存在
                </div>
              ) : visibleEntries.length === 0 ? (
                <div className="py-6 text-center text-xs text-muted-foreground">
                  {isSearchMode && inputValue.trim()
                    ? "没有匹配的目录"
                    : "该目录下没有子目录"}
                </div>
              ) : (
                visibleEntries.map((entry) => {
                  const isSelected = selectedEntry?.path === entry.path;
                  return (
                    <div
                      key={entry.path}
                      className={`flex items-center gap-2 rounded-md px-2 py-1.5 text-sm ${
                        isSelected ? "bg-primary/10" : "hover:bg-muted"
                      }`}
                    >
                      <button
                        type="button"
                        onClick={() => setSelectedEntry(entry)}
                        onDoubleClick={() => navigateTo(entry.path)}
                        className="flex-1 flex items-center gap-2 text-left"
                      >
                        <Folder
                          className={`h-4 w-4 ${
                            isSelected ? "text-primary" : "text-primary/70"
                          }`}
                        />
                        {entry.name}
                      </button>
                    </div>
                  );
                })
              )}
            </div>
          )}
        </div>

        <div className="flex justify-end gap-2 px-4 py-3 border-t flex-shrink-0">
          <Button variant="outline" size="sm" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button
            size="sm"
            onClick={handleSelect}
            disabled={!selectableRoot && currentAbsolutePath === normalizedRoot}
          >
            选择当前目录
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  </>
  );
}
