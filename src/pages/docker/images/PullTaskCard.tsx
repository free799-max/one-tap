/**
 * 镜像拉取任务卡片（纯展示组件）
 *
 * 接收外部进度状态，展示单个拉取任务的进度详情。
 * 支持展开/收起层详情；任务完成后不再自动移除，由调用方控制。
 */

import { useState } from "react";
import { Progress } from "@/components/ui/progress";
import { Badge } from "@/components/ui/badge";
import { formatBytes, formatDate } from "@/lib/utils";
import type { PullProgressEvent } from "@/hooks/useDocker";
import {
  X,
  CheckCircle,
  AlertCircle,
  Loader2,
  ChevronDown,
  ChevronUp,
  Circle,
  ArrowDown,
  Check,
  Clock,
} from "lucide-react";

/** 时间戳转可读字符串 */
function formatTimestamp(ts: number): string {
  return formatDate(new Date(ts).toISOString());
}

/** 单层状态图标 */
function LayerStatusIcon({ status }: { status: string }) {
  if (status === "已完成" || status === "已存在") {
    return <Check className="h-3.5 w-3.5 text-green-500 shrink-0" />;
  }
  if (status === "下载中") {
    return <ArrowDown className="h-3.5 w-3.5 text-blue-500 shrink-0" />;
  }
  if (status === "等待中" || status === "准备下载") {
    return <Clock className="h-3.5 w-3.5 text-muted-foreground shrink-0" />;
  }
  if (status === "验证中" || status === "解压中") {
    return (
      <Loader2 className="h-3.5 w-3.5 text-amber-500 shrink-0 animate-spin" />
    );
  }
  return <Circle className="h-3.5 w-3.5 text-muted-foreground shrink-0" />;
}

/** 单层进度行 */
function LayerProgressRow({
  layer,
}: {
  layer: {
    id: string;
    status_text: string;
    progress_text: string;
    percentage: number;
    speed: number;
  };
}) {
  return (
    <div className="flex flex-col gap-1 py-1.5 border-b border-border/50 last:border-0">
      <div className="flex items-center gap-2">
        <LayerStatusIcon status={layer.status_text} />
        <span className="text-xs font-mono text-muted-foreground shrink-0">
          {layer.id}
        </span>
        <Badge
          variant="outline"
          className="text-[10px] h-4 px-1 shrink-0 font-normal"
        >
          {layer.status_text}
        </Badge>
        <span className="text-[10px] text-muted-foreground ml-auto shrink-0">
          {layer.progress_text}
        </span>
      </div>
      {layer.percentage > 0 && layer.percentage < 100 && (
        <div className="flex items-center gap-2 pl-5">
          <Progress value={layer.percentage} className="flex-1 h-1" />
          <span className="text-[10px] text-muted-foreground w-8 text-right shrink-0">
            {layer.percentage}%
          </span>
        </div>
      )}
      {layer.speed > 0 && (
        <span className="text-[10px] text-muted-foreground pl-5">
          {formatBytes(layer.speed)}/s
        </span>
      )}
    </div>
  );
}

