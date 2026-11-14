import {
  Fragment,
  useDeferredValue,
  useMemo,
  useState,
} from "react";
import {
  AlertCircle,
  Boxes,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  CircleAlert,
  Database,
  Eye,
  FilterX,
  Layers3,
  ListChecks,
  Loader2,
  RefreshCw,
  Search,
  SearchX,
  Server,
  ShieldCheck,
  ShieldQuestion,
  Wrench,
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
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useToast } from "@/components/ui/toast";
import {
  useMcpCatalog,
  useUpdateMcpApiOperations,
  useUpdateMcpLocalTools,
  useUpdateMcpUpstream,
  type McpCatalogEntry,
  type McpCatalogSource,
} from "@/hooks/useMcp";
import { cn, formatDate } from "@/lib/utils";
import { McpCatalogDetailDialog } from "./McpCatalogDetailDialog";
import {
  McpEntryStatus,
  McpKindBadge,
  McpMethodBadge,
  McpRiskBadge,
} from "./McpCatalogBadges";

const PAGE_SIZES = [20, 50, 100];
const EMPTY_ENTRIES: McpCatalogEntry[] = [];
const EMPTY_SOURCES: McpCatalogSource[] = [];
const HIGH_RISKS = new Set(["write", "destructive", "unknown"]);
const SELECT_CLASS =
  "h-8 w-full rounded-lg border border-input bg-background px-2 text-sm text-foreground outline-none transition-colors focus:border-ring focus:ring-2 focus:ring-ring/20 disabled:cursor-not-allowed disabled:opacity-50 dark:bg-input/30";

interface DomainGroup {
  domain: string;
  entries: McpCatalogEntry[];
}

interface SourceGroup {
  source: McpCatalogSource | undefined;
  sourceId: string;
  sourceName: string;
  domains: DomainGroup[];
  pageCount: number;
}

function groupPageEntries(
  entries: McpCatalogEntry[],
  sourceMap: Map<string, McpCatalogSource>
): SourceGroup[] {
  const sources: SourceGroup[] = [];
  const sourceIndexes = new Map<string, number>();
  const domainIndexes = new Map<string, number>();

  entries.forEach((entry) => {
    let sourceIndex = sourceIndexes.get(entry.source);
    if (sourceIndex === undefined) {
      sourceIndex = sources.length;
      sourceIndexes.set(entry.source, sourceIndex);
      sources.push({
        source: sourceMap.get(entry.source),
        sourceId: entry.source,
        sourceName: entry.source_name,
        domains: [],
        pageCount: 0,
      });
    }
    const source = sources[sourceIndex];
    source.pageCount += 1;
    const domainKey = `${entry.source}\u0000${entry.domain}`;
    let domainIndex = domainIndexes.get(domainKey);
    if (domainIndex === undefined) {
      domainIndex = source.domains.length;
      domainIndexes.set(domainKey, domainIndex);
      source.domains.push({ domain: entry.domain, entries: [] });
    }
    source.domains[domainIndex].entries.push(entry);
  });
  return sources;
}

function pageItems(current: number, total: number): Array<number | string> {
  if (total <= 7) return Array.from({ length: total }, (_, index) => index + 1);
  const pages = new Set([1, total, current - 1, current, current + 1]);
  if (current <= 3) [2, 3, 4].forEach((page) => pages.add(page));
  if (current >= total - 2)
    [total - 3, total - 2, total - 1].forEach((page) => pages.add(page));
  const sorted = Array.from(pages)
    .filter((page) => page >= 1 && page <= total)
    .sort((a, b) => a - b);
  const result: Array<number | string> = [];
  sorted.forEach((page, index) => {
    if (index > 0 && page - sorted[index - 1] > 1) result.push("ellipsis");
    result.push(page);
  });
  return result;
}

function SourceIcon({ kind }: { kind?: string }) {
  if (kind === "remote_tool") return <Server aria-hidden="true" />;
  if (kind === "local_tool") return <Wrench aria-hidden="true" />;
  return <Boxes aria-hidden="true" />;
}

function SummaryButton({
  label,
  value,
  icon,
  active,
  onClick,
}: {
  label: string;
  value: number;
  icon: React.ReactNode;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      aria-pressed={active}
      onClick={onClick}
      className={cn(
        "min-w-0 rounded-xl border bg-card p-3 text-left shadow-sm transition-colors hover:border-primary/40 hover:bg-accent/40 focus-visible:outline-none focus-visible:ring-2 focus:ring-ring/30",
        active && "border-primary/50 bg-primary/[0.04] ring-1 ring-primary/15"
      )}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="truncate text-xs text-muted-foreground">{label}</span>
        <span className="shrink-0 text-muted-foreground [&_svg]:h-4 [&_svg]:w-4">
          {icon}
        </span>
      </div>
      <div className="mt-1 text-xl font-semibold tabular-nums text-foreground">
        {value}
      </div>
    </button>
  );
}

