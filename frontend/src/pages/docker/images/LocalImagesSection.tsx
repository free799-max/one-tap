/**
 * 本地镜像管理区域组件（重构版）
 *
 * 表格展示本地镜像列表（名称/标签分开），支持搜索、Shift 多选、详情、删除、移除未使用镜像。
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
  useImages,
  useBatchRemoveImages,
  usePruneImages,
  type ImageInfo,
} from "@/hooks/useDocker";
import { ImageDetailDialog } from "./ImageDetailDialog";
import { formatBytes, formatDate } from "@/lib/utils";
import {
  Trash2,
  HardDrive,
  Loader2,
  AlertTriangle,
  Search,
  Info,
  X,
  ScanLine,
} from "lucide-react";

export function LocalImagesSection() {
  const { data: images = [], isLoading: imagesLoading } = useImages();
  const batchRemoveImages = useBatchRemoveImages();
  const pruneImages = usePruneImages();

  const [searchQuery, setSearchQuery] = useState("");
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [lastSelectedIndex, setLastSelectedIndex] = useState<number | null>(
    null
  );

  /** 生成行唯一选择键（同一 image_id 可能对应多个 tag） */
  const getSelectionKey = (img: ImageInfo) => `${img.image_id}:${img.tag}`;
  const [showBatchDeleteDialog, setShowBatchDeleteDialog] = useState(false);
  const [showPruneDialog, setShowPruneDialog] = useState(false);
  const [detailImageId, setDetailImageId] = useState<string | null>(null);
  const [showDetailDialog, setShowDetailDialog] = useState(false);

  // 搜索过滤
  const filteredImages = useMemo(() => {
    if (!searchQuery.trim()) return images;
    const q = searchQuery.trim().toLowerCase();
    return images.filter(
      (img) =>
        img.name.toLowerCase().includes(q) ||
        img.tag.toLowerCase().includes(q) ||
        img.full_tag.toLowerCase().includes(q)
    );
  }, [images, searchQuery]);

  // 判断选中项中是否有正在使用的镜像
  const hasRunningSelected = useMemo(() => {
    for (const key of selectedIds) {
      const img = filteredImages.find((i) => getSelectionKey(i) === key);
      if (img && img.containers > 0) return true;
    }
    return false;
  }, [selectedIds, filteredImages]);

  // 行选择逻辑
  const toggleSelect = (key: string, index: number, shiftKey: boolean) => {
    if (shiftKey && lastSelectedIndex !== null) {
      // Shift 多选：选中区间
      const start = Math.min(lastSelectedIndex, index);
      const end = Math.max(lastSelectedIndex, index);
      const next = new Set(selectedIds);
      for (let i = start; i <= end; i++) {
        if (i < filteredImages.length) {
          next.add(getSelectionKey(filteredImages[i]));
        }
      }
      setSelectedIds(next);
    } else {
      // 单选切换
      const next = new Set(selectedIds);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      setSelectedIds(next);
      setLastSelectedIndex(index);
    }
  };

  const handleBatchDelete = () => {
    if (selectedIds.size === 0) return;
    setShowBatchDeleteDialog(true);
  };

  const confirmBatchDelete = () => {
    const selectedImages = filteredImages.filter((img) =>
      selectedIds.has(getSelectionKey(img))
    );
    const tagsToDelete = selectedImages.map((img) => img.full_tag);
    batchRemoveImages.mutate(
      { ids: tagsToDelete, force: false },
      {
        onSettled: () => {
          setShowBatchDeleteDialog(false);
          setSelectedIds(new Set());
        },
      }
    );
  };

  const handlePrune = () => {
    setShowPruneDialog(true);
  };

  const confirmPrune = () => {
    pruneImages.mutate(undefined, {
      onSettled: () => {
        setShowPruneDialog(false);
        setSelectedIds(new Set());
      },
    });
  };

  const openDetail = (imageId: string) => {
    setDetailImageId(imageId);
    setShowDetailDialog(true);
  };

  return (
    <>
      <Card className="rounded-xl">
        <CardContent className="p-4 space-y-4">
          {/* 头部：标题 + 搜索 + 操作 */}
          <div className="flex items-center gap-3 flex-wrap">
            <div className="flex items-center gap-2">
              <HardDrive className="h-4 w-4 text-muted-foreground" />
              <h2 className="text-sm font-medium">本地镜像</h2>
              <span className="text-xs text-muted-foreground">
                共 {filteredImages.length} 个
              </span>
            </div>

            {/* 搜索框 + 已选 + 取消选择 */}
            <div className="flex items-center gap-2 flex-1 min-w-[180px]">
              <div className="relative w-56 shrink-0">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground/60 pointer-events-none" />
                <Input
                  placeholder="搜索镜像名称…"
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

            {/* 批量操作栏 */}
            <div className="flex items-center gap-2 ml-auto">
              <Button
                size="sm"
                variant="outline"
                className="h-8 text-xs"
                onClick={handlePrune}
                disabled={pruneImages.isPending}
              >
                <ScanLine className="h-3.5 w-3.5 mr-1" />
                移除未使用
              </Button>
              {selectedIds.size > 0 && (
                <Button
                  size="sm"
                  variant="destructive"
                  className="h-8 text-xs"
                  onClick={handleBatchDelete}
                  disabled={
                    batchRemoveImages.isPending || hasRunningSelected
                  }
                  title={
                    hasRunningSelected
                      ? "选中镜像中有正在使用的容器"
                      : undefined
                  }
                >
                  {batchRemoveImages.isPending ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Trash2 className="h-3.5 w-3.5" />
                  )}
                  <span className="ml-1.5">删除</span>
                </Button>
              )}
            </div>
          </div>

          {/* 镜像表格 */}
          {imagesLoading ? (
            <div className="flex justify-center py-12">
              <Loader2 className="h-6 w-6 animate-spin text-primary" />
            </div>
          ) : filteredImages.length === 0 ? (
            <p className="text-muted-foreground text-center py-12 text-sm">
              {searchQuery ? "未找到匹配的镜像" : "暂无本地镜像"}
            </p>
          ) : (
            <div className="border rounded-xl overflow-hidden px-3">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>名称</TableHead>
                    <TableHead>标签</TableHead>
                    <TableHead>ID</TableHead>
                    <TableHead>大小</TableHead>
                    <TableHead>创建时间</TableHead>
                    <TableHead>容器</TableHead>
                    <TableHead className="text-right">操作</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {filteredImages.map((img, index) => (
                    <TableRow
                      key={img.image_id + ":" + img.tag}
                      data-state={
                        selectedIds.has(getSelectionKey(img))
                          ? "selected"
                          : undefined
                      }
                      className={`cursor-pointer transition-colors ${selectedIds.has(getSelectionKey(img)) ? "bg-muted" : "hover:bg-muted/40"}`}
                      onMouseDown={(e) => {
                        // Shift 多选时阻止浏览器默认选中文本
                        if (e.shiftKey) {
                          e.preventDefault();
                        }
                      }}
                      onClick={(e) => {
                        // 点击按钮时不触发行选择
                        const target = e.target as HTMLElement;
                        if (target.closest("button")) {
                          return;
                        }
                        toggleSelect(
                          getSelectionKey(img),
                          index,
                          e.shiftKey
                        );
                      }}
                    >
                      <TableCell className="font-medium text-sm">
                        {img.name}
                      </TableCell>
                      <TableCell>
                        <Badge variant="outline" className="text-xs">
                          {img.tag}
                        </Badge>
                      </TableCell>
                      <TableCell className="font-mono text-xs">
                        {img.id}
                      </TableCell>
                      <TableCell className="text-sm">
                        {formatBytes(img.size)}
                      </TableCell>
                      <TableCell className="text-muted-foreground text-sm">
                        {formatDate(img.created)}
                      </TableCell>
                      <TableCell>
                        {img.containers > 0 ? (
                          <Badge
                            variant="default"
                            className="text-xs bg-primary/10 text-primary border-primary/20"
                          >
                            {img.containers} 个
                          </Badge>
                        ) : (
                          <span className="text-muted-foreground text-sm">-</span>
                        )}
                      </TableCell>
                      <TableCell className="text-right">
                        <Button
                          size="sm"
                          variant="ghost"
                          className="h-8 w-8 p-0 text-muted-foreground hover:text-primary"
                          onClick={() => openDetail(img.image_id)}
                        >
                          <Info className="h-4 w-4" />
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>

      {/* 批量删除确认对话框 */}
      <Dialog
        open={showBatchDeleteDialog}
        onOpenChange={setShowBatchDeleteDialog}
      >
        <DialogContent>
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <AlertTriangle className="h-5 w-5 text-destructive" />
              确认批量删除
            </DialogTitle>
            <DialogDescription className="mt-4">
              确定要删除选中的 <strong>{selectedIds.size}</strong>{" "}
              个镜像吗？此操作不可恢复。
            </DialogDescription>
          </DialogHeader>
          <div className="flex justify-end gap-2 pt-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setShowBatchDeleteDialog(false)}
            >
              取消
            </Button>
            <Button
              variant="destructive"
              size="sm"
              onClick={confirmBatchDelete}
              disabled={batchRemoveImages.isPending}
            >
              {batchRemoveImages.isPending && (
                <Loader2 className="h-4 w-4 animate-spin mr-1.5" />
              )}
              确认删除
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* 移除未使用镜像确认对话框 */}
      <Dialog open={showPruneDialog} onOpenChange={setShowPruneDialog}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              <AlertTriangle className="h-5 w-5 text-destructive" />
              确认移除未使用镜像
            </DialogTitle>
            <DialogDescription className="mt-4">
              将删除所有未被容器引用的镜像，此操作不可恢复。
            </DialogDescription>
          </DialogHeader>
          <div className="flex justify-end gap-2 pt-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => setShowPruneDialog(false)}
            >
              取消
            </Button>
            <Button
              variant="destructive"
              size="sm"
              onClick={confirmPrune}
              disabled={pruneImages.isPending}
            >
              {pruneImages.isPending && (
                <Loader2 className="h-4 w-4 animate-spin mr-1.5" />
              )}
              确认移除
            </Button>
          </div>
        </DialogContent>
      </Dialog>

      {/* 镜像详情弹窗 */}
      <ImageDetailDialog
        open={showDetailDialog}
        onOpenChange={setShowDetailDialog}
        imageId={detailImageId}
      />
    </>
  );
}
