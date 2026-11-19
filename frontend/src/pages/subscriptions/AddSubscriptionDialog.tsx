import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertCircle,
  BookImage,
  BookOpen,
  Film,
  Loader2,
  Music,
  Search,
} from "lucide-react";
import {
  addResourceSubscription,
  getResourceCapabilities,
  searchResources,
  type ResourceDomain,
  type ResourceInstance,
  type ResourceSearchItem,
} from "@/api/resources";
import type { ApiError } from "@/api/types";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { useToast } from "@/components/ui/toast";
import { cn } from "@/lib/utils";

const DOMAINS: {
  value: ResourceDomain;
  label: string;
  icon: typeof Film;
}[] = [
  { value: "video", label: "影视", icon: Film },
  { value: "comic", label: "漫画", icon: BookImage },
  { value: "novel", label: "小说", icon: BookOpen },
  { value: "music", label: "音乐", icon: Music },
];

const VARIANT_LABEL: Partial<
  Record<ResourceDomain, { title: string; labels: Record<string, string> }>
> = {
  video: {
    title: "影视方案",
    labels: { local: "本地（MoviePilot）", cloud: "网盘（巡影）" },
  },
  comic: {
    title: "漫画方案",
    labels: { local: "本地（Comic Auto）", online: "在线（Suwayomi）" },
  },
};

const COMIC_STATUS_LABELS: Record<string, string> = {
  ONGOING: "连载中",
  ENDED: "已完结",
  FINISHED: "已完结",
  ON_HIATUS: "休刊中",
  HIATUS: "休刊中",
  CANCELLED: "已取消",
};

function value(item: ResourceSearchItem, key: string): string {
  const raw = item[key];
  if (raw === null || raw === undefined) return "";
  return typeof raw === "string" ? raw.trim() : String(raw).trim();
}

function itemTitle(item: ResourceSearchItem): string {
  return value(item, "title") || value(item, "name") || "未命名资源";
}

function itemKey(item: ResourceSearchItem): string {
  return (
    value(item, "item_id") ||
    value(item, "media_id") ||
    `${itemTitle(item)}-${value(item, "author")}`
  );
}