function FilterSelect({
  id,
  label,
  value,
  onChange,
  children,
}: {
  id: string;
  label: string;
  value: string;
  onChange: (value: string) => void;
  children: React.ReactNode;
}) {
  return (
    <div className="min-w-0 space-y-1">
      <Label htmlFor={id} className="text-[11px] text-muted-foreground">
        {label}
      </Label>
      <select
        id={id}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        className={SELECT_CLASS}
      >
        {children}
      </select>
    </div>
  );
}

function PermissionControl({
  entry,
  pendingAction,
  onToggle,
  onDetail,
  mobile = false,
}: {
  entry: McpCatalogEntry;
  pendingAction: string | null;
  onToggle: (entry: McpCatalogEntry, enabled: boolean) => void;
  onDetail: (entry: McpCatalogEntry) => void;
  mobile?: boolean;
}) {
  const blockedReason =
    entry.blocked_reason || (!entry.invocable ? "当前接口不可调用" : "");
  const disabled =
    pendingAction !== null || (Boolean(blockedReason) && !entry.enabled);
  const pending = pendingAction === entry.entry_id;
  return (
    <div
      className={cn(
        "flex shrink-0 items-center justify-end gap-2",
        mobile && "w-full justify-between border-t pt-3"
      )}
    >
      {mobile && (
        <Button type="button" variant="outline" size="sm" onClick={() => onDetail(entry)}>
          <Eye aria-hidden="true" />
          查看详情
        </Button>
      )}
      <div className="flex items-center gap-2">
        {mobile && (
          <span className="text-xs text-muted-foreground">
            {entry.enabled ? "已授权" : "未授权"}
          </span>
        )}
        <span title={blockedReason || undefined}>
          {pending ? (
            <Loader2
              className="mx-2 h-4 w-4 animate-spin text-muted-foreground"
              aria-label="正在保存权限"
            />
          ) : (
            <Switch
              aria-label={`${entry.enabled ? "撤销" : "授权"} ${entry.title || entry.name}`}
              checked={entry.enabled}
              disabled={disabled}
              onCheckedChange={(enabled) => onToggle(entry, enabled)}
            />
          )}
        </span>
        {!mobile && (
          <Button
            type="button"
            variant="ghost"
            size="icon-sm"
            onClick={() => onDetail(entry)}
            aria-label={`查看 ${entry.title || entry.name} 详情`}
            title="查看详情"
          >
            <Eye aria-hidden="true" />
          </Button>
        )}
      </div>
    </div>
  );
}

function DesktopEntryRow({
  entry,
  pendingAction,
  onToggle,
  onDetail,
}: {
  entry: McpCatalogEntry;
  pendingAction: string | null;
  onToggle: (entry: McpCatalogEntry, enabled: boolean) => void;
  onDetail: (entry: McpCatalogEntry) => void;
}) {
  return (
    <TableRow>
      <TableCell className="align-top whitespace-normal">
        <button
          type="button"
          onClick={() => onDetail(entry)}
          className="block max-w-full text-left text-xs font-medium break-words text-foreground hover:text-primary focus-visible:rounded focus-visible:outline-none focus-visible:ring-2 focus:ring-ring/30"
        >
          {entry.title || entry.name}
        </button>
        <code className="mt-1 block break-all font-mono text-[10px] leading-relaxed text-muted-foreground">
          {entry.public_name || entry.name}
        </code>
        {entry.description && (
          <p
            className="mt-1 line-clamp-1 text-[10px] text-muted-foreground"
            title={entry.description}
          >
            {entry.description}
          </p>
        )}
      </TableCell>
      <TableCell className="align-top whitespace-normal">
        <McpMethodBadge method={entry.method} />
        <code className="mt-1 block break-all font-mono text-[10px] leading-relaxed text-muted-foreground">
          {entry.path || entry.public_name || "MCP"}
        </code>
      </TableCell>
      <TableCell className="align-top whitespace-normal">
        <div className="text-xs break-words">{entry.source_name}</div>
        <div className="mt-1 text-[10px] break-words text-muted-foreground">
          {entry.domain}
        </div>
      </TableCell>
      <TableCell className="align-top whitespace-normal">
        <McpRiskBadge risk={entry.risk} kind={entry.kind} />
      </TableCell>
      <TableCell className="align-top whitespace-normal">
        <McpEntryStatus entry={entry} />
      </TableCell>
      <TableCell className="align-top whitespace-normal">
        <PermissionControl
          entry={entry}
          pendingAction={pendingAction}
          onToggle={onToggle}
          onDetail={onDetail}
        />
      </TableCell>
    </TableRow>
  );
}

