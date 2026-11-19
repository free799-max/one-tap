/**
 * Reader / Suwayomi 源配置面板
 *
 * 配置由独立接口读写，重新装配复用实例级 wire 后台任务。
 */

import { useEffect, useId, useRef, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  LibraryBig,
  Loader2,
  Plus,
  RefreshCw,
  Save,
  Trash2,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
  useAppResourceSources,
  useSaveAppResourceSources,
  useWireOrchestrationInstance,
  type ResourceSourceOption,
  type ResourceSourcesConfig,
  type ResourceSourcesLastResult,
} from "@/hooks/useOrchestrations";
import { cn } from "@/lib/utils";

function formatTime(value?: string) {
  if (!value) return "未记录";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN");
}

function getStatusMeta(status: string) {
  switch (status.toLowerCase()) {
    case "ok":
    case "success":
      return {
        label: "成功",
        className: "border-emerald-500/30 text-emerald-600 dark:text-emerald-400",
      };
    case "partial":
      return {
        label: "部分失败",
        className: "border-amber-500/30 text-amber-600 dark:text-amber-400",
      };
    case "error":
    case "failed":
      return { label: "失败", className: "border-destructive/30 text-destructive" };
    case "running":
      return { label: "执行中", className: "border-primary/30 text-primary" };
    default:
      return { label: status || "未知", className: "text-muted-foreground" };
  }
}

