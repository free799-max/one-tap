/**
 * 容器列表区域组件
 *
 * 按镜像管理样式重构：表格展示容器列表，支持搜索、Shift 多选、批量操作、行内操作。
 */

import { useState, useMemo } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  useContainers,
  useBatchContainerAction,
} from "@/hooks/useDocker";
import { StatusDot } from "../shared/StatusDot";
import { ActionButton } from "../shared/ActionButton";
import { formatDate } from "@/lib/utils";
import {
  Play,
  Square,
  RotateCcw,
  Trash2,
  Search,
  X,
  Info,
  Terminal,
  FileText,
  Plus,
  Loader2,
  AlertTriangle,
  Container,
  RefreshCw,
} from "lucide-react";

interface ContainerListSectionProps {
  onOpenDetail: (id: string) => void;
  onOpenLogs: (id: string) => void;
  onOpenTerminal: (id: string, name: string) => void;
  onOpenCreate: () => void;
}

export function ContainerListSection({
  onOpenDetail,
  onOpenLogs,
  onOpenTerminal,
  onOpenCreate,
}: ContainerListSectionProps) {
  const {
    data: containers = [],
    isLoading: containersLoading,
    refetch,
    isRefetching,
  } = useContainers();
  const batchAction = useBatchContainerAction();

  const [searchQuery, setSearchQuery] = useState("");
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [lastSelectedIndex, setLastSelectedIndex] = useState<number | null>(null);
  const [batchActionType, setBatchActionType] = useState<
    "start" | "stop" | "restart" | "remove" | null
  >(null);

  const filteredContainers = useMemo(() => {
    if (!searchQuery.trim()) return containers;
    const q = searchQuery.trim().toLowerCase();
    return containers.filter(
      (c) =>
        c.name.toLowerCase().includes(q) ||
        c.image.toLowerCase().includes(q) ||
        c.id.toLowerCase().includes(q) ||
        c.ports.toLowerCase().includes(q)
    );
  }, [containers, searchQuery]);

  const toggleSelect = (id: string, index: number, shiftKey: boolean) => {
    if (shiftKey && lastSelectedIndex !== null) {
      const start = Math.min(lastSelectedIndex, index);
      const end = Math.max(lastSelectedIndex, index);
      const next = new Set(selectedIds);
      for (let i = start; i <= end; i++) {
        if (i < filteredContainers.length) {
          next.add(filteredContainers[i].id);
        }
      }
      setSelectedIds(next);
    } else {
      const next = new Set(selectedIds);
      if (next.has(id)) {
        next.delete(id);
      } else {
        next.add(id);
      }
      setSelectedIds(next);
      setLastSelectedIndex(index);
    }
  };

  const handleBatchConfirm = () => {
    if (!batchActionType || selectedIds.size === 0) return;
    batchAction.mutate(
      { ids: Array.from(selectedIds), action: batchActionType },
      {
        onSettled: () => {
          setBatchActionType(null);
          setSelectedIds(new Set());
          setLastSelectedIndex(null);
        },
      }
    );
  };

  const actionConfig: Record<
    string,
    { label: string; variant: "default" | "outline" | "destructive"; icon: React.ReactNode }
  > = {
    start: { label: "启动", variant: "default", icon: <Play className="h-3.5 w-3.5" /> },
    stop: { label: "停止", variant: "outline", icon: <Square className="h-3.5 w-3.5" /> },
    restart: { label: "重启", variant: "outline", icon: <RotateCcw className="h-3.5 w-3.5" /> },
    remove: { label: "删除", variant: "destructive", icon: <Trash2 className="h-3.5 w-3.5" /> },
  };

  return (
    <>
      <Card className="rounded-xl">
        <CardContent className="pt-1 pb-4 px-4 space-y-4">
          {/* 头部：标题 + 搜索 + 操作 */}
          <div className="flex items-center gap-3 flex-wrap">
            <div className="flex items-center gap-2">
              <Container className="h-4 w-4 text-muted-foreground" />
              <h2 className="text-sm font-medium">容器列表</h2>
              <span className="text-xs text-muted-foreground">
                共 {filteredContainers.length} 个
              </span>
            </div>

            {/* 搜索框 */}
            <div className="flex items-center gap-2 flex-1 min-w-[180px]">
              <div className="relative w-56 shrink-0">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground/60 pointer-events-none" />
                <Input
                  placeholder="搜索容器名称/镜像/端口…"
                  value={searchQuery}
                  onChange={(e) => {
                    setSearchQuery(e.target.value);
                    setSelectedIds(new Set());
                    setLastSelectedIndex(null);
                  }}
                  className="pl-8 pr-7 h-8 w-full rounded-full border border-black/25 bg-background text-sm shadow-none placeholder:text-muted-foreground/60 focus-visible:ring-0"
                  spellCheck={false}
                />
                {searchQuery && (
                  <button
                    onClick={() => {
                      setSearchQuery("");
                      setSelectedIds(new Set());
                    }}
                    className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground/60 hover:text-foreground"
                  >
                    <X className="h-4 w-4" />
                  </button>
                )}
              </div>
              {selectedIds.size > 0 && (
                <Badge variant="outline" className="text-xs h-7 shrink-0">
                  已选 {selectedIds.size} 个
                </Badge>
              )}
              {selectedIds.size >= 2 && (
                <Button
                  size="sm"
                  variant="outline"
                  className="h-7 px-2 text-xs shrink-0"
                  onClick={() => {
                    setSelectedIds(new Set());
                    setLastSelectedIndex(null);
                  }}
                >
                  取消选择
                </Button>
              )}
            </div>

            {/* 操作栏 */}
            <div className="flex items-center gap-2 ml-auto">
              <Button
                size="sm"
                variant="outline"
                className="h-8 text-xs"
                onClick={() => refetch()}
                disabled={containersLoading || isRefetching}
              >
                <RefreshCw
                  className={`h-3.5 w-3.5 mr-1 ${
                    isRefetching ? "animate-spin" : ""
                  }`}
                />
                刷新
              </Button>
              <Button
                size="sm"
                variant="outline"
                className="h-8 text-xs"
                onClick={onOpenCreate}
              >
                <Plus className="h-3.5 w-3.5 mr-1" />
                创建
              </Button>

              {selectedIds.size > 0 && (
                <>
                  {(["start", "stop", "restart", "remove"] as const).map((act) => (
                    <Button
                      key={act}
                      size="sm"
                      variant={actionConfig[act].variant}
                      className="h-8 text-xs"
                      onClick={() => setBatchActionType(act)}
                      disabled={batchAction.isPending}
                    >
                      {actionConfig[act].icon}
                      <span className="ml-1.5">{actionConfig[act].label}</span>
                    </Button>
                  ))}
                </>
              )}
            </div>
          </div>

          {/* 容器表格 */}
          {containersLoading ? (
            <div className="flex justify-center py-12">
              <Loader2 className="h-6 w-6 animate-spin text-primary" />
            </div>
          ) : filteredContainers.length === 0 ? (
            <p className="text-muted-foreground text-center py-12 text-sm">
              {searchQuery ? "未找到匹配的容器" : "暂无容器"}
            </p>
          ) : (
            <div className="border rounded-xl overflow-hidden px-3">
              <Table className="table-fixed min-w-[952px]">
                <TableHeader>
                  <TableRow>
                    <TableHead className="w-48">名称</TableHead>
                    <TableHead className="w-24">ID</TableHead>
                    <TableHead className="w-32">状态</TableHead>
                    <TableHead className="w-56">镜像</TableHead>
                    <TableHead className="w-40">端口映射</TableHead>
                    <TableHead className="w-28">创建时间</TableHead>
                    <TableHead className="w-16 px-0">
                      <div className="flex items-center justify-end h-5">
                        <span className="w-16 text-center leading-none">操作</span>
                      </div>
                    </TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {filteredContainers.map((c, index) => (
                    <TableRow
                      key={c.id}
                      data-state={selectedIds.has(c.id) ? "selected" : undefined}
                      className={`cursor-pointer transition-colors ${
                        selectedIds.has(c.id) ? "bg-muted" : "hover:bg-muted/40"
                      }`}
                      onMouseDown={(e) => {
                        if (e.shiftKey) e.preventDefault();
                      }}
                      onClick={(e) => {
                        const target = e.target as HTMLElement;
                        if (target.closest("button")) return;
                        toggleSelect(c.id, index, e.shiftKey);
                      }}
                    >
                      <TableCell className="font-medium text-sm truncate" title={c.name}>{c.name}</TableCell>
                      <TableCell className="font-mono text-xs text-muted-foreground">{c.id}</TableCell>
                      <TableCell>
                        <div className="flex items-center gap-1.5">
                          <StatusDot status={c.status} />
                          <span className="text-sm">{c.state}</span>
                          {c.health !== "unknown" && (
                            <Badge
                              variant={c.health === "healthy" ? "default" : "destructive"}
                              className="text-xs ml-1"
                            >
                              {c.health}
                            </Badge>
                          )}
                        </div>
                      </TableCell>
                      <TableCell className="text-sm truncate" title={c.image}>{c.image}</TableCell>
                      <TableCell className="text-xs text-muted-foreground whitespace-nowrap truncate" title={c.ports || undefined}>
                        {c.ports || "-"}
                      </TableCell>
                      <TableCell className="text-muted-foreground text-sm">
                        {formatDate(c.created)}
                      </TableCell>
                      <TableCell className="text-right p-0 align-middle h-10">
                        <div className="flex items-center justify-end gap-0.5 h-full">
                          <ActionButton
                            icon={FileText}
                            title="查看日志"
                            onClick={(e) => {
                              e.stopPropagation();
                              onOpenLogs(c.id);
                            }}
                          />
                          <ActionButton
                            icon={Terminal}
                            title="进入终端"
                            onClick={(e) => {
                              e.stopPropagation();
                              onOpenTerminal(c.id, c.name);
                            }}
                          />
                          <ActionButton
                            icon={Info}
                            title="查看详情"
                            onClick={(e) => {
                              e.stopPropagation();
                              onOpenDetail(c.id);
                            }}
                          />
                        </div>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>

      {/* 批量操作确认对话框 */}
      <Dialog
        open={batchActionType !== null}
        onOpenChange={(open) => {
          if (!open) setBatchActionType(null);
        }}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <AlertTriangle className="h-5 w-5 text-destructive" />
              确认批量{actionConfig[batchActionType || ""]?.label}
            </DialogTitle>
            <DialogDescription className="mt-4">
              确定要{actionConfig[batchActionType || ""]?.label}选中的{" "}
              <strong>{selectedIds.size}</strong> 个容器吗？
              {batchActionType === "remove" && " 此操作不可恢复。"}
            </DialogDescription>
          </DialogHeader>
          <div className="flex justify-end gap-2 pt-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setBatchActionType(null)}
            >
              取消
            </Button>
            <Button
              variant={
                batchActionType === "remove" ? "destructive" : "default"
              }
              size="sm"
              onClick={handleBatchConfirm}
              disabled={batchAction.isPending}
            >
              {batchAction.isPending && (
                <Loader2 className="h-4 w-4 animate-spin mr-1.5" />
              )}
              确认
            </Button>
          </div>
        </DialogContent>
      </Dialog>
    </>
  );
}