function MobileEntryCard({
  entry,
  pendingAction,
  onToggle,
  onDetail,
}: {
  entry: McpCatalogEntry;
  pendingAction: string | null;
  onToggle: (entry: McpCatalogEntry, enabled: boolean) => void;
  onDetail: (entry: McpCatalogEntry) => void;
}) {
  return (
    <article className="min-w-0 space-y-3 rounded-xl border bg-card p-3 shadow-sm">
      <div className="flex min-w-0 items-start justify-between gap-2">
        <div className="min-w-0">
          <button
            type="button"
            onClick={() => onDetail(entry)}
            className="text-left text-sm font-medium break-words text-foreground hover:text-primary focus-visible:rounded focus-visible:outline-none focus-visible:ring-2 focus:ring-ring/30"
          >
            {entry.title || entry.name}
          </button>
          <code className="mt-1 block break-all font-mono text-[11px] text-muted-foreground">
            {entry.public_name || entry.name}
          </code>
        </div>
        <McpRiskBadge risk={entry.risk} kind={entry.kind} />
      </div>
      <div className="flex flex-wrap items-center gap-1.5">
        <McpMethodBadge method={entry.method} />
        <McpKindBadge kind={entry.kind} />
        <Badge variant="outline" className="font-normal">
          {entry.domain}
        </Badge>
      </div>
      <div className="min-w-0 text-xs">
        <span className="text-muted-foreground">来源：</span>
        <span className="break-words text-foreground">{entry.source_name}</span>
      </div>
      <code className="block break-all rounded-md bg-muted/60 p-2 font-mono text-[11px] leading-relaxed text-muted-foreground">
        {entry.path || entry.public_name || entry.name}
      </code>
      {entry.description && (
        <p className="text-xs leading-relaxed text-muted-foreground">
          {entry.description}
        </p>
      )}
      <McpEntryStatus entry={entry} />
      <PermissionControl
        entry={entry}
        pendingAction={pendingAction}
        onToggle={onToggle}
        onDetail={onDetail}
        mobile
      />
    </article>
  );
}

