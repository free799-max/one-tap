/**
 * 镜像搜索区域组件（卡片网格布局）
 *
 * 响应式网格展示远程镜像搜索结果，每张卡片包含图标、名称、描述、统计和拉取操作。
 */

import { useState, useMemo } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import {
  useSearchImages,
  usePullImageStream,
  type Registry,
  type ImageSearchResult,
} from "@/hooks/useDocker";
import { useToast } from "@/components/ui/toast";
import { TagSelectDialog } from "./TagSelectDialog";
import { PullProgressMini } from "./PullProgressMini";
import { usePullProgressStore } from "@/stores/pull-progress";
import { formatCount } from "@/lib/utils";
import {
  Search,
  Download,
  Star,
  Shield,
  Loader2,
  Settings,
  CheckCircle,
  ChevronLeft,
  ChevronRight,
  HardDrive,
  ImageOff,
  X,
} from "lucide-react";

interface ImageSearchSectionProps {
  defaultRegistry: Registry | undefined;
  onOpenConfig: () => void;
}

const PAGE_SIZE = 20;

/** 预设图标背景色 — 柔和不刺眼 */
const PALETTE = [
  "#2563eb", // blue
  "#dc2626", // red
  "#16a34a", // green
  "#9333ea", // purple
  "#ea580c", // orange
  "#0891b2", // cyan
  "#db2777", // pink
  "#4f46e5", // indigo
  "#059669", // emerald
  "#b91c1c", // rose
  "#4338ca", // violet
  "#0d9488", // teal
];

/** 根据字符串选稳定的颜色 */
function pickColor(str: string): string {
  let hash = 0;
  for (let i = 0; i < str.length; i++) {
    hash = str.charCodeAt(i) + ((hash << 5) - hash);
  }
  return PALETTE[Math.abs(hash) % PALETTE.length];
}

/** 单张镜像卡片 */
function ImageCard({
  image,
  pulling,
  onPull,
}: {
  image: ImageSearchResult;
  pulling: boolean;
  onPull: (name: string) => void;
}) {
  const iconBg = pickColor(image.name);
  const initial = image.name.charAt(0).toUpperCase();

  return (
    <div className="group flex flex-col rounded-lg border bg-card p-3.5 transition-all hover:shadow-md hover:border-primary/20">
      {/* 上部分：图标 + 名称 + 拉取 */}
      <div className="flex items-start gap-3">
        <div
          className="flex h-10 w-10 shrink-0 items-center justify-center rounded-md text-white text-sm font-bold"
          style={{ backgroundColor: iconBg }}
        >
          {initial}
        </div>

        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-1.5">
            <span className="font-medium text-sm truncate" title={image.name}>
              {image.name}
            </span>
            {image.official && (
              <Badge
                variant="outline"
                className="text-[10px] h-4 px-1 bg-primary/10 text-primary border-primary/20 font-normal shrink-0"
              >
                <Shield className="h-2.5 w-2.5 mr-0.5" />
                官方
              </Badge>
            )}
          </div>
          <p className="text-xs text-muted-foreground line-clamp-2 mt-0.5 leading-relaxed">
            {image.description || "暂无描述"}
          </p>
        </div>

        <Button
          size="sm"
          variant="ghost"
          className="shrink-0 h-8 w-8 p-0 text-muted-foreground hover:text-primary"
          onClick={() => onPull(image.name)}
          disabled={pulling}
        >
          {pulling ? (
            <Loader2 className="h-4 w-4 animate-spin" />
          ) : (
            <Download className="h-4 w-4" />
          )}
        </Button>
      </div>

      {/* 下部分：统计 */}
      <div className="mt-2.5 pt-2 border-t flex items-center justify-between text-[11px] text-muted-foreground">
        <div className="flex items-center gap-3">
          <span className="flex items-center gap-1">
            <Star className="h-3 w-3 fill-amber-400 text-amber-400" />
            {formatCount(image.star_count)}
          </span>
          <span className="flex items-center gap-1">
            <HardDrive className="h-3 w-3" />
            {formatCount(image.pull_count)}
          </span>
        </div>
        {image.is_automated && (
          <span className="text-[10px] px-1.5 py-0.5 rounded bg-secondary text-secondary-foreground">
            Automated
          </span>
        )}
      </div>
    </div>
  );
}

