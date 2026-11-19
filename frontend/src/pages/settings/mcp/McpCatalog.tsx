import { useMemo, useRef, useState } from "react";
import {
  AlertCircle,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Database,
  Eye,
  FilterX,
  History,
  Loader2,
  RefreshCw,
  RotateCcw,
  Search,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  useMcpCatalog,
  type McpCatalogEntry,
  type McpCatalogSource,
} from "@/hooks/useMcp";
import { cn, formatDate } from "@/lib/utils";
import { McpAuditDialog } from "./McpAuditDialog";
import { McpCatalogDetailDialog } from "./McpCatalogDetailDialog";
import { McpRiskBadge } from "./McpCatalogBadges";

const EMPTY_ENTRIES: McpCatalogEntry[] = [];
const EMPTY_SOURCES: McpCatalogSource[] = [];
const PAGE_SIZE = 20;
const SELECT_CLASS =
  "h-8 w-full rounded-lg border border-input bg-background px-2 text-sm text-foreground outline-none transition-colors focus:border-ring focus:ring-2 focus:ring-ring/20 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-input/30";

function DescriptionText({ text }: { text: string }) {
  const anchorRef = useRef<HTMLParagraphElement>(null);
  const [anchor, setAnchor] = useState<{ top: number; left: number } | null>(null);

  const show = () => {
    const rect = anchorRef.current?.getBoundingClientRect();
    if (!rect) return;
    setAnchor({ top: rect.bottom + 6, left: rect.left + rect.width / 2 });
  };

  return (
    <>
      <p
        ref={anchorRef}
        onMouseEnter={show}
        onMouseLeave={() => setAnchor(null)}
        className="truncate text-[10px] leading-relaxed text-muted-foreground"
      >
        {text}
      </p>
      {anchor && (
        <div
          className="pointer-events-none fixed z-50 w-[min(42rem,calc(100vw-3rem))] -translate-x-1/2 rounded-xl border bg-popover p-3 text-xs leading-relaxed text-popover-foreground shadow-lg"
          style={{ top: anchor.top, left: anchor.left }}
        >
          <div className="max-h-[40vh] overflow-auto">{text}</div>
        </div>
      )}
    </>
  );
}

function ToolRow({
  entry,
  onDetail,
}: {
  entry: McpCatalogEntry;
  onDetail: (entry: McpCatalogEntry) => void;
}) {
  return (
    <>
      <TableRow className="border-0 hover:bg-transparent data-[state=selected]:bg-transparent">
        <TableCell className="align-top whitespace-normal pb-0.5">
          <button
            type="button"
            onClick={() => onDetail(entry)}
            className="block max-w-full text-left font-mono text-xs font-medium break-words text-foreground hover:text-primary focus-visible:rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/30"
          >
            {entry.name}
          </button>
        </TableCell>
        <TableCell className="align-top break-words whitespace-normal text-xs text-muted-foreground">
          {entry.domain}
        </TableCell>
        <TableCell className="align-top whitespace-normal">
          <McpRiskBadge risk={entry.risk} kind={entry.kind} />
        </TableCell>
        <TableCell className="text-right align-top whitespace-normal">
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            onClick={() => onDetail(entry)}
            aria-label={`查看 ${entry.name} 详情`}
            title="查看详情"
          >
            <Eye aria-hidden="true" />
          </Button>
        </TableCell>
      </TableRow>
      {entry.description && (
        <TableRow className="border-b hover:bg-transparent data-[state=selected]:bg-transparent">
          <TableCell
            colSpan={4}
            className="px-2 pt-0 pb-1.5 whitespace-normal"
          >
            <DescriptionText text={entry.description} />
          </TableCell>
        </TableRow>
      )}
    </>
  );
}

function ToolCard({
  entry,
  onDetail,
}: {
  entry: McpCatalogEntry;
  onDetail: (entry: McpCatalogEntry) => void;
}) {
  return (
    <article className="min-w-0 space-y-2 rounded-xl border bg-card p-3">
      <div className="flex min-w-0 items-start justify-between gap-2">
        <button
          type="button"
          onClick={() => onDetail(entry)}
          className="text-left text-sm font-medium break-words text-foreground hover:text-primary focus-visible:rounded focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/30"
        >
          {entry.name}
        </button>
        <McpRiskBadge risk={entry.risk} kind={entry.kind} />
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        <Badge variant="outline" className="font-normal">
          {entry.domain}
        </Badge>
      </div>
      {entry.description && (
        <p className="text-xs leading-relaxed text-muted-foreground">
          {entry.description}
        </p>
      )}
      <div className="border-t pt-2 text-right">
        <Button type="button" variant="outline" size="sm" onClick={() => onDetail(entry)}>
          <Eye aria-hidden="true" />
          查看详情
        </Button>
      </div>
    </article>
  );
}