function LastResultSummary({ last }: { last?: ResourceSourcesLastResult | null }) {
  if (!last) {
    return (
      <div className="rounded-lg border border-dashed p-3 text-xs text-muted-foreground">
        暂无重新装配结果，保存并重新装配后可在这里查看执行情况。
      </div>
    );
  }

  const status = getStatusMeta(last.status);
  const failedItems = (last.items ?? []).filter(
    (item) => !["ok", "success"].includes(item.status.toLowerCase())
  );

  return (
    <section className="space-y-3 rounded-lg border bg-muted/20 p-3">
      <div className="flex flex-wrap items-center gap-2">
        <Clock className="h-4 w-4 shrink-0 text-muted-foreground" aria-hidden="true" />
        <h4 className="text-sm font-medium">上次装配结果</h4>
        <Badge variant="outline" className={cn("text-[11px]", status.className)}>
          {status.label}
        </Badge>
      </div>
      {last.message && (
        <p className="break-words text-xs leading-5 text-muted-foreground">
          {last.message}
        </p>
      )}
      <div className="flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-muted-foreground">
        <span className="break-words">开始：{formatTime(last.started_at)}</span>
        <span className="break-words">结束：{formatTime(last.finished_at)}</span>
      </div>
      {failedItems.length > 0 && (
        <div className="space-y-2 border-t border-border/60 pt-3">
          <div className="flex items-center gap-1.5 text-xs font-medium text-destructive">
            <AlertTriangle className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
            逐项错误（{failedItems.length}）
          </div>
          <ul className="space-y-2">
            {failedItems.map((item) => (
              <li
                key={item.id}
                className="min-w-0 rounded-md border border-destructive/20 bg-background/60 p-2.5"
              >
                <div className="flex flex-wrap items-baseline gap-x-2 gap-y-1">
                  <span className="break-words text-xs font-medium">{item.name}</span>
                  <span className="break-all font-mono text-[10px] text-muted-foreground">
                    {item.id}
                  </span>
                  <Badge variant="destructive" className="h-4 text-[10px]">
                    {item.status}
                  </Badge>
                </div>
                {item.message && (
                  <p className="mt-1 break-words text-xs leading-5 text-muted-foreground">
                    {item.message}
                  </p>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
      {last.items && last.items.length > 0 && failedItems.length === 0 && (
        <p className="flex items-center gap-1.5 text-xs text-emerald-600 dark:text-emerald-400">
          <CheckCircle2 className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
          {last.items.length} 个源地址均装配成功
        </p>
      )}
    </section>
  );
}

export function ResourceSourcesPanel({
  instanceId,
  appName,
}: {
  instanceId: number;
  appName: string;
}) {
  const idPrefix = useId();
  const isReader = appName === "reader";
  const sourceLabel = isReader ? "书源" : "漫源";
  const { data, isLoading, isError, isFetching, refetch } =
    useAppResourceSources(instanceId, appName);
  const saveMutation = useSaveAppResourceSources();
  const wireMutation = useWireOrchestrationInstance();
  const [draft, setDraft] = useState<ResourceSourcesConfig | null>(null);
  const [isAddingSource, setIsAddingSource] = useState(false);
  const [newSourceName, setNewSourceName] = useState("");
  const [newSourceUrl, setNewSourceUrl] = useState("");
  const [newSourceError, setNewSourceError] = useState("");
  const initialized = useRef(false);

  useEffect(() => {
    initialized.current = false;
    setDraft(null);
    setIsAddingSource(false);
    setNewSourceName("");
    setNewSourceUrl("");
    setNewSourceError("");
  }, [instanceId, appName]);

  useEffect(() => {
    if (!data?.config || initialized.current) return;
    setDraft(data.config);
    initialized.current = true;
  }, [data?.config]);

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 py-8 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
        正在读取{sourceLabel}配置…
      </div>
    );
  }

  if (isError || !data) {
    return (
      <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg border border-destructive/30 p-4 text-sm">
        <span className="break-words text-muted-foreground">{sourceLabel}配置读取失败，请重试。</span>
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
        该应用暂不支持{sourceLabel}配置，目前仅 Reader 和 Suwayomi 可用。
      </div>
    );
  }

  if (!draft) {
    return (
      <div className="flex items-center gap-2 py-8 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
        正在初始化{sourceLabel}配置…
      </div>
    );
  }

  const update = (patch: Partial<ResourceSourcesConfig>) =>
    setDraft((previous) => (previous ? { ...previous, ...patch } : previous));
  const isBusy = saveMutation.isPending || wireMutation.isPending;
  const appLabel = isReader ? "Reader" : "Suwayomi";
  const options: ResourceSourceOption[] = [
    ...data.options.filter((option) => !option.custom),
    ...(draft.custom_sources ?? []).map((source) => ({
      id: source.id,
      name: source.name,
      description: source.url,
      default_selected: false,
      filtered: false,
      custom: true,
    })),
  ];

  const handleSave = () => {
    saveMutation.mutate(
      { instanceId, appName, config: draft },
      { onSuccess: (panel) => setDraft(panel.config) }
    );
  };

  const handleSaveAndWire = () => {
    saveMutation.mutate(
      { instanceId, appName, config: draft },
      {
        onSuccess: (panel) => {
          setDraft(panel.config);
          wireMutation.mutate(instanceId);
        },
      }
    );
  };

  const toggleOption = (id: string) => {
    const option = options.find((item) => item.id === id);
    if (!option || option.filtered) return;
    update({
      selected_ids: draft.selected_ids.includes(id)
        ? draft.selected_ids.filter((selectedId) => selectedId !== id)
        : [...draft.selected_ids, id],
    });
  };

  const handleAddSource = () => {
    const name = newSourceName.trim();
    const url = newSourceUrl.trim();
    if (!name) {
      setNewSourceError(`请填写${sourceLabel}名称。`);
      return;
    }
    try {
      const parsed = new URL(url);
      if (!["http:", "https:"].includes(parsed.protocol)) throw new Error();
    } catch {
      setNewSourceError(`请输入有效的 HTTP/HTTPS ${sourceLabel}地址。`);
      return;
    }
    if (options.some((option) => option.description === url)) {
      setNewSourceError(`这个${sourceLabel}地址已经存在。`);
      return;
    }
    if ((draft.custom_sources ?? []).length >= 50) {
      setNewSourceError(`最多只能添加 50 个自定义${sourceLabel}。`);
      return;
    }
    const id = `custom-${Date.now().toString(36)}-${Math.random()
      .toString(36)
      .slice(2, 10)}`;
    update({
      custom_sources: [...(draft.custom_sources ?? []), { id, name, url }],
      selected_ids: [...draft.selected_ids, id],
    });
    setNewSourceName("");
    setNewSourceUrl("");
    setNewSourceError("");
    setIsAddingSource(false);
  };

  const removeCustomSource = (id: string) => {
    update({
      custom_sources: (draft.custom_sources ?? []).filter(
        (source) => source.id !== id
      ),
      selected_ids: draft.selected_ids.filter((selectedId) => selectedId !== id),
    });
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0 space-y-1">
          <div className="flex flex-wrap items-center gap-2">
            <LibraryBig className="h-4 w-4 shrink-0 text-primary" aria-hidden="true" />
            <h3 className="text-sm font-medium">{appLabel} {sourceLabel}</h3>
            <Badge
              variant={draft.enabled ? "default" : "secondary"}
              className="h-5 text-[11px]"
            >
              {draft.enabled ? "已启用" : "未启用"}
            </Badge>
          </div>
          <p className="break-words text-xs leading-5 text-muted-foreground">
            选择需要装配的{sourceLabel}；保存并重新装配后，后端会应用最新选择。
          </p>
        </div>
        <div className="flex w-full flex-wrap items-center gap-2 sm:w-auto">
          <Button
            type="button"
            size="sm"
            variant="outline"
            className="gap-1.5"
            onClick={() => refetch()}
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
            onClick={handleSave}
            disabled={isBusy}
          >
            {saveMutation.isPending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
            ) : (
              <Save className="h-3.5 w-3.5" aria-hidden="true" />
            )}
            保存配置
          </Button>
          <Button
            type="button"
            size="sm"
            className="gap-1.5"
            onClick={handleSaveAndWire}
            disabled={isBusy}
          >
            {isBusy ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
            ) : (
              <RefreshCw className="h-3.5 w-3.5" aria-hidden="true" />
            )}
            保存并重新装配
          </Button>
        </div>
      </div>

      {isReader && (
        <div className="flex flex-col gap-3 rounded-lg border p-3 sm:flex-row sm:items-end sm:justify-between">
          <div className="min-w-0 space-y-1">
            <Label htmlFor={`${idPrefix}-book-source-limit`} className="text-sm font-medium">
              书源上限
            </Label>
            <p className="break-words text-[11px] leading-4 text-muted-foreground">
              重新装配时通过 Reader 管理接口修改当前用户的数量限制。
            </p>
          </div>
          <Input
            id={`${idPrefix}-book-source-limit`}
            type="number"
            min={1}
            max={100000}
            className="w-full sm:w-40"
            value={draft.book_source_limit ?? 200}
            onChange={(event) =>
              update({ book_source_limit: Number(event.target.value) || 200 })
            }
            disabled={isBusy}
          />
        </div>
      )}

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        <div className="flex items-start justify-between gap-4 rounded-lg border p-3">
          <div className="min-w-0 space-y-1">
            <Label htmlFor={`${idPrefix}-enabled`} className="text-sm font-medium">
              自动装源
            </Label>
            <p className="break-words text-[11px] leading-4 text-muted-foreground">
              重新装配时自动安装并更新已选择的{sourceLabel}。
            </p>
          </div>
          <Switch
            id={`${idPrefix}-enabled`}
            checked={draft.enabled}
            onCheckedChange={(enabled) => update({ enabled })}
            disabled={isBusy}
            aria-label="自动装源"
          />
        </div>
        <div className="flex items-start justify-between gap-4 rounded-lg border p-3">
          <div className="min-w-0 space-y-1">
            <Label htmlFor={`${idPrefix}-auto-filter`} className="text-sm font-medium">
              简单自动筛选
            </Label>
            <p className="break-words text-[11px] leading-4 text-muted-foreground">
              {isReader
                ? "装配时过滤无效、禁用或标记为暂不可用的书源。"
                : "装配时保留中文，并优先使用可用图源。保留成人扩展"}
            </p>
          </div>
          <Switch
            id={`${idPrefix}-auto-filter`}
            checked={draft.auto_filter}
            onCheckedChange={(auto_filter) => update({ auto_filter })}
            disabled={isBusy}
            aria-label="简单自动筛选"
          />
        </div>
      </div>

      <section className="space-y-3 rounded-lg border p-3">
        <div className="flex flex-wrap items-center justify-between gap-2">
          <div>
            <h4 className="text-sm font-medium">{sourceLabel}选项</h4>
            <p className="mt-0.5 text-[11px] text-muted-foreground">
              已选择 {draft.selected_ids.length} / {options.length} 项
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            {draft.auto_filter && (
              <Badge variant="secondary" className="text-[11px]">
                自动筛选已开启
              </Badge>
            )}
            <Button
              type="button"
              size="sm"
              variant="outline"
              className="gap-1.5"
              onClick={() => {
                setNewSourceError("");
                setIsAddingSource(true);
              }}
              disabled={isBusy || isAddingSource}
            >
              <Plus className="h-3.5 w-3.5" aria-hidden="true" />
              添加{sourceLabel}
            </Button>
          </div>
        </div>

        {isAddingSource && (
          <form
            noValidate
            className="space-y-3 rounded-lg border border-primary/30 bg-primary/5 p-3"
            onSubmit={(event) => {
              event.preventDefault();
              handleAddSource();
            }}
          >
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
              <div className="space-y-1.5">
                <Label htmlFor={`${idPrefix}-custom-source-name`}>
                  {sourceLabel}名称
                </Label>
                <Input
                  id={`${idPrefix}-custom-source-name`}
                  value={newSourceName}
                  onChange={(event) => {
                    setNewSourceName(event.target.value);
                    setNewSourceError("");
                  }}
                  placeholder={
                    isReader ? "例如：我的精选书源" : "例如：我的精选漫源"
                  }
                  maxLength={100}
                  autoComplete="off"
                  disabled={isBusy}
                  autoFocus
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor={`${idPrefix}-custom-source-url`}>
                  {sourceLabel}地址
                </Label>
                <Input
                  id={`${idPrefix}-custom-source-url`}
                  type="url"
                  value={newSourceUrl}
                  onChange={(event) => {
                    setNewSourceUrl(event.target.value);
                    setNewSourceError("");
                  }}
                  placeholder="https://example.com/sources.json"
                  maxLength={2048}
                  autoComplete="off"
                  disabled={isBusy}
                />
              </div>
            </div>
            {newSourceError && (
              <p role="alert" className="text-xs text-destructive">
                {newSourceError}
              </p>
            )}
            <div className="flex flex-wrap items-center justify-between gap-2">
              <p className="min-w-0 text-[11px] text-muted-foreground">
                添加后默认勾选，保存并重新装配后导入 {appLabel}。
              </p>
              <div className="ml-auto flex flex-wrap items-center gap-2">
                <Button type="submit" size="sm" disabled={isBusy}>
                  确认
                </Button>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={isBusy}
                  onClick={() => {
                    setIsAddingSource(false);
                    setNewSourceName("");
                    setNewSourceUrl("");
                    setNewSourceError("");
                  }}
                >
                  取消
                </Button>
              </div>
            </div>
          </form>
        )}

        {options.length > 0 ? (
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
            {options.map((option, index) => {
              const selected = draft.selected_ids.includes(option.id);
              const inputId = `${idPrefix}-option-${index}`;
              return (
                <div
                  key={option.id}
                  className={cn(
                    "flex min-w-0 items-start gap-1 rounded-lg border p-3 transition-colors hover:bg-muted/30",
                    selected && "border-primary/50 bg-primary/5",
                    option.filtered && "bg-muted/20 hover:bg-muted/20",
                    isBusy && "cursor-wait"
                  )}
                >
                  <label
                    htmlFor={inputId}
                    className={cn(
                      "flex min-w-0 flex-1 cursor-pointer items-start gap-3",
                      option.filtered && "cursor-not-allowed"
                    )}
                  >
                    <input
                      id={inputId}
                      type="checkbox"
                      checked={selected}
                      onChange={() => toggleOption(option.id)}
                      disabled={option.filtered || isBusy}
                      className="mt-0.5 h-4 w-4 shrink-0 rounded border-input accent-primary disabled:cursor-not-allowed disabled:opacity-50"
                    />
                    <div className="min-w-0 flex-1 space-y-1.5">
                      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                        <span
                          className={cn(
                            "break-words text-sm font-medium",
                            option.filtered && "text-muted-foreground"
                          )}
                        >
                          {option.name}
                        </span>
                        {option.custom && (
                          <Badge variant="outline" className="h-4 text-[10px] font-normal">
                            自定义
                          </Badge>
                        )}
                        {option.default_selected && (
                          <Badge variant="outline" className="h-4 text-[10px] font-normal">
                            默认
                          </Badge>
                        )}
                        {option.filtered && (
                          <Badge variant="secondary" className="h-4 text-[10px] font-normal">
                            已筛选
                          </Badge>
                        )}
                      </div>
                      {!option.custom && (
                        <p className="break-all font-mono text-[10px] text-muted-foreground">
                          {option.id}
                        </p>
                      )}
                      {option.description && (
                        <p className="break-words text-xs leading-5 text-muted-foreground">
                          {option.description}
                        </p>
                      )}
                      {(option.language || option.group) && (
                        <div className="flex flex-wrap gap-1.5">
                          {option.language && (
                            <Badge variant="outline" className="h-4 max-w-full text-[10px] font-normal">
                              <span className="truncate">{option.language}</span>
                            </Badge>
                          )}
                          {option.group && (
                            <Badge variant="outline" className="h-4 max-w-full text-[10px] font-normal">
                              <span className="truncate">{option.group}</span>
                            </Badge>
                          )}
                        </div>
                      )}
                      {option.filtered && option.filter_reason && (
                        <p className="break-words text-xs leading-5 text-amber-600 dark:text-amber-400">
                          筛选原因：{option.filter_reason}
                        </p>
                      )}
                    </div>
                  </label>
                  {option.custom && (
                    <Button
                      type="button"
                      size="icon"
                      variant="ghost"
                      className="h-8 w-8 shrink-0 text-muted-foreground hover:text-destructive"
                      aria-label={`删除自定义${sourceLabel} ${option.name}`}
                      title={`删除自定义${sourceLabel}`}
                      onClick={() => removeCustomSource(option.id)}
                      disabled={isBusy}
                    >
                      <Trash2 className="h-3.5 w-3.5" aria-hidden="true" />
                    </Button>
                  )}
                </div>
              );
            })}
          </div>
        ) : (
          <div className="rounded-lg border border-dashed p-6 text-center text-xs text-muted-foreground">
            暂无可选{sourceLabel}
          </div>
        )}
      </section>

      <LastResultSummary last={data.last_result} />
    </div>
  );
}
