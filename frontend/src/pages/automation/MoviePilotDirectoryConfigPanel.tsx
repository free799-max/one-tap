/**
 * MoviePilot 目录配置
 *
 * 目录使用独立草稿保存。
 */

import { useEffect, useId, useMemo, useRef, useState } from "react";
import {
  AlertTriangle,
  FolderTree,
  Loader2,
  Plus,
  RefreshCw,
  Save,
  Sparkles,
  Trash2,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
  useAppDirectoryConfig,
  useRunAppAutoLibrarySync,
  useSaveAppAutoLibrarySync,
  useSaveAppDirectories,
  type AppAutoLibrarySyncConfig,
  type AppDirectory,
} from "@/hooks/useOrchestrations";
import { cn } from "@/lib/utils";

const TRANSFER_OPTIONS: {
  value: AppDirectory["transfer_type"];
  label: string;
}[] = [
  { value: "link", label: "硬链接" },
  { value: "softlink", label: "软链接" },
  { value: "copy", label: "复制" },
  { value: "move", label: "移动" },
];

const DEFAULT_AUTO_LIBRARY_SYNC: AppAutoLibrarySyncConfig = {
  enabled: true,
  interval_minutes: 24 * 60,
  last_run_at: null,
  last_result: null,
};

function formatRunTime(value: string | null) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    dateStyle: "medium",
    timeStyle: "medium",
  }).format(date);
}

function validateDirectories(directories: AppDirectory[]) {
  const names = new Set<string>();
  for (const [index, directory] of directories.entries()) {
    const label = `目录 ${index + 1}`;
    const name = directory.name.trim();
    const downloadPath = directory.download_path.trim();
    const libraryPath = directory.library_path.trim();
    if (!name) return `${label}的名称不能为空。`;
    if (names.has(name)) return `目录名称“${name}”重复，请使用唯一名称。`;
    names.add(name);
    if (!downloadPath || !libraryPath) {
      return `${label}的下载目录和整理目录不能为空。`;
    }
    if (!downloadPath.startsWith("/") || !libraryPath.startsWith("/")) {
      return `${label}的下载目录和整理目录必须是以 / 开头的绝对路径。`;
    }
  }
  return "";
}