function coverUrl(
  item: ResourceSearchItem,
  instance?: ResourceInstance
): string {
  const cover = value(item, "cover") || value(item, "poster");
  if (!cover) return "";
  if (/^https?:\/\//.test(cover)) return cover;
  if (cover.startsWith("/") && instance?.url) {
    return `${instance.url.replace(/\/$/, "")}${cover}`;
  }
  return "";
}

function ResultCover({
  src,
  icon: Icon,
}: {
  src: string;
  icon: typeof Film;
}) {
  const [failed, setFailed] = useState(false);
  return (
    <div className="h-16 w-11 shrink-0 overflow-hidden rounded-md bg-muted">
      {src && !failed ? (
        <img
          src={src}
          alt=""
          loading="lazy"
          onError={() => setFailed(true)}
          className="h-full w-full object-cover"
        />
      ) : (
        <div className="flex h-full w-full items-center justify-center">
          <Icon className="h-5 w-5 text-muted-foreground/60" />
        </div>
      )}
    </div>
  );
}

function metaParts(domain: ResourceDomain, item: ResourceSearchItem): string[] {
  if (domain === "video") {
    const vote = Number(item.vote);
    return [
      value(item, "type"),
      value(item, "year"),
      Number.isFinite(vote) && vote > 0 ? `评分 ${vote}` : "",
    ].filter(Boolean);
  }
  if (domain === "comic" || domain === "music") {
    return [];
  }
  const total = Number(item.total_chapters);
  return [
    value(item, "author"),
    value(item, "origin_name"),
    Number.isFinite(total) && total > 0 ? `${total} 章` : "",
  ].filter(Boolean);
}

function musicLines(item: ResourceSearchItem): string[] {
  const credit = [value(item, "artist"), value(item, "album")]
    .filter(Boolean)
    .join(" · ");
  const quality = [value(item, "bitrate"), value(item, "size")]
    .filter(Boolean)
    .join(" · ");
  return [credit, quality].filter(Boolean);
}

function comicLines(item: ResourceSearchItem): string[] {
  const status = value(item, "status");
  const progress =
    value(item, "update_info") ||
    (Number(item.chapter_count) > 0 ? `${Number(item.chapter_count)} 话` : "");
  return [
    value(item, "author"),
    [COMIC_STATUS_LABELS[status] ?? status, progress]
      .filter(Boolean)
      .join(" · "),
  ].filter(Boolean);
}

function description(domain: ResourceDomain, item: ResourceSearchItem): string {
  if (domain === "novel") return value(item, "intro");
  if (domain === "video") return value(item, "overview");
  return "";
}

function subscribeOptions(
  domain: ResourceDomain,
  item: ResourceSearchItem,
  options2: { cloud?: boolean } = {}
): Record<string, unknown> {
  const options: Record<string, unknown> = {
    ...item,
    name: itemTitle(item),
  };
  if (domain === "video") {
    // 网盘方案由巡影按 TMDB 自行判定电影/剧集，不能默认成电影
    if (!options2.cloud || value(item, "type")) {
      options.type = value(item, "type") || "电影";
    }
  }
  if (domain === "comic" && Array.isArray(item.genre)) {
    options.tags = item.genre;
  }
  return options;
}

interface TabSearchState {
  keyword: string;
  items: ResourceSearchItem[];
  hasSearched: boolean;
  error: string;
}

const EMPTY_SEARCH: TabSearchState = {
  keyword: "",
  items: [],
  hasSearched: false,
  error: "",
};

function searchKey(domain: ResourceDomain, instanceId?: number): string {
  return instanceId ? `${domain}:${instanceId}` : domain;
}

interface AddSubscriptionDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  initialDomain?: ResourceDomain;
}