function SourceSection({
  group,
  filteredSourceCount,
  filteredDomainCounts,
  pendingAction,
  onToggle,
  onDetail,
}: {
  group: SourceGroup;
  filteredSourceCount: number;
  filteredDomainCounts: Map<string, number>;
  pendingAction: string | null;
  onToggle: (entry: McpCatalogEntry, enabled: boolean) => void;
  onDetail: (entry: McpCatalogEntry) => void;
}) {
  const available = group.source?.available ?? false;
  const error = group.source?.error;
  return (
    <section className="min-w-0 overflow-hidden rounded-xl border bg-card shadow-sm">
      <header className="flex min-w-0 flex-wrap items-center gap-2 border-b bg-muted/30 px-3 py-2.5">
        <span className="text-muted-foreground [&_svg]:h-4 [&_svg]:w-4">
          <SourceIcon kind={group.source?.kind} />
        </span>
        <h3 className="min-w-0 break-words text-sm font-medium">{group.sourceName}</h3>
        {group.source && <McpKindBadge kind={group.source.kind} />}
        <Badge
          variant="outline"
          className={cn(
            "font-normal",
            available
              ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400"
              : "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-400"
          )}
        >
          {available ? "来源正常" : "来源异常"}
        </Badge>
        <span className="ml-auto shrink-0 text-[11px] tabular-nums text-muted-foreground">
          本页 {group.pageCount} / 匹配 {filteredSourceCount}
        </span>
        {error && (
          <p className="w-full break-words text-[11px] leading-relaxed text-amber-700 dark:text-amber-400">
            {error}
          </p>
        )}
      </header>

      <div className="hidden lg:block">
        <Table className="table-fixed">
          <TableHeader>
            <TableRow>
              <TableHead className="w-[25%]">接口</TableHead>
              <TableHead className="w-[23%]">方法 / 路径</TableHead>
              <TableHead className="w-[13%]">来源</TableHead>
              <TableHead className="w-[9%]">风险</TableHead>
              <TableHead className="w-[19%]">授权 / 实际生效</TableHead>
              <TableHead className="w-[11%] text-right">权限 / 详情</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {group.domains.map((domain) => (
              <Fragment key={domain.domain}>
                <TableRow className="bg-muted/20 hover:bg-muted/20">
                  <TableCell colSpan={6} className="h-7 py-1 whitespace-normal">
                    <div className="flex items-center gap-1.5 text-[11px] font-medium text-muted-foreground">
                      <Layers3 className="h-3 w-3" aria-hidden="true" />
                      领域：{domain.domain}
                      <span className="tabular-nums">
                        ({filteredDomainCounts.get(`${group.sourceId}\u0000${domain.domain}`) ?? domain.entries.length})
                      </span>
                    </div>
                  </TableCell>
                </TableRow>
                {domain.entries.map((entry) => (
                  <DesktopEntryRow
                    key={entry.entry_id}
                    entry={entry}
                    pendingAction={pendingAction}
                    onToggle={onToggle}
                    onDetail={onDetail}
                  />
                ))}
              </Fragment>
            ))}
          </TableBody>
        </Table>
      </div>

      <div className="space-y-4 p-3 lg:hidden">
        {group.domains.map((domain) => (
          <div key={domain.domain} className="space-y-2">
            <h4 className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
              <Layers3 className="h-3.5 w-3.5" aria-hidden="true" />
              {domain.domain}
              <span className="tabular-nums">
                ({filteredDomainCounts.get(`${group.sourceId}\u0000${domain.domain}`) ?? domain.entries.length})
              </span>
            </h4>
            <div className="space-y-2">
              {domain.entries.map((entry) => (
                <MobileEntryCard
                  key={entry.entry_id}
                  entry={entry}
                  pendingAction={pendingAction}
                  onToggle={onToggle}
                  onDetail={onDetail}
                />
              ))}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

export function McpCatalog({
  sourceFilter,
  onSourceFilterChange,
}: {
  sourceFilter: string;
  onSourceFilterChange: (source: string) => void;
}) {
  const catalogQuery = useMcpCatalog();
  const updateLocalTools = useUpdateMcpLocalTools();
  const updateApiOperations = useUpdateMcpApiOperations();
  const updateUpstream = useUpdateMcpUpstream();
  const toast = useToast();
  const [search, setSearch] = useState("");
  const deferredSearch = useDeferredValue(search);
  const [domainFilter, setDomainFilter] = useState("all");
  const [riskFilter, setRiskFilter] = useState("all");
  const [enabledFilter, setEnabledFilter] = useState("all");
  const [effectiveFilter, setEffectiveFilter] = useState("all");
  const [pageSize, setPageSize] = useState(20);
  const [page, setPage] = useState(1);
  const [pendingAction, setPendingAction] = useState<string | null>(null);
  const [confirmEntry, setConfirmEntry] = useState<McpCatalogEntry | null>(null);
  const [detailEntry, setDetailEntry] = useState<McpCatalogEntry | null>(null);

  const entries = catalogQuery.data?.entries ?? EMPTY_ENTRIES;
  const sources = catalogQuery.data?.sources ?? EMPTY_SOURCES;
  const sourceMap = useMemo(
    () => new Map(sources.map((source) => [source.id, source])),
    [sources]
  );
  const domains = useMemo(
    () => Array.from(new Set(entries.map((entry) => entry.domain))).sort(),
    [entries]
  );

  const filteredEntries = useMemo(() => {
    const keyword = deferredSearch.trim().toLocaleLowerCase();
    const sourceOrder = new Map(sources.map((source, index) => [source.id, index]));
    return entries
      .filter((entry) => {
        const source = sourceMap.get(entry.source);
        const sourceMatches =
          sourceFilter === "all" ||
          (sourceFilter === "issues"
            ? !source || !source.available || Boolean(source.error)
            : entry.source === sourceFilter);
        if (!sourceMatches) return false;
        if (domainFilter !== "all" && entry.domain !== domainFilter) return false;
        if (riskFilter === "write-risk" && entry.risk === "read") return false;
        if (
          riskFilter !== "all" &&
          riskFilter !== "write-risk" &&
          entry.risk !== riskFilter
        )
          return false;
        if (enabledFilter === "enabled" && !entry.enabled) return false;
        if (enabledFilter === "disabled" && entry.enabled) return false;
        if (effectiveFilter === "effective" && !entry.effective) return false;
        if (effectiveFilter === "ineffective" && entry.effective) return false;
        if (!keyword) return true;
        const operationId = entry.kind === "onetap_api" ? entry.name : "";
        return [
          entry.title,
          entry.name,
          entry.public_name,
          operationId,
          entry.entry_id,
          entry.method,
          entry.path,
          entry.description,
        ]
          .join("\n")
          .toLocaleLowerCase()
          .includes(keyword);
      })
      .sort(
        (a, b) =>
          (sourceOrder.get(a.source) ?? 9999) - (sourceOrder.get(b.source) ?? 9999) ||
          a.domain.localeCompare(b.domain) ||
          a.path.localeCompare(b.path) ||
          a.method.localeCompare(b.method) ||
          a.name.localeCompare(b.name)
      );
  }, [
    deferredSearch,
    domainFilter,
    effectiveFilter,
    enabledFilter,
    entries,
    riskFilter,
    sourceFilter,
    sourceMap,
    sources,
  ]);

  const totalPages = Math.max(1, Math.ceil(filteredEntries.length / pageSize));
  const safePage = Math.min(page, totalPages);
  const pagedEntries = useMemo(
    () =>
      filteredEntries.slice((safePage - 1) * pageSize, safePage * pageSize),
    [filteredEntries, pageSize, safePage]
  );
  const groups = useMemo(
    () => groupPageEntries(pagedEntries, sourceMap),
    [pagedEntries, sourceMap]
  );
  const filteredSourceCounts = useMemo(() => {
    const counts = new Map<string, number>();
    filteredEntries.forEach((entry) =>
      counts.set(entry.source, (counts.get(entry.source) ?? 0) + 1)
    );
    return counts;
  }, [filteredEntries]);
  const filteredDomainCounts = useMemo(() => {
    const counts = new Map<string, number>();
    filteredEntries.forEach((entry) => {
      const key = `${entry.source}\u0000${entry.domain}`;
      counts.set(key, (counts.get(key) ?? 0) + 1);
    });
    return counts;
  }, [filteredEntries]);

  const enabledCount = entries.filter((entry) => entry.enabled).length;
  const effectiveCount = entries.filter((entry) => entry.effective).length;
  const readCount = entries.filter((entry) => entry.risk === "read").length;
  const writeOrHighRiskCount = entries.length - readCount;
  const abnormalSources = sources.filter(
    (source) => !source.available || Boolean(source.error)
  );
  const readOnlyApiCandidates = filteredEntries.filter(
    (entry) =>
      entry.kind === "onetap_api" && entry.risk === "read" && entry.invocable
  );
  const newReadOnlyApis = readOnlyApiCandidates.filter((entry) => !entry.enabled);
  const hasActiveFilters =
    Boolean(search.trim()) ||
    sourceFilter !== "all" ||
    domainFilter !== "all" ||
    riskFilter !== "all" ||
    enabledFilter !== "all" ||
    effectiveFilter !== "all";

  const resetPage = () => setPage(1);
  const clearFilters = () => {
    setSearch("");
    onSourceFilterChange("all");
    setDomainFilter("all");
    setRiskFilter("all");
    setEnabledFilter("all");
    setEffectiveFilter("all");
    setPage(1);
  };

  const saveEntryPermission = async (entry: McpCatalogEntry, enabled: boolean) => {
    if (pendingAction) return;
    setPendingAction(entry.entry_id);
    try {
      if (entry.kind === "onetap_api") {
        const operationIds = new Set(
          entries
            .filter((item) => item.kind === "onetap_api" && item.enabled)
            .map((item) => item.name)
        );
        if (enabled) operationIds.add(entry.name);
        else operationIds.delete(entry.name);
        await updateApiOperations.mutateAsync(Array.from(operationIds));
      } else if (entry.kind === "local_tool") {
        const enabledTools = new Set(
          entries
            .filter((item) => item.kind === "local_tool" && item.enabled)
            .map((item) => item.name)
        );
        if (enabled) enabledTools.add(entry.name);
        else enabledTools.delete(entry.name);
        await updateLocalTools.mutateAsync(Array.from(enabledTools));
      } else if (entry.kind === "remote_tool") {
        const instanceId = entry.upstream_instance_id;
        const source = sourceMap.get(entry.source);
        if (instanceId === null || !source) {
          toast.error("目录缺少上游实例信息，无法更新权限");
          return;
        }
        const allowedTools = new Set(
          entries
            .filter(
              (item) =>
                item.kind === "remote_tool" &&
                item.upstream_instance_id === instanceId &&
                item.enabled
            )
            .map((item) => item.name)
        );
        if (enabled) allowedTools.add(entry.name);
        else allowedTools.delete(entry.name);
        await updateUpstream.mutateAsync({
          instanceId,
          data: {
            enabled: source.enabled,
            allowed_tools: Array.from(allowedTools),
          },
        });
      } else {
        toast.error("未知条目类型，无法更新权限");
        return;
      }
      setConfirmEntry(null);
    } catch {
      // mutation hooks 已提示错误；不做乐观更新，保留服务端目录状态。
    } finally {
      setPendingAction(null);
    }
  };

  const requestToggle = (entry: McpCatalogEntry, enabled: boolean) => {
    if (
      enabled &&
      !entry.enabled &&
      HIGH_RISKS.has(entry.risk)
    ) {
      setConfirmEntry(entry);
      return;
    }
    void saveEntryPermission(entry, enabled);
  };

  const authorizeFilteredReadOnlyApis = async () => {
    if (pendingAction || newReadOnlyApis.length === 0) return;
    setPendingAction("batch:read-only-api");
    const operationIds = new Set(
      entries
        .filter((entry) => entry.kind === "onetap_api" && entry.enabled)
        .map((entry) => entry.name)
    );
    readOnlyApiCandidates.forEach((entry) => operationIds.add(entry.name));
    try {
      await updateApiOperations.mutateAsync(Array.from(operationIds));
    } catch {
      // 失败时不清空筛选，便于核对后重试。
    } finally {
      setPendingAction(null);
    }
  };

  const applySummary = (summary: string) => {
    if (summary === "all") {
      clearFilters();
      return;
    }
    if (summary === "issues") onSourceFilterChange("issues");
    if (summary === "enabled") setEnabledFilter("enabled");
    if (summary === "effective") setEffectiveFilter("effective");
    if (summary === "read") setRiskFilter("read");
    if (summary === "write-risk") setRiskFilter("write-risk");
    setPage(1);
  };

  const firstResult =
    filteredEntries.length === 0 ? 0 : (safePage - 1) * pageSize + 1;
  const lastResult = Math.min(safePage * pageSize, filteredEntries.length);
  const confirmRiskLabel =
    confirmEntry?.risk === "destructive"
      ? "破坏性"
      : confirmEntry?.risk === "unknown"
        ? "风险未知"
        : "写入";

  return (
    <div className="space-y-4">
      <Card className="rounded-xl" size="sm">
        <CardHeader className="pb-0">
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="space-y-1">
              <CardTitle className="flex items-center gap-2 text-base">
                <ListChecks className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
                完整接口目录
              </CardTitle>
              <CardDescription>
                汇总 OneTap OpenAPI、本地工具与所有远程 MCP 工具；筛选在完整数据上执行。
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
                刷新目录
              </Button>
            </div>
          </div>
        </CardHeader>
        <CardContent className="grid grid-cols-2 gap-2 md:grid-cols-3 xl:grid-cols-6">
          <SummaryButton
            label="全部接口"
            value={entries.length}
            icon={<Database aria-hidden="true" />}
            active={!hasActiveFilters}
            onClick={() => applySummary("all")}
          />
          <SummaryButton
            label="已授权"
            value={enabledCount}
            icon={<ShieldCheck aria-hidden="true" />}
            active={enabledFilter === "enabled"}
            onClick={() => applySummary("enabled")}
          />
          <SummaryButton
            label="实际生效"
            value={effectiveCount}
            icon={<CheckCircle2 aria-hidden="true" />}
            active={effectiveFilter === "effective"}
            onClick={() => applySummary("effective")}
          />
          <SummaryButton
            label="只读"
            value={readCount}
            icon={<ListChecks aria-hidden="true" />}
            active={riskFilter === "read"}
            onClick={() => applySummary("read")}
          />
          <SummaryButton
            label="写或高风险"
            value={writeOrHighRiskCount}
            icon={<ShieldQuestion aria-hidden="true" />}
            active={riskFilter === "write-risk"}
            onClick={() => applySummary("write-risk")}
          />
          <SummaryButton
            label="异常来源"
            value={abnormalSources.length}
            icon={<CircleAlert aria-hidden="true" />}
            active={sourceFilter === "issues"}
            onClick={() => applySummary("issues")}
          />
        </CardContent>
      </Card>

      <Card className="rounded-xl" size="sm">
        <CardContent className="space-y-3">
          <div className="relative">
            <Search
              className="pointer-events-none absolute top-1/2 left-3 h-4 w-4 -translate-y-1/2 text-muted-foreground"
              aria-hidden="true"
            />
            <Label htmlFor="mcp-catalog-search" className="sr-only">
              搜索接口目录
            </Label>
            <Input
              id="mcp-catalog-search"
              value={search}
              onChange={(event) => {
                setSearch(event.target.value);
                resetPage();
              }}
              placeholder="搜索标题、operationId、公开工具名、方法、路径或描述"
              className="h-9 pl-9"
            />
          </div>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 xl:grid-cols-6">
            <FilterSelect
              id="mcp-filter-source"
              label="来源"
              value={sourceFilter}
              onChange={(value) => {
                onSourceFilterChange(value);
                resetPage();
              }}
            >
              <option value="all">全部来源</option>
              <option value="issues">仅异常来源</option>
              {sources.map((source) => (
                <option key={source.id} value={source.id}>
                  {source.name} ({source.count})
                </option>
              ))}
            </FilterSelect>
            <FilterSelect
              id="mcp-filter-domain"
              label="领域"
              value={domainFilter}
              onChange={(value) => {
                setDomainFilter(value);
                resetPage();
              }}
            >
              <option value="all">全部领域</option>
              {domains.map((domain) => (
                <option key={domain} value={domain}>
                  {domain}
                </option>
              ))}
            </FilterSelect>
            <FilterSelect
              id="mcp-filter-risk"
              label="风险"
              value={riskFilter}
              onChange={(value) => {
                setRiskFilter(value);
                resetPage();
              }}
            >
              <option value="all">全部风险</option>
              <option value="read">只读</option>
              <option value="write-risk">写或高风险</option>
              <option value="write">写入</option>
              <option value="destructive">破坏性</option>
              <option value="unknown">未知 / 未声明</option>
              <option value="secret">敏感</option>
              <option value="control_plane">控制面</option>
              <option value="unsupported">不支持</option>
            </FilterSelect>
            <FilterSelect
              id="mcp-filter-enabled"
              label="授权"
              value={enabledFilter}
              onChange={(value) => {
                setEnabledFilter(value);
                resetPage();
              }}
            >
              <option value="all">全部授权状态</option>
              <option value="enabled">已授权</option>
              <option value="disabled">未授权</option>
            </FilterSelect>
            <FilterSelect
              id="mcp-filter-effective"
              label="实际生效"
              value={effectiveFilter}
              onChange={(value) => {
                setEffectiveFilter(value);
                resetPage();
              }}
            >
              <option value="all">全部生效状态</option>
              <option value="effective">实际生效</option>
              <option value="ineffective">未生效</option>
            </FilterSelect>
            <div className="flex items-end">
              <Button
                type="button"
                variant="outline"
                size="sm"
                className="w-full"
                disabled={!hasActiveFilters}
                onClick={clearFilters}
              >
                <FilterX aria-hidden="true" />
                清空筛选
              </Button>
            </div>
          </div>
          <div className="flex flex-col gap-2 border-t pt-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="text-xs text-muted-foreground" aria-live="polite">
              筛选结果 <span className="font-medium text-foreground">{filteredEntries.length}</span> 条，完整目录共 {entries.length} 条
              {search !== deferredSearch && <span className="ml-2">正在更新筛选...</span>}
            </div>
            <Button
              type="button"
              variant="secondary"
              size="sm"
              disabled={pendingAction !== null || newReadOnlyApis.length === 0}
              title={`当前筛选有 ${readOnlyApiCandidates.length} 个可调用只读 API，其中 ${newReadOnlyApis.length} 个尚未授权`}
              onClick={() => void authorizeFilteredReadOnlyApis()}
            >
              {pendingAction === "batch:read-only-api" ? (
                <Loader2 className="animate-spin" aria-hidden="true" />
              ) : (
                <ShieldCheck aria-hidden="true" />
              )}
              授权筛选中的只读 API ({newReadOnlyApis.length})
            </Button>
          </div>
        </CardContent>
      </Card>

      {catalogQuery.error && catalogQuery.data && (
        <div className="flex items-start gap-2 rounded-xl border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-amber-800 dark:text-amber-300">
          <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
          <p className="leading-relaxed">
            本次刷新失败，当前仍显示上次成功读取的完整目录。可稍后重试，已有权限配置不受影响。
          </p>
        </div>
      )}

      {abnormalSources.length > 0 && !catalogQuery.isLoading && (
        <div className="flex flex-col gap-2 rounded-xl border border-amber-500/30 bg-amber-500/10 p-3 text-xs text-amber-800 dark:text-amber-300 sm:flex-row sm:items-center">
          <CircleAlert className="h-4 w-4 shrink-0" aria-hidden="true" />
          <p className="min-w-0 flex-1 leading-relaxed">
            {abnormalSources.length} 个来源读取异常。其他接口仍可使用；已配置但本次未发现的远程权限会保留。
          </p>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => applySummary("issues")}
          >
            仅看异常来源
          </Button>
        </div>
      )}

      {catalogQuery.isLoading ? (
        <div className="flex min-h-64 flex-col items-center justify-center gap-3 rounded-xl border border-dashed text-sm text-muted-foreground">
          <Loader2 className="h-6 w-6 animate-spin" aria-hidden="true" />
          <p>正在读取全部 API 与 MCP 来源...</p>
          <p className="text-xs">远程来源较多时可能需要稍候</p>
        </div>
      ) : catalogQuery.error && !catalogQuery.data ? (
        <div className="flex min-h-64 flex-col items-center justify-center gap-3 rounded-xl border border-destructive/30 bg-destructive/5 p-6 text-center">
          <AlertCircle className="h-7 w-7 text-destructive" aria-hidden="true" />
          <div>
            <p className="text-sm font-medium text-destructive">接口目录加载失败</p>
            <p className="mt-1 text-xs text-muted-foreground">
              请检查登录状态和后端服务，然后重新加载。
            </p>
          </div>
          <Button type="button" variant="outline" size="sm" onClick={() => catalogQuery.refetch()}>
            <RefreshCw aria-hidden="true" />
            重新加载
          </Button>
        </div>
      ) : entries.length === 0 ? (
        <div className="flex min-h-64 flex-col items-center justify-center gap-3 rounded-xl border border-dashed p-6 text-center">
          <Database className="h-7 w-7 text-muted-foreground" aria-hidden="true" />
          <p className="text-sm text-muted-foreground">目录中暂无接口或工具。</p>
          <Button type="button" variant="outline" size="sm" onClick={() => catalogQuery.refetch()}>
            <RefreshCw aria-hidden="true" />
            重新读取
          </Button>
        </div>
      ) : filteredEntries.length === 0 ? (
        <div className="flex min-h-64 flex-col items-center justify-center gap-3 rounded-xl border border-dashed p-6 text-center">
          <SearchX className="h-7 w-7 text-muted-foreground" aria-hidden="true" />
          <div>
            <p className="text-sm font-medium">没有匹配的接口</p>
            <p className="mt-1 text-xs text-muted-foreground">
              可尝试搜索 operationId、缩短关键词，或清空来源与状态筛选。
            </p>
          </div>
          <Button type="button" variant="outline" size="sm" onClick={clearFilters}>
            <FilterX aria-hidden="true" />
            清空全部筛选
          </Button>
        </div>
      ) : (
        <div className="space-y-3">
          {groups.map((group) => (
            <SourceSection
              key={group.sourceId}
              group={group}
              filteredSourceCount={filteredSourceCounts.get(group.sourceId) ?? 0}
              filteredDomainCounts={filteredDomainCounts}
              pendingAction={pendingAction}
              onToggle={requestToggle}
              onDetail={setDetailEntry}
            />
          ))}
        </div>
      )}

      {filteredEntries.length > 0 && !catalogQuery.error && (
        <div className="flex flex-col gap-3 rounded-xl border bg-card p-3 text-xs sm:flex-row sm:items-center sm:justify-between">
          <div className="flex flex-wrap items-center gap-2 text-muted-foreground">
            <span>
              显示 {firstResult}-{lastResult} 条，共 {filteredEntries.length} 条
            </span>
            <Label htmlFor="mcp-page-size" className="sr-only">
              每页条数
            </Label>
            <select
              id="mcp-page-size"
              value={pageSize}
              onChange={(event) => {
                setPageSize(Number(event.target.value));
                setPage(1);
              }}
              className={cn(SELECT_CLASS, "w-24")}
            >
              {PAGE_SIZES.map((size) => (
                <option key={size} value={size}>
                  {size} 条/页
                </option>
              ))}
            </select>
          </div>
          <div className="flex flex-wrap items-center gap-1">
            <Button
              type="button"
              variant="outline"
              size="icon-sm"
              disabled={safePage <= 1}
              onClick={() => setPage(Math.max(1, safePage - 1))}
              aria-label="上一页"
            >
              <ChevronLeft aria-hidden="true" />
            </Button>
            <span className="px-2 tabular-nums text-muted-foreground sm:hidden">
              {safePage} / {totalPages}
            </span>
            <div className="hidden items-center gap-1 sm:flex">
              {pageItems(safePage, totalPages).map((item, index) =>
                typeof item === "number" ? (
                  <Button
                    key={item}
                    type="button"
                    variant={item === safePage ? "default" : "outline"}
                    size="icon-sm"
                    onClick={() => setPage(item)}
                    aria-label={`第 ${item} 页`}
                    aria-current={item === safePage ? "page" : undefined}
                  >
                    {item}
                  </Button>
                ) : (
                  <span key={`ellipsis-${index}`} className="w-6 text-center text-muted-foreground">
                    ...
                  </span>
                )
              )}
            </div>
            <Button
              type="button"
              variant="outline"
              size="icon-sm"
              disabled={safePage >= totalPages}
              onClick={() => setPage(Math.min(totalPages, safePage + 1))}
              aria-label="下一页"
            >
              <ChevronRight aria-hidden="true" />
            </Button>
          </div>
        </div>
      )}

      <McpCatalogDetailDialog
        key={detailEntry?.entry_id ?? "closed"}
        entry={detailEntry}
        onClose={() => setDetailEntry(null)}
      />
      <ConfirmDialog
        open={confirmEntry !== null}
        onOpenChange={(open) => {
          if (!open && pendingAction === null) setConfirmEntry(null);
        }}
        title={`确认授权${confirmRiskLabel}权限？`}
        description={`“${confirmEntry?.title || confirmEntry?.name || "该接口"}”属于${confirmRiskLabel}操作。授权后，具备有效 Token 的 MCP 客户端可能在来源和全局开关均启用时调用它。请确认你理解其影响。`}
        confirmText="确认授权"
        destructive={confirmEntry?.risk === "destructive"}
        isPending={pendingAction === confirmEntry?.entry_id}
        onConfirm={() => {
          if (confirmEntry) void saveEntryPermission(confirmEntry, true);
        }}
      />
    </div>
  );
}