export function MoviePilotDirectoryConfigPanel({
  instanceId,
  appName,
}: {
  instanceId: number;
  appName: string;
}) {
  const idPrefix = useId();
  const { data, isLoading, isError, isFetching, refetch } =
    useAppDirectoryConfig(instanceId, appName);
  const saveDirectoriesMutation = useSaveAppDirectories();
  const saveAutoLibraryMutation = useSaveAppAutoLibrarySync();
  const runAutoLibraryMutation = useRunAppAutoLibrarySync();
  const [draftDirectories, setDraftDirectories] = useState<
    AppDirectory[] | null
  >(null);
  const [draftAutoLibrary, setDraftAutoLibrary] =
    useState<AppAutoLibrarySyncConfig | null>(null);
  const [directoryError, setDirectoryError] = useState("");
  const initialized = useRef(false);

  useEffect(() => {
    initialized.current = false;
    setDraftDirectories(null);
    setDraftAutoLibrary(null);
    setDirectoryError("");
  }, [instanceId, appName]);

  useEffect(() => {
    if (!data || initialized.current) return;
    setDraftDirectories(data.directories.map((directory) => ({ ...directory })));
    setDraftAutoLibrary({
      ...(data.auto_library_sync ?? DEFAULT_AUTO_LIBRARY_SYNC),
    });
    initialized.current = true;
  }, [data]);

  const directories = useMemo(
    () => draftDirectories ?? [],
    [draftDirectories]
  );

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 py-8 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
        正在读取目录配置…
      </div>
    );
  }

  if (isError || !data) {
    return (
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-destructive/30 p-4 text-sm">
        <span className="break-words text-muted-foreground">
          目录配置读取失败，请重试。
        </span>
        <Button size="sm" variant="outline" onClick={() => refetch()}>
          <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
          重新加载
        </Button>
      </div>
    );
  }

  if (!data.supported) {
    return (
      <div className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">
        该应用暂不支持目录配置，目前仅 MoviePilot 可用。
      </div>
    );
  }

  if (!draftDirectories || !draftAutoLibrary) {
    return (
      <div className="flex items-center gap-2 py-8 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
        正在初始化目录配置…
      </div>
    );
  }

  const isBusy =
    saveDirectoriesMutation.isPending ||
    saveAutoLibraryMutation.isPending ||
    runAutoLibraryMutation.isPending;

  const updateDirectory = (index: number, patch: Partial<AppDirectory>) => {
    setDraftDirectories((previous) =>
      previous
        ? previous.map((directory, directoryIndex) =>
            directoryIndex === index ? { ...directory, ...patch } : directory
          )
        : previous
    );
    setDirectoryError("");
  };

  const addDirectory = () => {
    let suffix = 1;
    let name = "新目录";
    while (directories.some((directory) => directory.name.trim() === name)) {
      suffix += 1;
      name = `新目录 ${suffix}`;
    }
    setDraftDirectories((previous) =>
      previous
        ? [
            ...previous,
            {
              ...data.directory_template,
              name,
              download_path: data.detected.download_path,
              library_path: data.detected.library_path,
            },
          ]
        : previous
    );
    setDirectoryError("");
  };

  const removeDirectory = (index: number) => {
    setDraftDirectories((previous) =>
      previous
        ? previous.filter((_, directoryIndex) => directoryIndex !== index)
        : previous
    );
    setDirectoryError("");
  };

  const handleRefresh = async () => {
    const result = await refetch();
    if (!result.data) return;
    setDraftDirectories(
      result.data.directories.map((directory) => ({ ...directory }))
    );
    setDraftAutoLibrary({
      ...(result.data.auto_library_sync ?? DEFAULT_AUTO_LIBRARY_SYNC),
    });
    setDirectoryError("");
    initialized.current = true;
  };

  const handleSaveDirectories = () => {
    const error = validateDirectories(directories);
    if (error) {
      setDirectoryError(error);
      return;
    }
    const payload = directories.map((directory) => ({
      ...directory,
      name: directory.name.trim(),
      download_path: directory.download_path.trim(),
      library_path: directory.library_path.trim(),
    }));
    saveDirectoriesMutation.mutate(
      { instanceId, appName, directories: payload },
      {
        onSuccess: (panel) => {
          setDraftDirectories(
            panel.directories.map((directory) => ({ ...directory }))
          );
          setDirectoryError("");
        },
      }
    );
  };

  const handleSaveAutoLibrary = () => {
    saveAutoLibraryMutation.mutate(
      {
        instanceId,
        appName,
        config: {
          enabled: draftAutoLibrary.enabled,
          interval_minutes: draftAutoLibrary.interval_minutes,
        },
      },
      {
        onSuccess: (panel) =>
          setDraftAutoLibrary({
            ...(panel.auto_library_sync ?? DEFAULT_AUTO_LIBRARY_SYNC),
          }),
      }
    );
  };

  const handleRunAutoLibrary = () => {
    runAutoLibraryMutation.mutate(
      { instanceId, appName },
      {
        onSuccess: (result) =>
          setDraftAutoLibrary({
            ...(result.panel.auto_library_sync ?? DEFAULT_AUTO_LIBRARY_SYNC),
          }),
      }
    );
  };

  const autoLastResult = draftAutoLibrary.last_result;
  const autoServerErrors = (autoLastResult?.servers ?? []).filter(
    (server) => server.error
  );

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 space-y-1">
          <div className="flex items-center gap-2">
            <FolderTree className="h-4 w-4 shrink-0 text-primary" aria-hidden="true" />
            <h3 className="text-sm font-medium">MoviePilot 目录配置</h3>
            <Badge variant="secondary" className="h-5 text-[11px]">
              {directories.length} 个目录
            </Badge>
          </div>
          <p className="break-words text-xs leading-5 text-muted-foreground">
            配置下载目录、整理目录及整理方式。
          </p>
        </div>
        <div className="flex w-full flex-wrap items-center gap-2 sm:w-auto">
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="gap-1.5"
            onClick={handleRefresh}
            disabled={isFetching || isBusy}
          >
            <RefreshCw
              className={cn("h-3.5 w-3.5", isFetching && "animate-spin")}
              aria-hidden="true"
            />
            刷新
          </Button>
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="gap-1.5"
            onClick={addDirectory}
            disabled={isBusy}
          >
            <Plus className="h-3.5 w-3.5" aria-hidden="true" />
            添加目录
          </Button>
          <Button
            type="button"
            size="sm"
            className="gap-1.5"
            onClick={handleSaveDirectories}
            disabled={isBusy}
          >
            {saveDirectoriesMutation.isPending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
            ) : (
              <Save className="h-3.5 w-3.5" aria-hidden="true" />
            )}
            保存目录
          </Button>
        </div>
      </div>

      {directoryError && (
        <p
          role="alert"
          className="flex items-start gap-1.5 rounded-lg border border-destructive/30 bg-destructive/5 p-3 text-xs text-destructive"
        >
          <AlertTriangle className="mt-px h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          {directoryError}
        </p>
      )}

      {directories.length > 0 ? (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {directories.map((directory, index) => (
            <section
              key={index}
              className="min-w-0 space-y-3 rounded-lg border bg-card p-3"
            >
              <div className="flex items-center justify-between gap-2">
                <div className="flex min-w-0 items-center gap-2">
                  <FolderTree
                    className="h-4 w-4 shrink-0 text-muted-foreground"
                    aria-hidden="true"
                  />
                  <h4 className="truncate text-sm font-medium">
                    {directory.name || `目录 ${index + 1}`}
                  </h4>
                </div>
                <Button
                  type="button"
                  size="icon"
                  variant="ghost"
                  className="h-8 w-8 shrink-0 text-muted-foreground hover:text-destructive"
                  onClick={() => removeDirectory(index)}
                  disabled={isBusy}
                  aria-label={`删除目录 ${directory.name || index + 1}`}
                  title="删除目录"
                >
                  <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                </Button>
              </div>

              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
                <div className="space-y-1.5">
                  <Label htmlFor={`${idPrefix}-directory-name-${index}`}>
                    名称
                  </Label>
                  <Input
                    id={`${idPrefix}-directory-name-${index}`}
                    value={directory.name}
                    onChange={(event) =>
                      updateDirectory(index, { name: event.target.value })
                    }
                    placeholder="例如：电影"
                    disabled={isBusy}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor={`${idPrefix}-transfer-type-${index}`}>
                    整理方式
                  </Label>
                  <select
                    id={`${idPrefix}-transfer-type-${index}`}
                    className="h-9 w-full rounded-md border border-input bg-background px-2 text-xs disabled:cursor-not-allowed disabled:opacity-50"
                    value={directory.transfer_type}
                    onChange={(event) =>
                      updateDirectory(index, {
                        transfer_type: event.target
                          .value as AppDirectory["transfer_type"],
                      })
                    }
                    disabled={isBusy}
                  >
                    {TRANSFER_OPTIONS.map((option) => (
                      <option key={option.value} value={option.value}>
                        {option.label}
                      </option>
                    ))}
                  </select>
                </div>
              </div>

              <div className="space-y-1.5">
                <Label htmlFor={`${idPrefix}-download-path-${index}`}>
                  下载目录
                </Label>
                <Input
                  id={`${idPrefix}-download-path-${index}`}
                  className="font-mono text-xs"
                  value={directory.download_path}
                  onChange={(event) =>
                    updateDirectory(index, {
                      download_path: event.target.value,
                    })
                  }
                  placeholder="/downloads/movies"
                  disabled={isBusy}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor={`${idPrefix}-library-path-${index}`}>
                  整理目录
                </Label>
                <Input
                  id={`${idPrefix}-library-path-${index}`}
                  className="font-mono text-xs"
                  value={directory.library_path}
                  onChange={(event) =>
                    updateDirectory(index, {
                      library_path: event.target.value,
                    })
                  }
                  placeholder="/media/movies"
                  disabled={isBusy}
                />
              </div>
            </section>
          ))}
        </div>
      ) : (
        <div className="rounded-lg border border-dashed p-6 text-center text-xs text-muted-foreground">
          暂无目录，点击“添加目录”创建。
        </div>
      )}

      <section className="space-y-3 rounded-lg border bg-card p-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0 space-y-1">
            <div className="flex items-center gap-2">
              <Sparkles
                className="h-4 w-4 text-primary"
                aria-hidden="true"
              />
              <h4 className="text-sm font-medium">自动创建媒体库</h4>
              <Badge
                variant={draftAutoLibrary.enabled ? "default" : "secondary"}
                className="h-5 text-[10px] font-normal"
              >
                {draftAutoLibrary.enabled ? "已启用" : "未启用"}
              </Badge>
            </div>
            <p className="break-words text-[11px] leading-4 text-muted-foreground">
              MoviePilot 实际整理出分类目录后，自动为同组 Jellyfin / Emby
              创建媒体库。不会预建空库，也不会删除或重命名已有媒体库。
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="gap-1.5"
              onClick={handleRunAutoLibrary}
              disabled={isBusy}
            >
              {runAutoLibraryMutation.isPending ? (
                <Loader2
                  className="h-3.5 w-3.5 animate-spin"
                  aria-hidden="true"
                />
              ) : (
                <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
              )}
              立即检测
            </Button>
            <Button
              type="button"
              size="sm"
              className="gap-1.5"
              onClick={handleSaveAutoLibrary}
              disabled={isBusy}
            >
              {saveAutoLibraryMutation.isPending ? (
                <Loader2
                  className="h-3.5 w-3.5 animate-spin"
                  aria-hidden="true"
                />
              ) : (
                <Save className="h-3.5 w-3.5" aria-hidden="true" />
              )}
              保存配置
            </Button>
          </div>
        </div>

        <div className="grid gap-3 lg:grid-cols-[minmax(0,18rem)_minmax(0,1fr)] lg:items-stretch">
          <div className="space-y-3 rounded-md border bg-muted/20 p-3">
            <div className="flex items-center justify-between gap-3">
              <Label
                htmlFor={`${idPrefix}-auto-library-enabled`}
                className="text-xs font-normal"
              >
                定时自动检测
              </Label>
              <Switch
                id={`${idPrefix}-auto-library-enabled`}
                checked={draftAutoLibrary.enabled}
                onCheckedChange={(checked) =>
                  setDraftAutoLibrary((previous) =>
                    previous ? { ...previous, enabled: checked } : previous
                  )
                }
                disabled={isBusy}
              />
            </div>
            <div className="space-y-1.5">
              <Label
                htmlFor={`${idPrefix}-auto-library-interval`}
                className="text-xs"
              >
                检测间隔（分钟）
              </Label>
              <Input
                id={`${idPrefix}-auto-library-interval`}
                type="number"
                min={1}
                max={1440}
                className="h-8"
                value={draftAutoLibrary.interval_minutes}
                onChange={(event) =>
                  setDraftAutoLibrary((previous) =>
                    previous
                      ? {
                          ...previous,
                          interval_minutes: Number(event.target.value) || 1,
                        }
                      : previous
                  )
                }
                disabled={isBusy || !draftAutoLibrary.enabled}
              />
              <p className="text-[11px] leading-4 text-muted-foreground">
                可填 1–1440 分钟。关闭自动检测后，仍可用“立即检测”手动执行。
              </p>
            </div>
          </div>
          <div className="flex min-w-0 flex-col gap-2 rounded-md border bg-muted/20 p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="text-xs font-medium">最近一次检测</span>
              <span className="text-[11px] text-muted-foreground">
                {formatRunTime(draftAutoLibrary.last_run_at) || "尚未执行"}
              </span>
            </div>
            {autoLastResult ? (
              <>
                <p className="break-words text-[11px] leading-4 text-muted-foreground">
                  {autoLastResult.message}
                </p>
                <div className="flex flex-wrap gap-2">
                  <Badge variant="outline" className="font-normal">
                    分类目录 {autoLastResult.targets}
                  </Badge>
                  <Badge variant="outline" className="font-normal">
                    已创建目录 {autoLastResult.present_directories}
                  </Badge>
                  <Badge
                    variant={
                      autoLastResult.created > 0 ? "default" : "secondary"
                    }
                    className="font-normal"
                  >
                    新建媒体库 {autoLastResult.created}
                  </Badge>
                </div>
                {autoServerErrors.length > 0 && (
                  <div className="space-y-1 border-t pt-2">
                    {autoServerErrors.map((server) => (
                      <p
                        key={server.name}
                        className="break-words text-[11px] leading-4 text-destructive"
                      >
                        {server.name}：{server.error}
                      </p>
                    ))}
                  </div>
                )}
              </>
            ) : (
              <p className="text-[11px] leading-4 text-muted-foreground">
                {draftAutoLibrary.enabled
                  ? "尚未检测。保存配置后，后台会按间隔自动检查分类目录。"
                  : "自动检测已关闭，仍可使用“立即检测”手动执行一轮。"}
              </p>
            )}
          </div>
        </div>
      </section>
    </div>
  );
}