export function McpCatalog() {
  const catalogQuery = useMcpCatalog();
  const [searchInput, setSearchInput] = useState("");
  const [search, setSearch] = useState("");
  const [domainFilter, setDomainFilter] = useState("all");
  const [page, setPage] = useState(1);
  const [detailEntry, setDetailEntry] = useState<McpCatalogEntry | null>(null);
  const [auditOpen, setAuditOpen] = useState(false);

  const entries = catalogQuery.data?.entries ?? EMPTY_ENTRIES;
  const sources = catalogQuery.data?.sources ?? EMPTY_SOURCES;
  const domains = useMemo(
    () => Array.from(new Set(entries.map((entry) => entry.domain))).sort(),
    [entries]
  );
  const unavailableSources = useMemo(
    () => sources.filter((source) => !source.available || source.error),
    [sources]
  );

  const filteredEntries = useMemo(() => {
    const keyword = search.trim().toLocaleLowerCase();
    return entries
      .filter((entry) => {
        if (domainFilter !== "all" && entry.domain !== domainFilter) return false;
        if (!keyword) return true;
        return [
          entry.title,
          entry.name,
          entry.domain,
          entry.description,
        ]
          .join("\n")
          .toLocaleLowerCase()
          .includes(keyword);
      })
      .sort(
        (a, b) =>
          a.domain.localeCompare(b.domain) || a.name.localeCompare(b.name)
      );
  }, [domainFilter, entries, search]);

  const totalPages = Math.max(1, Math.ceil(filteredEntries.length / PAGE_SIZE));
  const currentPage = Math.min(page, totalPages);
  const pageStart = (currentPage - 1) * PAGE_SIZE;
  const pageEntries = filteredEntries.slice(pageStart, pageStart + PAGE_SIZE);

  const hasActiveFilters = Boolean(search) || domainFilter !== "all";

  const applySearch = (event: React.FormEvent) => {
    event.preventDefault();
    setSearch(searchInput.trim());
    setPage(1);
  };

  const resetSearch = () => {
    setSearchInput("");
    setSearch("");
    setDomainFilter("all");
    setPage(1);
  };

  return (
    <div className="space-y-4">
      <Card className="rounded-xl" size="sm">
        <CardHeader className="pb-3">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="space-y-1">
              <CardTitle className="flex items-center gap-2 text-base">
                <Database className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
                MCP 工具
                <span className="text-xs font-normal text-muted-foreground">
                  共 {filteredEntries.length} 个
                </span>
              </CardTitle>
              <CardDescription>
                OneTap 资源网关工具，AI Agent 通过它们查询、订阅、下载和替换各类资源。
              </CardDescription>
            </div>
            <div className="flex items-center gap-2">
              {catalogQuery.data && (
                <span className="hidden text-[11px] text-muted-foreground xl:inline">
                  生成于 {formatDate(catalogQuery.data.generated_at)}
                </span>
              )}
              <Button
                type="button"
                variant="outline"
                size="sm"
                disabled={catalogQuery.isFetching}
                onClick={() => catalogQuery.refetch()}
              >
                <RefreshCw
                  className={catalogQuery.isFetching ? "animate-spin" : undefined}
                  aria-hidden="true"
                />
                刷新
              </Button>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-3">
          <form
            onSubmit={applySearch}
            className="flex flex-wrap items-center gap-2"
          >
            <div className="relative w-full sm:w-96">
              <Search
                className="pointer-events-none absolute top-1/2 left-3 h-4 w-4 -translate-y-1/2 text-muted-foreground"
                aria-hidden="true"
              />
              <Label htmlFor="mcp-catalog-search" className="sr-only">
                搜索工具
              </Label>
              <Input
                id="mcp-catalog-search"
                value={searchInput}
                onChange={(event) => setSearchInput(event.target.value)}
                placeholder="搜索工具名、类别或描述"
                className="h-9 pl-9"
              />
            </div>
            <div className="relative shrink-0">
              <select
                id="mcp-filter-domain"
                aria-label="类别筛选"
                value={domainFilter}
                onChange={(event) => {
                  setDomainFilter(event.target.value);
                  setPage(1);
                }}
                className={cn(SELECT_CLASS, "h-9 w-40 appearance-none pr-8")}
              >
                <option value="all">全部类别</option>
                {domains.map((domain) => (
                  <option key={domain} value={domain}>
                    {domain}
                  </option>
                ))}
              </select>
              <ChevronDown
                className="pointer-events-none absolute top-1/2 right-2.5 h-4 w-4 -translate-y-1/2 text-muted-foreground"
                aria-hidden="true"
              />
            </div>
            <Button
              type="submit"
              size="sm"
              className="h-9 gap-1.5 px-3.5"
            >
              <Search aria-hidden="true" />
              搜索
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="h-9 px-2.5"
              disabled={!hasActiveFilters}
              onClick={resetSearch}
            >
              <RotateCcw aria-hidden="true" />
              重置
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              className="ml-auto h-9 px-2.5"
              onClick={() => setAuditOpen(true)}
            >
              <History aria-hidden="true" />
              审计记录
            </Button>
          </form>

          {catalogQuery.error && catalogQuery.data && (
            <div className="flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-amber-800 dark:text-amber-300">
              <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
              <p className="leading-relaxed">
                本次刷新失败，当前仍显示上次成功读取的工具列表。可稍后重试。
              </p>
            </div>
          )}

          {unavailableSources.length > 0 && (
            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-[11px] text-amber-800 dark:text-amber-300">
              <span className="flex items-center gap-1.5 font-medium">
                <AlertCircle className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                来源不可用
              </span>
              {unavailableSources.map((source) => (
                <span key={source.id}>
                  {source.name}：{source.error || "未知原因"}
                </span>
              ))}
            </div>
          )}

          {catalogQuery.isLoading ? (
            <div className="flex min-h-48 flex-col items-center justify-center gap-3 rounded-lg border border-dashed text-sm text-muted-foreground">
              <Loader2 className="h-6 w-6 animate-spin" aria-hidden="true" />
              <p>正在读取 OneTap MCP 工具...</p>
            </div>
          ) : catalogQuery.error && !catalogQuery.data ? (
            <div className="flex min-h-48 flex-col items-center justify-center gap-3 rounded-lg border border-destructive/30 bg-destructive/5 p-6 text-center">
              <AlertCircle className="h-7 w-7 text-destructive" aria-hidden="true" />
              <div>
                <p className="text-sm font-medium text-destructive">工具列表加载失败</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  请检查登录状态和后端服务，然后重新加载。
                </p>
              </div>
              <Button type="button" variant="outline" size="sm" onClick={() => catalogQuery.refetch()}>
                <RefreshCw aria-hidden="true" />
                重新加载
              </Button>
            </div>
          ) : filteredEntries.length === 0 ? (
            <div className="flex min-h-48 flex-col items-center justify-center gap-3 rounded-lg border border-dashed p-6 text-center">
              <Database className="h-7 w-7 text-muted-foreground" aria-hidden="true" />
              <div>
                <p className="text-sm font-medium">
                  {hasActiveFilters ? "没有匹配的工具" : "暂无可用工具"}
                </p>
                <p className="mt-1 text-xs text-muted-foreground">
                  {hasActiveFilters
                    ? "可尝试缩短关键词，或清空类别筛选。"
                    : "确认已部署支持 MCP 的应用后刷新。"}
                </p>
              </div>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={hasActiveFilters ? resetSearch : () => catalogQuery.refetch()}
              >
                {hasActiveFilters ? (
                  <>
                    <FilterX aria-hidden="true" />
                    清空筛选
                  </>
                ) : (
                  <>
                    <RefreshCw aria-hidden="true" />
                    重新读取
                  </>
                )}
              </Button>
            </div>
          ) : (
            <>
              <div className="hidden lg:block">
                <Table className="table-fixed">
                  <TableHeader>
                    <TableRow>
                      <TableHead className="w-[68%]">工具</TableHead>
                      <TableHead className="w-[14%]">类别</TableHead>
                      <TableHead className="w-[12%]">风险</TableHead>
                      <TableHead className="w-[6%] text-right">详情</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {pageEntries.map((entry) => (
                      <ToolRow key={entry.entry_id} entry={entry} onDetail={setDetailEntry} />
                    ))}
                  </TableBody>
                </Table>
              </div>
              <div className="space-y-2 lg:hidden">
                {pageEntries.map((entry) => (
                  <ToolCard key={entry.entry_id} entry={entry} onDetail={setDetailEntry} />
                ))}
              </div>

              <div className="flex flex-wrap items-center justify-between gap-2 border-t pt-3 text-xs text-muted-foreground">
                <span className="tabular-nums">
                  共 {filteredEntries.length} 个工具 · 第 {pageStart + 1}–
                  {pageStart + pageEntries.length} 条
                </span>
                <div className="flex items-center gap-1.5">
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="h-8"
                    disabled={currentPage <= 1}
                    onClick={() => setPage(currentPage - 1)}
                  >
                    <ChevronLeft aria-hidden="true" />
                    上一页
                  </Button>
                  <span className="px-1 tabular-nums">
                    {currentPage} / {totalPages}
                  </span>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    className="h-8"
                    disabled={currentPage >= totalPages}
                    onClick={() => setPage(currentPage + 1)}
                  >
                    下一页
                    <ChevronRight aria-hidden="true" />
                  </Button>
                </div>
              </div>
            </>
          )}
        </CardContent>
      </Card>

      <McpCatalogDetailDialog
        key={detailEntry?.entry_id ?? "closed"}
        entry={detailEntry}
        onClose={() => setDetailEntry(null)}
      />

      <McpAuditDialog open={auditOpen} onOpenChange={setAuditOpen} />
    </div>
  );
}