/** 骨架屏卡片 */
function SkeletonCard() {
  const c = PALETTE[0];
  return (
    <div className="flex flex-col rounded-lg border bg-card p-3.5">
      <div className="flex items-start gap-3">
        <div className="h-10 w-10 shrink-0 rounded-md animate-pulse" style={{ backgroundColor: c + "33" }} />
        <div className="flex-1 space-y-1.5 pt-0.5">
          <div className="h-3.5 w-2/3 rounded bg-muted animate-pulse" />
          <div className="h-2.5 w-full rounded bg-muted animate-pulse" />
          <div className="h-2.5 w-4/5 rounded bg-muted animate-pulse" />
        </div>
      </div>
      <div className="mt-2.5 pt-2 border-t flex items-center justify-between">
        <div className="flex gap-3">
          <div className="h-2.5 w-10 rounded bg-muted animate-pulse" />
          <div className="h-2.5 w-10 rounded bg-muted animate-pulse" />
        </div>
      </div>
    </div>
  );
}

export function ImageSearchSection({
  defaultRegistry,
  onOpenConfig,
}: ImageSearchSectionProps) {
  const toast = useToast();

  const [searchInput, setSearchInput] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [page, setPage] = useState(1);

  // Tag 选择弹窗状态
  const [showTagDialog, setShowTagDialog] = useState(false);
  const [selectedImage, setSelectedImage] = useState<string>("");

  // 跟踪活跃拉取任务中的镜像（用于禁用按钮），仅当状态为 pulling 时禁用
  const history = usePullProgressStore((s) => s.history);
  const activeImages = useMemo(
    () =>
      new Set(
        history
          .filter((item) => item.status === "pulling")
          .map((t) => t.image)
      ),
    [history]
  );

  const {
    data,
    isLoading,
    isFetching,
    refetch,
  } = useSearchImages(searchQuery, page);
  const pullImageStream = usePullImageStream();

  const results = data?.results ?? [];
  const total = data?.total ?? 0;
  const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));

  const handleSearch = () => {
    if (!searchInput.trim()) {
      toast.error("请输入搜索关键词");
      return;
    }
    const trimmed = searchInput.trim();
    setPage(1);
    if (trimmed === searchQuery) {
      // 关键词未变化时强制重新获取，避免 staleTime 导致点击无效
      refetch();
    } else {
      setSearchQuery(trimmed);
    }
  };

  const clearSearch = () => {
    setSearchInput("");
    setSearchQuery("");
  };

  /** 点击下载按钮 — 打开 tag 选择弹窗 */
  const handlePull = (name: string) => {
    setSelectedImage(name);
    setShowTagDialog(true);
  };

  /** 确认下载 — 启动后台拉取任务 */
  const handleConfirmPull = (imageWithTag: string) => {
    setShowTagDialog(false);
    pullImageStream.mutate(imageWithTag, {
      onSuccess: (data) => {
        toast.success(`已开始拉取 ${imageWithTag}`);
        // 记录到全局拉取进度 store，进度面板会自动显示
        usePullProgressStore.getState().addTask(data.task_id, imageWithTag);
      },
    });
  };

  const skeletons = useMemo(() => Array.from({ length: 6 }, (_, i) => i), []);

  return (
    <Card className="rounded-xl">
      <CardContent className="p-4 space-y-3">
        {/* 搜索行 */}
        <div className="flex items-center gap-2">
          <div className="relative flex-1 max-w-sm">
            <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground/60 pointer-events-none" />
            <Input
              placeholder="搜索 Docker Hub 镜像…"
              value={searchInput}
              onChange={(e) => setSearchInput(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && handleSearch()}
              spellCheck={false}
              className="pl-9 pr-8 h-9 w-full rounded-full border-0 bg-muted/60 text-sm shadow-none placeholder:text-muted-foreground/60 focus-visible:bg-muted focus-visible:ring-1 focus-visible:ring-primary/30"
            />
            {searchInput && (
              <button
                onClick={clearSearch}
                className="absolute right-3 top-1/2 -translate-y-1/2 text-muted-foreground/60 hover:text-foreground transition-colors"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            )}
          </div>
          <Button
            size="sm"
            className="h-[34px] px-3 text-xs"
            onClick={handleSearch}
            disabled={isFetching}
          >
            {isFetching ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Search className="h-3.5 w-3.5" />
            )}
            <span className="ml-1">搜索</span>
          </Button>
          <PullProgressMini />
          <div className="flex items-center gap-2 ml-auto">
            {defaultRegistry && (
              <Badge
                variant="outline"
                className="text-xs bg-primary/5 text-primary border-primary/20 font-normal h-6 shrink-0"
              >
                <CheckCircle className="h-3 w-3 mr-1" />
                {defaultRegistry.name}
              </Badge>
            )}
            <Button
              size="icon"
              variant="ghost"
              className="h-9 w-9 shrink-0"
              onClick={onOpenConfig}
            >
              <Settings className="h-5 w-5" />
            </Button>
          </div>
        </div>

        {/* 搜索结果 */}
        {searchQuery ? (
          <>
            {isLoading ? (
              <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
                {skeletons.map((i) => (
                  <SkeletonCard key={i} />
                ))}
              </div>
            ) : results.length === 0 ? (
              <div className="flex flex-col items-center justify-center py-12 text-muted-foreground">
                <ImageOff className="h-10 w-10 mb-3 opacity-40" />
                <p className="text-sm">
                  未找到与 &quot;{searchQuery}&quot; 相关的镜像
                </p>
              </div>
            ) : (
              <div className="relative">
                {/* 分页加载时内容区遮罩 */}
                {isFetching && (
                  <div className="absolute inset-0 z-10 flex items-center justify-center bg-background/60 backdrop-blur-[1px] rounded-lg">
                    <Loader2 className="h-8 w-8 text-primary animate-spin" />
                  </div>
                )}
                <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
                  {results.map((r) => (
                    <ImageCard
                      key={r.name}
                      image={r}
                      pulling={activeImages.has(r.name)}
                      onPull={handlePull}
                    />
                  ))}
                </div>

                {/* Tag 选择弹窗 */}
                <TagSelectDialog
                  key={selectedImage}
                  open={showTagDialog}
                  imageName={selectedImage}
                  onClose={() => setShowTagDialog(false)}
                  onConfirm={handleConfirmPull}
                />
              </div>
            )}

            {/* 分页 */}
            {total > 0 && (
              <div className="flex items-center justify-between pt-1">
                <span className="text-xs text-muted-foreground">
                  共 {total} 条
                </span>
                <div className="flex items-center gap-1">
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-8 w-8 p-0"
                    onClick={() => setPage((p) => Math.max(1, p - 1))}
                    disabled={page <= 1 || isFetching}
                  >
                    <ChevronLeft className="h-4 w-4" />
                  </Button>
                  <span className="text-sm px-2 min-w-[4rem] text-center">
                    {page} / {totalPages}
                  </span>
                  <Button
                    size="sm"
                    variant="outline"
                    className="h-8 w-8 p-0"
                    onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                    disabled={page >= totalPages || isFetching}
                  >
                    <ChevronRight className="h-4 w-4" />
                  </Button>
                </div>
              </div>
            )}
          </>
        ) : null}
      </CardContent>
    </Card>
  );
}
