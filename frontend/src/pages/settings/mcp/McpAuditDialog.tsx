import { useMemo, useState } from "react";
import {
  AlertCircle,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  Loader2,
  RotateCcw,
  Search,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
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
import { useMcpAudit, type McpAuditRecord } from "@/hooks/useMcp";
import { cn, formatDate } from "@/lib/utils";

const PAGE_SIZE = 20;
const EMPTY_RECORDS: McpAuditRecord[] = [];

function formatDuration(durationMs: number) {
  return durationMs < 1000
    ? `${Math.round(durationMs)} ms`
    : `${(durationMs / 1000).toFixed(2)} s`;
}

function AuditBody() {
  const auditQuery = useMcpAudit(100);
  const records = auditQuery.data ?? EMPTY_RECORDS;
  const [keywordInput, setKeywordInput] = useState("");
  const [keyword, setKeyword] = useState("");
  const [resultFilter, setResultFilter] = useState("all");
  const [page, setPage] = useState(1);

  const filteredRecords = useMemo(() => {
    const needle = keyword.trim().toLocaleLowerCase();
    return records.filter((record) => {
      if (resultFilter === "success" && !record.success) return false;
      if (resultFilter === "failed" && record.success) return false;
      if (!needle) return true;
      return [record.tool_name, record.error_message ?? ""]
        .join("\n")
        .toLocaleLowerCase()
        .includes(needle);
    });
  }, [keyword, records, resultFilter]);

  const totalPages = Math.max(1, Math.ceil(filteredRecords.length / PAGE_SIZE));
  const currentPage = Math.min(page, totalPages);
  const pageStart = (currentPage - 1) * PAGE_SIZE;
  const pageRecords = filteredRecords.slice(pageStart, pageStart + PAGE_SIZE);
  const hasActiveFilters = Boolean(keyword) || resultFilter !== "all";

  const applySearch = (event: React.FormEvent) => {
    event.preventDefault();
    setKeyword(keywordInput.trim());
    setPage(1);
  };

  const resetSearch = () => {
    setKeywordInput("");
    setKeyword("");
    setResultFilter("all");
    setPage(1);
  };

  if (auditQuery.isLoading) {
    return (
      <div className="flex items-center gap-2 py-10 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
        加载审计记录...
      </div>
    );
  }

  if (auditQuery.error) {
    return (
      <div className="flex flex-wrap items-center gap-3 py-6 text-sm text-destructive">
        <AlertCircle className="h-4 w-4 shrink-0" aria-hidden="true" />
        <span>审计记录加载失败。</span>
        <Button type="button" variant="outline" size="sm" onClick={() => auditQuery.refetch()}>
          重新加载
        </Button>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      <form onSubmit={applySearch} className="flex flex-wrap items-center gap-2">
        <div className="relative w-full sm:w-64">
          <Search
            className="pointer-events-none absolute top-1/2 left-3 h-4 w-4 -translate-y-1/2 text-muted-foreground"
            aria-hidden="true"
          />
          <Label htmlFor="mcp-audit-search" className="sr-only">
            搜索审计记录
          </Label>
          <Input
            id="mcp-audit-search"
            value={keywordInput}
            onChange={(event) => setKeywordInput(event.target.value)}
            placeholder="搜索工具名或错误"
            className="h-9 pl-9"
          />
        </div>
        <div className="relative shrink-0">
          <select
            aria-label="结果筛选"
            value={resultFilter}
            onChange={(event) => {
              setResultFilter(event.target.value);
              setPage(1);
            }}
            className="h-9 w-28 appearance-none rounded-lg border border-input bg-background pr-8 pl-3 text-sm text-foreground outline-none transition-colors focus:border-ring focus:ring-2 focus:ring-ring/20 dark:bg-input/30"
          >
            <option value="all">全部结果</option>
            <option value="success">成功</option>
            <option value="failed">失败</option>
          </select>
          <ChevronDown
            className="pointer-events-none absolute top-1/2 right-2.5 h-4 w-4 -translate-y-1/2 text-muted-foreground"
            aria-hidden="true"
          />
        </div>
        <Button type="submit" size="sm" className="h-9 gap-1.5 px-3.5">
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
      </form>

      {filteredRecords.length === 0 ? (
        <div className="rounded-lg border border-dashed py-12 text-center text-sm text-muted-foreground">
          {hasActiveFilters ? "没有匹配的审计记录。" : "暂无工具调用记录。"}
        </div>
      ) : (
        <>
          <div className="max-h-[55vh] overflow-auto rounded-lg border">
            <Table className="min-w-[900px]">
              <TableHeader className="sticky top-0 z-10">
                <TableRow>
                  <TableHead>工具</TableHead>
                  <TableHead>结果</TableHead>
                  <TableHead>耗时</TableHead>
                  <TableHead>时间</TableHead>
                  <TableHead>错误</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {pageRecords.map((record) => (
                  <TableRow key={record.id}>
                    <TableCell className="font-mono text-xs">{record.tool_name}</TableCell>
                    <TableCell>
                      <Badge
                        variant="outline"
                        className={
                          record.success
                            ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400"
                            : "border-destructive/30 bg-destructive/10 text-destructive"
                        }
                      >
                        {record.success ? "成功" : "失败"}
                      </Badge>
                    </TableCell>
                    <TableCell className="tabular-nums">
                      {formatDuration(record.duration_ms)}
                    </TableCell>
                    <TableCell className="text-xs">{formatDate(record.created_at)}</TableCell>
                    <TableCell
                      className={cn(
                        "max-w-72 truncate text-xs",
                        record.error_message ? "text-destructive" : "text-muted-foreground"
                      )}
                      title={record.error_message ?? undefined}
                    >
                      {record.error_message || "-"}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>

          <div className="flex flex-wrap items-center justify-between gap-2 text-xs text-muted-foreground">
            <span className="tabular-nums">
              共 {filteredRecords.length} 条 · 第 {pageStart + 1}–
              {pageStart + pageRecords.length} 条
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
    </div>
  );
}

export function McpAuditDialog({
  open,
  onOpenChange,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-5xl">
        <DialogHeader className="pr-8">
          <DialogTitle>OneTap 工具调用</DialogTitle>
        </DialogHeader>
        {open && <AuditBody />}
      </DialogContent>
    </Dialog>
  );
}
