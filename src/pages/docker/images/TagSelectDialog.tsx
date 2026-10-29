/**
 * 镜像标签选择弹窗
 *
 * 搜索镜像后选择具体标签，展示更新时间、大小等元信息，再启动后台拉取任务。
 */

import { useState, useMemo } from "react";
import { useImageTags, type ImageTag } from "@/hooks/useDocker";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Loader2,
  Download,
  Clock,
  HardDrive,
  Check,
  Tag,
  Search,
  X,
} from "lucide-react";
import { formatBytes, formatDate, formatRelativeTime } from "@/lib/utils";

interface TagSelectDialogProps {
  open: boolean;
  imageName: string;
  onClose: () => void;
  onConfirm: (imageWithTag: string) => void;
}

/** 标签排序：latest 置顶，其余按更新时间倒序 */
function sortTags(tags: ImageTag[]): ImageTag[] {
  return [...tags].sort((a, b) => {
    const aLatest = a.name === "latest" ? 1 : 0;
    const bLatest = b.name === "latest" ? 1 : 0;
    if (aLatest !== bLatest) return bLatest - aLatest;
    return (
      new Date(b.last_updated || 0).getTime() -
      new Date(a.last_updated || 0).getTime()
    );
  });
}

export function TagSelectDialog({
  open,
  imageName,
  onClose,
  onConfirm,
}: TagSelectDialogProps) {
  const [manualTag, setManualTag] = useState<string | null>(null);
  const [searchQuery, setSearchQuery] = useState("");
  const { data: rawTags, isLoading } = useImageTags(imageName);

  const tags = useMemo(() => sortTags(rawTags ?? []), [rawTags]);

  // 默认选中 latest，否则选中第一个可用标签
  const defaultTag = useMemo(() => {
    if (tags.some((t) => t.name === "latest")) return "latest";
    return tags[0]?.name ?? "latest";
  }, [tags]);

  // 若用户手动选择后标签不再存在（极少），回退到默认标签
  const selectedTag = tags.find((t) => t.name === manualTag)
    ? manualTag!
    : defaultTag;

  // 按名称过滤标签
  const filteredTags = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    if (!q) return tags;
    return tags.filter((t) => t.name.toLowerCase().includes(q));
  }, [tags, searchQuery]);

  const handleConfirm = () => {
    onConfirm(`${imageName}:${selectedTag}`);
  };

  const selectedTagMeta = tags.find((t) => t.name === selectedTag);

  return (
    <Dialog open={open} onOpenChange={(v) => !v && onClose()}>
      <DialogContent className="sm:max-w-lg" showCloseButton={false}>
        <DialogHeader className="flex-col items-start gap-3">
          <div className="flex items-center justify-between w-full gap-4">
            <DialogTitle className="text-base flex items-center gap-2 shrink-0">
              <Tag className="h-4 w-4 text-primary shrink-0" />
              选择镜像标签
            </DialogTitle>
            <Button
              variant="ghost"
              size="icon-sm"
              className="h-7 w-7 shrink-0"
              onClick={onClose}
            >
              <X className="h-4 w-4" />
            </Button>
          </div>

          <div className="flex items-center justify-between w-full gap-3">
            <div
              className="min-w-0 flex-1 text-sm text-muted-foreground truncate"
              title={imageName}
            >
              {imageName}
            </div>

            <div className="relative w-40 shrink-0">
              <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-3.5 w-3.5 text-muted-foreground/60 pointer-events-none" />
              <Input
                placeholder="搜索标签…"
                value={searchQuery}
                onChange={(e) => setSearchQuery(e.target.value)}
                className="pl-8 pr-7 h-7 w-full rounded-lg border-0 bg-muted/60 text-xs shadow-none placeholder:text-muted-foreground/60 focus-visible:bg-muted focus-visible:ring-1 focus-visible:ring-primary/30"
              />
              {searchQuery && (
                <button
                  onClick={() => setSearchQuery("")}
                  className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground/60 hover:text-foreground transition-colors"
                >
                  <X className="h-3.5 w-3.5" />
                </button>
              )}
            </div>
          </div>
        </DialogHeader>

        <div className="space-y-3 py-1">
          {isLoading ? (
            <div className="flex flex-col items-center justify-center py-10 gap-2">
              <Loader2 className="h-5 w-5 animate-spin text-primary" />
              <span className="text-xs text-muted-foreground">正在加载标签…</span>
            </div>
          ) : tags.length > 0 ? (
            <div
              className="space-y-1.5 max-h-[300px] overflow-y-auto scrollbar-thin"
              style={{ scrollbarGutter: "stable" }}
            >
              {filteredTags.map((tag) => {
                const selected = selectedTag === tag.name;
                const hasMeta = !!tag.last_updated || tag.size > 0;

                return (
                  <button
                    key={tag.name}
                    onClick={() => setManualTag(tag.name)}
                    className={`w-full flex items-center gap-3 rounded-lg border px-3 py-2.5 text-left transition-all ${
                      selected
                        ? "bg-primary/5 border-primary/40 shadow-sm"
                        : "bg-card hover:bg-muted/50 border-border"
                    }`}
                    title={`${tag.name}${
                      tag.last_updated ? ` · ${formatDate(tag.last_updated)}` : ""
                    }`}
                  >
                    {/* 选中指示器 */}
                    <div
                      className={`shrink-0 flex h-5 w-5 items-center justify-center rounded-full border transition-colors ${
                        selected
                          ? "border-primary bg-primary text-primary-foreground"
                          : "border-muted-foreground/30"
                      }`}
                    >
                      {selected && <Check className="h-3 w-3" />}
                    </div>

                    {/* 标签信息 */}
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2 min-w-0">
                        <span
                          className={`text-sm font-medium truncate min-w-0 block ${
                            selected ? "text-primary" : "text-foreground"
                          }`}
                        >
                          {tag.name}
                        </span>
                        {tag.name === "latest" && (
                          <Badge
                            variant="outline"
                            className="text-[10px] h-4 px-1 bg-primary/10 text-primary border-primary/20 font-normal shrink-0"
                          >
                            最新
                          </Badge>
                        )}
                      </div>

                      {hasMeta && (
                        <div className="flex items-center gap-3 mt-1 text-[11px] text-muted-foreground">
                          {tag.size > 0 && (
                            <span
                              className="flex items-center gap-1"
                              title={`大小：${formatBytes(tag.size)}`}
                            >
                              <HardDrive className="h-3 w-3" />
                              {formatBytes(tag.size)}
                            </span>
                          )}
                          {tag.last_updated && (
                            <span
                              className="flex items-center gap-1"
                              title={`更新时间：${formatDate(tag.last_updated)}`}
                            >
                              <Clock className="h-3 w-3" />
                              {formatRelativeTime(tag.last_updated)}
                            </span>
                          )}
                        </div>
                      )}
                    </div>
                  </button>
                );
              })}
              {filteredTags.length === 0 && (
                <div className="flex flex-col items-center justify-center py-6 text-muted-foreground">
                  <p className="text-xs">
                    未找到匹配 &quot;{searchQuery}&quot; 的标签
                  </p>
                </div>
              )}
            </div>
          ) : (
            <div className="flex flex-col items-center justify-center py-10 text-muted-foreground">
              <Tag className="h-8 w-8 mb-2 opacity-40" />
              <p className="text-sm">未找到可用标签</p>
              <p className="text-xs mt-0.5">将默认使用 latest 标签拉取</p>
            </div>
          )}
        </div>

        <div className="flex items-center justify-between pt-2 border-t gap-4">
          <div className="min-w-0 text-xs text-muted-foreground truncate">
            {!isLoading && tags.length > 0 ? (
              <span className="truncate block">
                共 <span className="font-medium text-foreground">{tags.length}</span> 个标签
                {selectedTagMeta && (
                  <span className="ml-2">
                    · 已选
                    <span
                      className="font-medium text-foreground mx-1 truncate inline-block align-bottom max-w-[160px]"
                      title={selectedTagMeta.name}
                    >
                      {selectedTagMeta.name}
                    </span>
                    {selectedTagMeta.size > 0 && `(${formatBytes(selectedTagMeta.size)})`}
                  </span>
                )}
              </span>
            ) : null}
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <Button variant="outline" size="sm" onClick={onClose}>
              取消
            </Button>
            <Button size="sm" onClick={handleConfirm} disabled={isLoading}>
              <Download className="h-3.5 w-3.5 mr-1.5" />
              拉取
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
