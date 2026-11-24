/**
 * 日志流弹窗通用外壳
 *
 * 容器日志与编排/应用日志共用同一套标题栏、工具栏和正文样式，
 * 避免两处弹窗各自维护后出现视觉差异。
 */

import { useEffect, useRef, useState, type ReactNode } from "react";
import {
  Scroll,
  ScrollText,
  Trash2,
  Copy,
  Check,
  FileText,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";

interface LogStreamDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 标题，如「容器日志」「编排日志」 */
  title: string;
  /** 名称，展示在标题后 */
  name?: string | null;
  /** 状态徽标 */
  badge?: ReactNode;
  logs: string[];
  connected: boolean;
  follow: boolean;
  onFollowChange: (follow: boolean) => void;
  tail: number;
  onTailChange: (tail: number) => void;
  onClear: () => void;
  /** 日志行 key 前缀，切换日志源时强制重建列表 */
  lineKeyPrefix?: string | number | null;
}

export function LogStreamDialog({
  open,
  onOpenChange,
  title,
  name,
  badge,
  logs,
  connected,
  follow,
  onFollowChange,
  tail,
  onTailChange,
  onClear,
  lineKeyPrefix,
}: LogStreamDialogProps) {
  const endRef = useRef<HTMLDivElement>(null);
  const scrollRafRef = useRef<number | null>(null);
  const [copied, setCopied] = useState(false);

  // 平滑跟随滚动：使用 RAF 节流，避免高频日志导致滚动动画堆积
  useEffect(() => {
    if (!follow || !endRef.current) return;
    if (scrollRafRef.current) return;
    scrollRafRef.current = requestAnimationFrame(() => {
      endRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
      scrollRafRef.current = null;
    });
    return () => {
      if (scrollRafRef.current) {
        cancelAnimationFrame(scrollRafRef.current);
        scrollRafRef.current = null;
      }
    };
  }, [logs, follow]);

  const handleCopy = () => {
    navigator.clipboard.writeText(logs.join("\n"));
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };

  const handleTailChange = (e: React.ChangeEvent<HTMLInputElement>) => {
    const value = parseInt(e.target.value, 10);
    onTailChange(Number.isNaN(value) ? 100 : Math.max(1, Math.min(10000, value)));
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-[85vw] sm:max-w-[85vw] max-h-[85vh] overflow-hidden flex flex-col gap-0 p-0 [&_[data-slot='dialog-close']]:top-3">
        <DialogHeader className="flex-row flex-wrap items-center gap-x-4 gap-y-2 px-6 py-3 pr-14 border-b shrink-0">
          <DialogTitle className="text-lg font-semibold flex items-center gap-2 flex-wrap">
            <FileText className="h-5 w-5 text-muted-foreground" />
            {title}
            {name && (
              <span className="text-base font-medium text-foreground">
                {name}
              </span>
            )}
            {badge}
          </DialogTitle>

          <div className="ml-auto flex flex-wrap items-center gap-1.5">
            <Button
              size="sm"
              variant={follow ? "default" : "outline"}
              className="h-7 text-xs"
              onClick={() => onFollowChange(!follow)}
            >
              {follow ? (
                <Scroll className="h-3.5 w-3.5 mr-1" />
              ) : (
                <ScrollText className="h-3.5 w-3.5 mr-1" />
              )}
              {follow ? "跟随滚动" : "已暂停"}
            </Button>
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-xs"
              onClick={onClear}
            >
              <Trash2 className="h-3.5 w-3.5 mr-1" />
              清空
            </Button>
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-xs"
              onClick={handleCopy}
            >
              {copied ? (
                <Check className="h-3.5 w-3.5 mr-1 text-green-500" />
              ) : (
                <Copy className="h-3.5 w-3.5 mr-1" />
              )}
              复制
            </Button>
            <span className="ml-1 text-xs text-muted-foreground">Tail:</span>
            <Input
              type="number"
              min={1}
              max={10000}
              value={tail}
              onChange={handleTailChange}
              className="h-7 w-20 text-xs"
            />
            <span
              className={`ml-1 text-xs ${connected ? "text-green-500" : "text-muted-foreground"}`}
            >
              {connected ? "连接中" : "已断开"}
            </span>
          </div>
        </DialogHeader>

        <div className="flex-1 overflow-auto px-2 py-3 bg-black/95 font-mono text-xs">
          {logs.length === 0 ? (
            <p className="text-muted-foreground text-center py-12">暂无日志</p>
          ) : (
            <div className="space-y-0">
              {logs.map((line, index) => (
                <div key={`${lineKeyPrefix ?? "log"}-${index}`} className="flex">
                  <span className="select-none text-muted-foreground w-10 text-right shrink-0 pr-2 opacity-60">
                    {index + 1}
                  </span>
                  <span className="text-green-400 whitespace-pre-wrap break-all">
                    {line}
                  </span>
                </div>
              ))}
              <div ref={endRef} className="h-0" />
            </div>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