export function AddSubscriptionDialog({
  open,
  onOpenChange,
  initialDomain = "video",
}: AddSubscriptionDialogProps) {
  const toast = useToast();
  const queryClient = useQueryClient();
  const capabilities = useQuery({
    queryKey: ["resources", "capabilities"],
    queryFn: getResourceCapabilities,
    enabled: open,
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  });

  const [domain, setDomain] = useState<ResourceDomain>(initialDomain);
  const [selectedInstanceId, setSelectedInstanceId] = useState<
    Partial<Record<ResourceDomain, number>>
  >({});
  const [searches, setSearches] = useState<Record<string, TabSearchState>>({});
  const [searchingKey, setSearchingKey] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const currentCapability = capabilities.data?.domains[domain];
  const domainChoices = useMemo(() => {
    if (!currentCapability) return [];
    const choices = currentCapability.variants?.length
      ? currentCapability.variants
      : currentCapability.instances;
    return [...choices].sort(
      (a, b) => Number(b.variant === "local") - Number(a.variant === "local")
    );
  }, [currentCapability]);

  const chosenId = selectedInstanceId[domain];
  const selectedId = domainChoices.some(
    (instance) => instance.instance_id === chosenId
  )
    ? chosenId
    : domainChoices[0]?.instance_id;
  const activeInstance = domainChoices.find(
    (instance) => instance.instance_id === selectedId
  );
  const supportsSearch = activeInstance?.operations
    ? activeInstance.operations.includes("search")
    : true;
  const cloudVideo = domain === "video" && activeInstance?.variant === "cloud";
  const showVariantPicker =
    domainChoices.length > 1 && Boolean(VARIANT_LABEL[domain]);
  const activeKey = searchKey(domain, selectedId);
  const activeSearch = searches[activeKey] ?? EMPTY_SEARCH;
  const searching = searchingKey === activeKey;
  const keyword = activeSearch.keyword;
  const items = activeSearch.items;
  const hasSearched = activeSearch.hasSearched;
  const error = activeSearch.error;

  const patchSearch = (key: string, patch: Partial<TabSearchState>) => {
    setSearches((prev) => ({
      ...prev,
      [key]: { ...(prev[key] ?? EMPTY_SEARCH), ...patch },
    }));
  };

  useEffect(() => {
    if (!open) return;
    setDomain(initialDomain);
    setBusyId(null);
    setSearchingKey(null);
  }, [open, initialDomain]);

  const applyCandidates = (key: string, result: Record<string, unknown>) => {
    const rows = Array.isArray(result.candidates) ? result.candidates : [];
    const candidates = rows
      .filter(
        (row): row is ResourceSearchItem =>
          Boolean(row) && typeof row === "object"
      )
      .map((row) => row as ResourceSearchItem);
    patchSearch(key, {
      items: candidates,
      hasSearched: true,
      error: candidates.length
        ? ""
        : "巡影匹配到多个候选，但没有返回候选列表，请换个更完整的片名再试",
    });
  };

  const acquireByTitle = async (key: string, term: string) => {
    setSearchingKey(key);
    patchSearch(key, { error: "", items: [], hasSearched: false });
    try {
      const result = await addResourceSubscription({
        domain,
        item_id: "",
        options: { name: term },
        instance_id: selectedId,
      });
      if (result.status === "ambiguous") {
        applyCandidates(key, result);
        return;
      }
      toast.success(`已提交获取：${String(result.title ?? term)}`);
      await queryClient.invalidateQueries({
        queryKey: ["notifications", "subscriptions"],
      });
      onOpenChange(false);
    } catch (err) {
      const message =
        (err as ApiError).displayMessage ||
        (err instanceof Error ? err.message : "提交获取失败");
      patchSearch(key, { error: message, hasSearched: true });
    } finally {
      setSearchingKey((current) => (current === key ? null : current));
    }
  };

  const handleSearch = async () => {
    const key = activeKey;
    const term = activeSearch.keyword.trim();
    if (!term) {
      patchSearch(key, {
        error: supportsSearch ? "请输入搜索关键词" : "请输入片名",
      });
      return;
    }
    if (!currentCapability?.available) {
      patchSearch(key, {
        error: currentCapability?.reason || "当前资源类型没有可用实例",
      });
      return;
    }
    if (!supportsSearch) {
      await acquireByTitle(key, term);
      return;
    }
    setSearchingKey(key);
    patchSearch(key, { error: "", items: [], hasSearched: false });
    try {
      const result = await searchResources({
        domain,
        keyword: term,
        instance_id: selectedId,
        count: 20,
      });
      patchSearch(key, { items: result.items ?? [], hasSearched: true });
    } catch (err) {
      const message =
        (err as ApiError).displayMessage ||
        (err instanceof Error ? err.message : "搜索失败");
      patchSearch(key, { error: message, hasSearched: true });
    } finally {
      setSearchingKey((current) => (current === key ? null : current));
    }
  };

  const handleSubscribe = async (item: ResourceSearchItem) => {
    const key = itemKey(item);
    const title = itemTitle(item);
    const itemId = cloudVideo
      ? value(item, "tmdb_id") ||
        value(item, "media_id") ||
        value(item, "item_id")
      : domain === "video"
        ? value(item, "media_id")
        : value(item, "item_id") || title;

    const tabKey = activeKey;
    setBusyId(key);
    patchSearch(tabKey, { error: "" });
    try {
      const result = await addResourceSubscription({
        domain,
        item_id: itemId,
        options: subscribeOptions(domain, item, { cloud: cloudVideo }),
        instance_id: selectedId,
      });
      if (result.status === "ambiguous") {
        applyCandidates(tabKey, result);
        return;
      }
      const alreadySubscribed = result.already_subscribed === true;
      toast.success(
        alreadySubscribed
          ? `${title} 已在订阅中`
          : cloudVideo
            ? `已提交获取：${title}`
            : domain === "music"
              ? `已加入下载队列：${title}`
              : `已添加订阅：${title}`
      );
      await queryClient.invalidateQueries({
        queryKey: ["notifications", "subscriptions"],
      });
      onOpenChange(false);
    } catch (err) {
      const message =
        (err as ApiError).displayMessage ||
        (err instanceof Error ? err.message : "添加订阅失败");
      patchSearch(tabKey, { error: message });
      toast.error(message);
    } finally {
      setBusyId(null);
    }
  };

  const Icon = DOMAINS.find((item) => item.value === domain)?.icon ?? Film;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[85vh] w-[calc(100%-2rem)] flex-col gap-0 overflow-hidden p-0 sm:max-w-3xl">
        <DialogHeader className="border-b px-4 py-3 pr-10">
          <DialogTitle>添加订阅</DialogTitle>
        </DialogHeader>

        <div className="space-y-3 border-b px-4 py-3">
          <div className="flex flex-wrap items-center gap-2">
            <div className="flex flex-wrap gap-1.5">
              {DOMAINS.map((item) => {
                const ItemIcon = item.icon;
                const unavailable =
                  capabilities.data &&
                  capabilities.data.domains[item.value]?.available === false;
                return (
                  <button
                    key={item.value}
                    type="button"
                    onClick={() => setDomain(item.value)}
                    disabled={Boolean(unavailable)}
                    className={cn(
                      "inline-flex h-8 items-center gap-1.5 rounded-full border px-3 text-xs transition-colors",
                      domain === item.value
                        ? "border-primary bg-primary/10 text-primary"
                        : "border-border/70 text-muted-foreground hover:border-border hover:text-foreground",
                      unavailable && "cursor-not-allowed opacity-40"
                    )}
                  >
                    <ItemIcon className="h-3.5 w-3.5" aria-hidden="true" />
                    {item.label}
                  </button>
                );
              })}
            </div>

            {showVariantPicker && (
              <div className="flex flex-wrap items-center gap-1.5 text-xs sm:ml-auto">
                <span className="text-muted-foreground">
                  {VARIANT_LABEL[domain]?.title}
                </span>
                {domainChoices.map((instance) => (
                  <button
                    key={instance.instance_id}
                    type="button"
                    onClick={() =>
                      setSelectedInstanceId((prev) => ({
                        ...prev,
                        [domain]: instance.instance_id,
                      }))
                    }
                    title={instance.name}
                    className={cn(
                      "rounded-full border px-2.5 py-1 transition-colors",
                      selectedId === instance.instance_id
                        ? "border-primary bg-primary/10 text-primary"
                        : "border-border/70 text-muted-foreground hover:text-foreground"
                    )}
                  >
                    {VARIANT_LABEL[domain]?.labels[instance.variant ?? ""] ??
                      instance.name}
                  </button>
                ))}
              </div>
            )}
          </div>

          <form
            className="flex items-center gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              void handleSearch();
            }}
          >
            <div className="relative min-w-0 flex-1">
              <Search
                className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground"
                aria-hidden="true"
              />
              <Input
                value={keyword}
                onChange={(event) =>
                  patchSearch(activeKey, { keyword: event.target.value })
                }
                placeholder={
                  supportsSearch
                    ? `搜索${DOMAINS.find((item) => item.value === domain)?.label ?? "资源"}标题`
                    : "输入片名，直接获取"
                }
                aria-label="搜索资源"
                className="h-9 pl-8 text-sm"
              />
            </div>
            <Button
              type="submit"
              size="sm"
              disabled={searching || !currentCapability?.available}
              className="h-9 shrink-0 gap-1 px-3 text-xs"
            >
              {searching ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
              ) : (
                <Search className="h-3.5 w-3.5" aria-hidden="true" />
              )}
              {supportsSearch ? "搜索" : "获取"}
            </Button>
          </form>

          {capabilities.isLoading && (
            <p className="flex items-center gap-2 text-xs text-muted-foreground">
              <Loader2 className="h-3.5 w-3.5 animate-spin" aria-hidden="true" />
              正在读取可用资源…
            </p>
          )}
          {capabilities.isError && (
            <p className="text-xs text-destructive">读取可用资源失败，请稍后重试。</p>
          )}
          {currentCapability && !currentCapability.available && (
            <p className="text-xs text-muted-foreground">
              {currentCapability.reason || "该资源类型当前不可用"}
            </p>
          )}
          {currentCapability?.available && cloudVideo && (
            <p className="text-xs text-muted-foreground">
              网盘方案的搜索走 TMDB（只读，不会开始转存），选中条目后再提交巡影获取。
            </p>
          )}
          {error && (
            <p className="flex items-start gap-1.5 text-xs text-destructive">
              <AlertCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" aria-hidden="true" />
              {error}
            </p>
          )}
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto p-3">
          {searching ? (
            <div className="flex items-center justify-center gap-2 py-12 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
              {supportsSearch ? "正在搜索…" : "正在提交获取…"}
            </div>
          ) : items.length === 0 ? (
            <div className="flex flex-col items-center justify-center py-12 text-center">
              <Icon className="h-7 w-7 text-muted-foreground/50" aria-hidden="true" />
              <p className="mt-2 text-sm text-muted-foreground">
                {hasSearched
                  ? "没有找到相关资源"
                  : supportsSearch
                    ? "输入关键词搜索资源"
                    : "输入片名后点「获取」"}
              </p>
            </div>
          ) : (
            <div className="space-y-2">
              <div className="flex items-center justify-between px-1 text-[11px] text-muted-foreground">
                <span>
                  {supportsSearch
                    ? `找到 ${items.length} 个结果`
                    : `匹配到 ${items.length} 个候选`}
                </span>
                <span>
                  {supportsSearch ? "点击右侧按钮直接添加" : "选择要获取的条目"}
                </span>
              </div>
              {items.map((item) => {
                const key = itemKey(item);
                const title = itemTitle(item);
                const cover = coverUrl(item, activeInstance);
                const meta = metaParts(domain, item);
                const details =
                  domain === "music"
                    ? musicLines(item)
                    : domain === "comic"
                      ? comicLines(item)
                      : [];
                const summary = description(domain, item);
                const inLibrary = Boolean(item.in_library);
                const busy = busyId === key;
                const ItemIcon =
                  DOMAINS.find((entry) => entry.value === domain)?.icon ?? Film;
                return (
                  <div
                    key={key}
                    className="flex items-center gap-3 rounded-lg border border-border/70 bg-card p-2.5"
                  >
                    <ResultCover src={cover} icon={ItemIcon} />
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center justify-between gap-2">
                        <div className="min-w-0 flex-1">
                          <p
                            className="truncate text-sm font-medium"
                            title={title}
                          >
                            {title}
                          </p>
                          {meta.length > 0 && (
                            <p className="truncate text-[11px] text-muted-foreground">
                              {meta.join(" · ")}
                            </p>
                          )}
                          {details.length > 0 && (
                            <div className="mt-1 space-y-0.5">
                              {details.map((line) => (
                                <p
                                  key={line}
                                  className="truncate text-[11px] leading-4 text-muted-foreground"
                                >
                                  {line}
                                </p>
                              ))}
                            </div>
                          )}
                        </div>
                        <Button
                          type="button"
                          size="xs"
                          disabled={inLibrary || busy}
                          onClick={() => void handleSubscribe(item)}
                          className="shrink-0"
                        >
                          {busy ? (
                            <Loader2
                              className="h-3 w-3 animate-spin"
                              aria-hidden="true"
                            />
                          ) : null}
                          {inLibrary
                            ? "已在库"
                            : !supportsSearch
                              ? "获取"
                              : domain === "music"
                                ? "下载"
                                : "订阅"}
                        </Button>
                      </div>
                      {summary && (
                        <p className="mt-1 line-clamp-2 text-[11px] leading-4 text-muted-foreground">
                          {summary}
                        </p>
                      )}
                    </div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
