/**
 * 订阅记录
 *
 * 展示通知台账中的资源订阅与下载请求，并按领域给出当前所处阶段。
 */

import { useMemo, useState } from "react";
import {
  BookImage,
  BookOpen,
  Check,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Film,
  Loader2,
  Music,
  Plus,
  Rss,
  Search,
  X,
} from "lucide-react";
import type { ResourceDomain } from "@/api/resources";
import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { Progress } from "@/components/ui/progress";
import {
  useSubscriptions,
  type SubscriptionRecord,
} from "@/hooks/useNotifications";
import { cn, formatRelativeTime } from "@/lib/utils";
import { AddSubscriptionDialog } from "./AddSubscriptionDialog";

const DOMAIN_LABELS: Record<string, string> = {
  video: "影视",
  comic: "漫画",
  novel: "小说",
  music: "音乐",
};

const DOMAIN_ICONS: Record<string, typeof Film> = {
  video: Film,
  comic: BookImage,
  novel: BookOpen,
  music: Music,
};

const STEPS: Record<string, string[]> = {
  video: ["已订阅", "下载中", "整理中", "已入库"],
  comic: ["已订阅", "下载中", "整理中", "已入库"],
  novel: ["已加入书架", "缓存章节", "导出 EPUB", "已完成"],
  music: ["已提交", "下载中", "整理中", "已入库"],
};

const STAGE_INDEX: Record<string, number> = {
  subscribed: 0,
  downloading: 1,
  downloaded: 1,
  organizing: 2,
  organized: 3,
  completed: 3,
};

const STAGE_LABELS: Record<string, string> = {
  subscribed: "已订阅",
  downloading: "下载中",
  downloaded: "已下载",
  organizing: "整理中",
  organized: "已入库",
  completed: "已完成",
  failed: "失败",
  removed: "已移除",
};

const EVENT_LABELS: Record<string, string> = {
  "subscription.added": "已加入订阅",
  "subscription.updated": "订阅有更新",
  "download.completed": "下载完成",
  "organize.completed": "整理入库",
};

const COMIC_STATUS_LABELS: Record<string, string> = {
  ONGOING: "连载中",
  ENDED: "已完结",
  FINISHED: "已完结",
  ON_HIATUS: "休刊中",
  HIATUS: "休刊中",
  CANCELLED: "已取消",
  CONTINUING: "连载中",
};

type StatusFilter = "all" | "active" | "done" | "failed";

const STATUS_FILTERS: { value: StatusFilter; label: string }[] = [
  { value: "all", label: "全部状态" },
  { value: "active", label: "进行中" },
  { value: "done", label: "已完成" },
  { value: "failed", label: "失败" },
];

const PAGE_SIZE = 20;

function paginationItems(
  current: number,
  total: number
): (number | "ellipsis")[] {
  if (total <= 7) {
    return Array.from({ length: total }, (_, index) => index + 1);
  }

  const pages = new Set<number>([1, total]);
  for (let page = current - 1; page <= current + 1; page += 1) {
    if (page > 1 && page < total) pages.add(page);
  }

  const sorted = [...pages].sort((a, b) => a - b);
  const items: (number | "ellipsis")[] = [];
  sorted.forEach((page, index) => {
    if (index > 0 && page - sorted[index - 1] > 1) items.push("ellipsis");
    items.push(page);
  });
  return items;
}

function text(value: unknown): string {
  return typeof value === "string" ? value.trim() : "";
}