/** 单任务进度卡片 */
export function PullTaskCard({
  image,
  progress,
  finalProgress,
  status,
  error,
  createdAt,
  lastActivityAt,
  onRemove,
  defaultExpanded = true,
}: {
  image: string;
  progress: PullProgressEvent | null;
  finalProgress?: PullProgressEvent | null;
  status: "pulling" | "completed" | "failed" | "cancelled";
  error: string | null;
  createdAt: number;
  lastActivityAt: number;
  onRemove: () => void;
  defaultExpanded?: boolean;
}) {
  const [expanded, setExpanded] = useState(defaultExpanded);

  const isCompleted = status === "completed";
  const isFailed = status === "failed";
  const isCancelled = status === "cancelled";

  // 优先使用实时进度，无实时进度时使用最终快照
  const displayProgress = progress ?? finalProgress ?? null;

  const percentage = displayProgress?.percentage ?? 0;
  const overallStatus = displayProgress?.status ?? "准备中…";
  const speed = displayProgress?.speed ?? 0;
  const sizeText = displayProgress?.size_text ?? "--";

  const statusLabel = isCompleted
    ? "完成"
    : isFailed
    ? "失败"
    : isCancelled
    ? "取消"
    : "拉取中";

  return (
    <div className="flex flex-col gap-2 rounded-lg border bg-card p-3">
      {/* 头部：镜像名 + 状态 + 操作 */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 min-w-0">
          {isCompleted ? (
            <CheckCircle className="h-4 w-4 text-green-500 shrink-0" />
          ) : isFailed ? (
            <AlertCircle className="h-4 w-4 text-red-500 shrink-0" />
          ) : isCancelled ? (
            <Circle className="h-4 w-4 text-muted-foreground shrink-0" />
          ) : (
            <Loader2 className="h-4 w-4 text-primary animate-spin shrink-0" />
          )}
          <span className="text-sm font-medium truncate" title={image}>
            {image}
          </span>
          <Badge
            variant="outline"
            className={`text-[10px] h-4 px-1 shrink-0 ${
              isCompleted
                ? "bg-green-500/10 text-green-600 border-green-500/20"
                : isFailed
                ? "bg-red-500/10 text-red-600 border-red-500/20"
                : isCancelled
                ? "bg-muted text-muted-foreground border-border"
                : "bg-primary/10 text-primary border-primary/20"
            }`}
          >
            {statusLabel}
          </Badge>
        </div>
        <div className="flex items-center gap-1 shrink-0 ml-2">
          <button
            onClick={() => setExpanded((e) => !e)}
            className="text-muted-foreground hover:text-foreground transition-colors p-1"
            title={expanded ? "收起" : "展开"}
          >
            {expanded ? (
              <ChevronUp className="h-3.5 w-3.5" />
            ) : (
              <ChevronDown className="h-3.5 w-3.5" />
            )}
          </button>
          <button
            onClick={onRemove}
            className="text-muted-foreground hover:text-foreground transition-colors p-1"
            title="移除"
          >
            <X className="h-3.5 w-3.5" />
          </button>
        </div>
      </div>

      {/* 时间信息 */}
      <div className="flex items-center gap-3 text-[10px] text-muted-foreground">
        <span>开始：{formatTimestamp(createdAt)}</span>
        <span>更新：{formatTimestamp(lastActivityAt)}</span>
      </div>

      {/* 总体进度 */}
      <div className="flex flex-col gap-1">
        <div className="flex items-center justify-between text-xs">
          <span className="text-muted-foreground">
            {overallStatus}
            {speed > 0 && (
              <span className="ml-2 text-primary">{formatBytes(speed)}/s</span>
            )}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <Progress value={percentage} className="flex-1 h-2" />
          <span className="text-xs text-muted-foreground w-10 text-right shrink-0">
            {percentage}%
          </span>
        </div>
        <div className="flex items-center justify-between text-[11px] text-muted-foreground">
          <span>{sizeText}</span>
        </div>
      </div>

      {/* 层详情（可展开） */}
      {expanded && displayProgress?.layers && displayProgress.layers.length > 0 && (
        <div className="mt-1 rounded-md bg-muted/40 px-2 py-1">
          <p className="text-[10px] text-muted-foreground font-medium mb-1 px-1">
            层详情
          </p>
          <div>
            {displayProgress.layers.map((layer) => (
              <LayerProgressRow
                key={layer.id}
                layer={{
                  id: layer.id,
                  status_text: layer.status_text,
                  progress_text: layer.progress_text,
                  percentage: layer.percentage,
                  speed: layer.speed,
                }}
              />
            ))}
          </div>
        </div>
      )}

      {isFailed && error && (
        <p className="text-xs text-red-500 mt-0.5">{error}</p>
      )}
    </div>
  );
}