function number(value: unknown): number {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function texts(value: unknown): string[] {
  return Array.isArray(value)
    ? value.map((item) => text(item)).filter(Boolean)
    : [];
}

function formatBytes(value: number): string {
  if (value <= 0) return "";
  const units = ["B", "KB", "MB", "GB"];
  let size = value;
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${size.toFixed(size >= 10 || unit === 0 ? 0 : 1)} ${units[unit]}`;
}

function stageLabel(record: SubscriptionRecord): string {
  if (record.stage === "subscribed") {
    return STEPS[record.domain]?.[0] ?? "已订阅";
  }
  return STAGE_LABELS[record.stage] ?? record.stage;
}

const STAGE_TONES: Record<
  string,
  { badge: string; bar: string; text: string }
> = {
  subscribed: {
    badge:
      "border-sky-500/40 bg-sky-500/10 text-sky-600 dark:text-sky-400",
    bar: "bg-sky-500",
    text: "text-sky-600 dark:text-sky-400",
  },
  downloading: {
    badge:
      "border-amber-500/40 bg-amber-500/10 text-amber-600 dark:text-amber-400",
    bar: "bg-amber-500",
    text: "text-amber-600 dark:text-amber-400",
  },
  downloaded: {
    badge:
      "border-violet-500/40 bg-violet-500/10 text-violet-600 dark:text-violet-400",
    bar: "bg-violet-500",
    text: "text-violet-600 dark:text-violet-400",
  },
  organizing: {
    badge:
      "border-indigo-500/40 bg-indigo-500/10 text-indigo-600 dark:text-indigo-400",
    bar: "bg-indigo-500",
    text: "text-indigo-600 dark:text-indigo-400",
  },
  organized: {
    badge:
      "border-emerald-500/40 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
    bar: "bg-emerald-500",
    text: "text-emerald-600 dark:text-emerald-400",
  },
  completed: {
    badge:
      "border-emerald-500/40 bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
    bar: "bg-emerald-500",
    text: "text-emerald-600 dark:text-emerald-400",
  },
  failed: {
    badge: "border-destructive/40 bg-destructive/10 text-destructive",
    bar: "bg-destructive",
    text: "text-destructive",
  },
  removed: {
    badge: "border-border bg-muted text-muted-foreground",
    bar: "bg-slate-400",
    text: "text-muted-foreground",
  },
};

function stageTone(record: SubscriptionRecord): string {
  return (STAGE_TONES[record.stage] ?? STAGE_TONES.subscribed).badge;
}

const STAGE_PROGRESS_PERCENT: Record<string, number> = {
  subscribed: 15,
  downloading: 45,
  downloaded: 65,
  organizing: 85,
  organized: 100,
  completed: 100,
  failed: 100,
  removed: 0,
};

function progressTone(record: SubscriptionRecord): {
  bar: string;
  text: string;
} {
  const tone = STAGE_TONES[record.stage] ?? STAGE_TONES.subscribed;
  return { bar: tone.bar, text: tone.text };
}

function progressOf(record: SubscriptionRecord): {
  percent: number;
  label: string;
} {
  const seen = record.last_seen ?? {};
  if (record.domain === "comic") {
    const done = number(seen.downloaded_chapters);
    const total = Math.max(number(seen.total_chapters), done);
    if (total > 0) {
      return {
        percent: Math.min(100, Math.round((done / total) * 100)),
        label: `已下载 ${done}/${total} 话`,
      };
    }
  }
  if (record.domain === "novel") {
    const done = number(seen.cached_chapters);
    const total = Math.max(number(seen.total_chapters), done);
    if (total > 0) {
      return {
        percent: Math.min(100, Math.round((done / total) * 100)),
        label: `已缓存 ${done}/${total} 章`,
      };
    }
  }
  if (record.domain === "video") {
    const total = number(seen.total_episode);
    const lack = number(seen.lack_episode);
    if (total > 0) {
      return {
        percent: Math.min(100, Math.round(((total - lack) / total) * 100)),
        label: `已下载 ${Math.max(0, total - lack)}/${total} 集`,
      };
    }
  }
  return {
    percent: STAGE_PROGRESS_PERCENT[record.stage] ?? 0,
    label: stageLabel(record),
  };
}

function detailLines(record: SubscriptionRecord): string[] {
  const seen = record.last_seen ?? {};
  const lines: string[] = [];

  if (record.domain === "video") {
    const summary = [
      text(seen.type),
      number(seen.rating) > 0 ? `评分 ${number(seen.rating)}` : "",
      number(seen.runtime) > 0 ? `${number(seen.runtime)} 分钟` : "",
    ].filter(Boolean);
    if (summary.length) lines.push(summary.join(" · "));
  } else if (record.domain === "comic") {
    const comicStatus = text(seen.comic_status);
    const summary = [
      text(seen.author) ? `作者 ${text(seen.author)}` : "",
      COMIC_STATUS_LABELS[comicStatus] ?? comicStatus,
      text(seen.latest_chapter),
    ].filter(Boolean);
    if (summary.length) lines.push(summary.join(" · "));
  } else if (record.domain === "novel") {
    const summary = [
      text(seen.author) ? `作者 ${text(seen.author)}` : "",
      text(seen.origin_name) ? `书源 ${text(seen.origin_name)}` : "",
    ].filter(Boolean);
    if (summary.length) lines.push(summary.join(" · "));
  } else if (record.domain === "music") {
    const summary = [
      text(seen.artist),
      text(seen.album),
      text(seen.audio_format).toUpperCase(),
      formatBytes(number(seen.file_size)),
    ].filter(Boolean);
    if (summary.length) lines.push(summary.join(" · "));
  }

  return lines;
}

function searchableText(record: SubscriptionRecord): string {
  const seen = record.last_seen ?? {};
  return [
    record.title,
    record.year,
    DOMAIN_LABELS[record.domain],
    text(seen.author),
    text(seen.artist),
    text(seen.album),
    text(seen.origin_name),
    texts(seen.genres).join(" "),
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
}

function SubscriptionCard({ record }: { record: SubscriptionRecord }) {
  const [posterFailed, setPosterFailed] = useState(false);
  const Icon = DOMAIN_ICONS[record.domain] ?? Rss;
  const steps = STEPS[record.domain] ?? STEPS.video;
  const failed = record.stage === "failed" || record.stage === "removed";
  const index = STAGE_INDEX[record.stage] ?? 0;
  const progress = progressOf(record);
  const tone = progressTone(record);
  const details = detailLines(record);
  const meta = [
    DOMAIN_LABELS[record.domain] ?? record.domain,
    record.kind === "request" ? "下载请求" : "订阅",
    record.year,
  ]
    .filter(Boolean)
    .join(" · ");

  return (
    <div className="flex min-w-0 gap-3 rounded-xl border border-border/70 bg-card px-3 py-3.5 shadow-sm">
      <div className="h-[120px] w-[84px] shrink-0 self-center overflow-hidden rounded-md bg-muted">
        {record.poster && !posterFailed ? (
          <img
            src={record.poster}
            alt=""
            loading="lazy"
            onError={() => setPosterFailed(true)}
            className="h-full w-full object-cover"
          />
        ) : (
          <div className="flex h-full w-full items-center justify-center">
            <Icon className="h-6 w-6 text-muted-foreground/60" />
          </div>
        )}
      </div>

      <div className="min-w-0 flex-1 space-y-2">
        <div className="flex flex-wrap items-start justify-between gap-x-2 gap-y-1">
          <div className="min-w-0 flex-1">
            <p
              className="truncate text-sm font-medium text-foreground"
              title={record.title}
            >
              {record.title || "未命名资源"}
            </p>
            <div className="mt-0.5 flex min-w-0 items-center gap-2">
              <p className="min-w-0 truncate text-[11px] text-muted-foreground">
                {meta}
              </p>
              <span
                className="flex shrink-0 items-center gap-1"
                role="img"
                aria-label={`当前阶段：${stageLabel(record)}`}
              >
                {steps.map((step, stepIndex) => (
                  <span
                    key={step}
                    title={step}
                    className={cn(
                      "h-1.5 w-1.5 rounded-full",
                      failed
                        ? "bg-destructive/30"
                        : stepIndex < index
                          ? cn(tone.bar, "opacity-40")
                          : stepIndex === index
                            ? tone.bar
                            : "bg-muted-foreground/20"
                    )}
                  />
                ))}
              </span>
            </div>
          </div>
          <span
            className={cn(
              "shrink-0 rounded-full border px-2 py-0.5 text-[10px] font-medium",
              stageTone(record)
            )}
          >
            {stageLabel(record)}
          </span>
        </div>

        <div className="space-y-1">
          <div className="flex items-center justify-between gap-2 text-[10px]">
            <span className="min-w-0 truncate text-muted-foreground">
              {progress.label}
            </span>
            <span className={cn("shrink-0 tabular-nums", tone.text)}>
              {progress.percent}%
            </span>
          </div>
          <Progress
            value={progress.percent}
            className="h-1"
            indicatorClassName={tone.bar}
          />
        </div>

        {details.length > 0 && (
          <div className="space-y-0.5">
            {details.map((line) => (
              <p
                key={line}
                className="truncate text-[11px] text-muted-foreground"
                title={line}
              >
                {line}
              </p>
            ))}
          </div>
        )}

        <div className="flex items-center justify-between gap-2 text-[10px] text-muted-foreground">
          <span className="min-w-0 truncate">
            更新于 {formatRelativeTime(record.updated_at)}
          </span>
          <span className="shrink-0 whitespace-nowrap">
            {EVENT_LABELS[record.last_event_type ?? ""] ?? stageLabel(record)}
          </span>
        </div>
      </div>
    </div>
  );
}

export function SubscriptionsPage() {
  const query = useSubscriptions();
  const [domain, setDomain] = useState<string>("all");
  const [status, setStatus] = useState<StatusFilter>("all");
  const [keyword, setKeyword] = useState("");
  const [searchKeyword, setSearchKeyword] = useState("");
  const [page, setPage] = useState(1);
  const [addOpen, setAddOpen] = useState(false);

  const records = useMemo(() => query.data ?? [], [query.data]);

  const domainCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    records.forEach((record) => {
      counts[record.domain] = (counts[record.domain] ?? 0) + 1;
    });
    return counts;
  }, [records]);

  const normalizedKeyword = searchKeyword.trim().toLowerCase();

  const filtered = useMemo(() => {
    return records
      .filter((record) => {
        if (domain !== "all" && record.domain !== domain) return false;
        if (
          normalizedKeyword &&
          !searchableText(record).includes(normalizedKeyword)
        ) {
          return false;
        }
        if (status === "active") {
          return [
            "subscribed",
            "downloading",
            "downloaded",
            "organizing",
          ].includes(record.stage);
        }
        if (status === "done") {
          return ["organized", "completed"].includes(record.stage);
        }
        if (status === "failed") return record.stage === "failed";
        return true;
      })
      .sort((a, b) => {
        const joined =
          new Date(b.created_at).getTime() - new Date(a.created_at).getTime();
        return joined || b.id - a.id;
      });
  }, [domain, normalizedKeyword, records, status]);

  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE));
  const currentPage = Math.min(page, totalPages);
  const pageRecords = filtered.slice(
    (currentPage - 1) * PAGE_SIZE,
    currentPage * PAGE_SIZE
  );
  const pageItems = paginationItems(currentPage, totalPages);
  const addInitialDomain: ResourceDomain =
    domain === "video" ||
    domain === "comic" ||
    domain === "novel" ||
    domain === "music"
      ? domain
      : "video";

  const domainFilters = [
    { value: "all", label: "全部", count: records.length },
    ...Object.entries(DOMAIN_LABELS).map(([value, label]) => ({
      value,
      label,
      count: domainCounts[value] ?? 0,
    })),
  ];

  return (
    <>
      <div className="rounded-xl border border-border/70 bg-card p-4 shadow-[var(--shadow-card)] sm:p-5">
        <div className="space-y-4">
        <div className="flex flex-col gap-2.5 lg:flex-row lg:items-center lg:justify-between">
          <div className="flex flex-wrap items-center gap-2">
            <form
              className="flex w-full flex-wrap items-center gap-2 sm:w-auto sm:flex-nowrap"
              onSubmit={(event) => {
                event.preventDefault();
                setSearchKeyword(keyword);
                setPage(1);
              }}
            >
              <div className="relative min-w-[180px] flex-1 sm:w-60 sm:flex-none">
                <Search
                  className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground"
                  aria-hidden="true"
                />
                <Input
                  value={keyword}
                  onChange={(event) => setKeyword(event.target.value)}
                  placeholder="搜索标题、作者、歌手"
                  aria-label="搜索订阅"
                  spellCheck={false}
                  className="h-8 w-full pl-8 pr-7 text-xs"
                />
                {keyword && (
                  <button
                    type="button"
                    onClick={() => {
                      setKeyword("");
                      setSearchKeyword("");
                      setPage(1);
                    }}
                    aria-label="清空搜索"
                    className="absolute right-2.5 top-1/2 -translate-y-1/2 text-muted-foreground/60 transition-colors hover:text-foreground"
                  >
                    <X className="h-3 w-3" aria-hidden="true" />
                  </button>
                )}
              </div>
              <DropdownMenu>
                <DropdownMenuTrigger asChild>
                  <button
                    type="button"
                    aria-label="订阅状态"
                    className="inline-flex h-8 w-[112px] shrink-0 items-center justify-between gap-2 rounded-md border border-input bg-background px-3 text-xs text-foreground shadow-sm outline-none transition-colors hover:border-primary/40 hover:bg-accent/40 focus-visible:border-primary data-[popup-open]:border-primary/50"
                  >
                    <span className="truncate">
                      {STATUS_FILTERS.find((item) => item.value === status)?.label}
                    </span>
                    <ChevronDown
                      className="h-3.5 w-3.5 shrink-0 text-muted-foreground"
                      aria-hidden="true"
                    />
                  </button>
                </DropdownMenuTrigger>
                <DropdownMenuContent
                  align="end"
                  sideOffset={6}
                  className="w-[112px] rounded-lg p-1"
                >
                  {STATUS_FILTERS.map((item) => (
                    <DropdownMenuItem
                      key={item.value}
                      onClick={() => {
                        setStatus(item.value);
                        setPage(1);
                      }}
                      className={cn(
                        "cursor-pointer rounded-md px-2 py-1.5 text-xs",
                        status === item.value && "font-medium text-primary"
                      )}
                    >
                      {item.label}
                      {status === item.value && (
                        <Check className="ml-auto h-3.5 w-3.5" aria-hidden="true" />
                      )}
                    </DropdownMenuItem>
                  ))}
                </DropdownMenuContent>
              </DropdownMenu>
              <Button
                type="submit"
                size="sm"
                className="h-8 shrink-0 gap-1 px-2.5 text-xs"
              >
                <Search className="h-3 w-3" aria-hidden="true" />
                <span>搜索</span>
              </Button>
            </form>
            <Button
              type="button"
              size="sm"
              onClick={() => setAddOpen(true)}
              className="h-8 shrink-0 gap-1 px-2.5 text-xs"
            >
              <Plus className="h-3.5 w-3.5" aria-hidden="true" />
              <span>订阅</span>
            </Button>
          </div>

          <div className="flex flex-wrap items-center gap-1.5 lg:justify-end">
            {domainFilters.map((item) => (
              <button
                key={item.value}
                type="button"
                onClick={() => {
                  setDomain(item.value);
                  setPage(1);
                }}
                className={cn(
                  "inline-flex items-center gap-1.5 rounded-full border px-3 py-1 text-xs font-medium transition-colors",
                  domain === item.value
                    ? "border-primary bg-primary text-primary-foreground shadow-sm"
                    : "border-border bg-card text-foreground hover:border-primary/40 hover:text-primary"
                )}
              >
                {item.label}
                <span
                  className={cn(
                    "rounded-full px-1.5 text-[10px] leading-4",
                    domain === item.value
                      ? "bg-primary-foreground/20 text-primary-foreground"
                      : "bg-muted text-muted-foreground"
                  )}
                >
                  {item.count}
                </span>
              </button>
            ))}
          </div>
        </div>

        {query.isLoading ? (
          <div className="flex items-center gap-2 py-10 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            正在读取订阅记录…
          </div>
        ) : query.isError ? (
          <div className="rounded-lg border border-destructive/30 p-4 text-sm text-muted-foreground">
            订阅记录读取失败，请稍后重试。
          </div>
        ) : filtered.length === 0 ? (
          <div className="rounded-xl border border-dashed p-8 text-center">
            <Rss
              className="mx-auto h-6 w-6 text-muted-foreground/60"
              aria-hidden="true"
            />
            <p className="mt-2 text-sm text-muted-foreground">
              {records.length === 0
                ? "还没有订阅记录。通过 AI 或应用发起订阅后会出现在这里。"
                : "没有符合筛选条件的订阅。"}
            </p>
          </div>
        ) : (
          <div className="space-y-3">
            <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4">
              {pageRecords.map((record) => (
                <SubscriptionCard key={record.id} record={record} />
              ))}
            </div>

            <div className="flex flex-col items-center justify-between gap-2 border-t border-border/60 pt-3 sm:flex-row">
              <p className="text-xs text-muted-foreground">
                共 {filtered.length} 条 · 第 {currentPage} / {totalPages} 页
              </p>
              <div className="flex items-center gap-1">
                <Button
                  type="button"
                  variant="outline"
                  size="icon-sm"
                  onClick={() => setPage(currentPage - 1)}
                  disabled={currentPage <= 1}
                  aria-label="上一页"
                >
                  <ChevronLeft className="h-3.5 w-3.5" aria-hidden="true" />
                </Button>
                {pageItems.map((item, index) =>
                  item === "ellipsis" ? (
                    <span
                      key={`ellipsis-${index}`}
                      className="px-1 text-xs text-muted-foreground"
                    >
                      …
                    </span>
                  ) : (
                    <Button
                      key={item}
                      type="button"
                      size="sm"
                      variant={item === currentPage ? "default" : "outline"}
                      onClick={() => setPage(item)}
                      aria-current={item === currentPage ? "page" : undefined}
                      className={cn(
                        "h-7 min-w-7 px-2 text-xs",
                        item !== currentPage && "text-muted-foreground"
                      )}
                    >
                      {item}
                    </Button>
                  )
                )}
                <Button
                  type="button"
                  variant="outline"
                  size="icon-sm"
                  onClick={() => setPage(currentPage + 1)}
                  disabled={currentPage >= totalPages}
                  aria-label="下一页"
                >
                  <ChevronRight className="h-3.5 w-3.5" aria-hidden="true" />
                </Button>
              </div>
            </div>
          </div>
        )}
        </div>
      </div>
      <AddSubscriptionDialog
        open={addOpen}
        onOpenChange={setAddOpen}
        initialDomain={addInitialDomain}
      />
    </>
  );
}
