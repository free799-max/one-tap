/**
 * Compose 项目日志弹窗（实时 SSE 版）
 *
 * 通过 SSE 实时流式展示 docker compose logs 输出，支持跟随滚动、暂停、清空、复制。
 */

import { useState, useRef, useEffect } from "react";
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
import { Badge } from "@/components/ui/badge";
import { useToast } from "@/components/ui/toast";
import { useComposeLogsStream, useComposeStatus } from "@/hooks/useCompose";

/** 根据 Compose 状态返回 Badge 样式 */
function statusVariant(status: string) {
  switch (status) {
    case "running":
      return "default";
    case "exited":
    case "stopped":
      return "secondary";
    default:
      return "outline";
  }
}

export function StackLogsDialog({
  projectId,
  projectName,
  open,
  onOpenChange,
}: {
  projectId: number | null;
  projectName: string | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const toast = useToast();
  const [tail, setTail] = useState(100);
  const [follow, setFollow] = useState(true);
  const { logs, connected, clearLogs } = useComposeLogsStream(
    open ? projectId : null,
    tail,
    (message) => {
      toast.error(message);
      onOpenChange(false);
    }
  );
  const { data: status } = useComposeStatus(projectId);
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
    setTail(Number.isNaN(value) ? 100 : Math.max(1, Math.min(10000, value)));
    clearLogs();
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="max-w-[85vw] sm:max-w-[85vw] max-h-[85vh] overflow-hidden flex flex-col gap-0 p-0 [&_[data-slot='dialog-close']]:top-3">
        <DialogHeader className="flex-row flex-wrap items-center gap-x-4 gap-y-2 px-6 py-3 pr-14 border-b shrink-0">
          <DialogTitle className="text-lg font-semibold flex items-center gap-2 flex-wrap">
            <FileText className="h-5 w-5 text-muted-foreground" />
            编排日志
            {projectName && (
              <span className="text-base font-medium text-foreground">
                {projectName}
              </span>
            )}
            {status?.status && (
              <Badge variant={statusVariant(status.status)} className="text-xs">
                {status.status}
              </Badge>
            )}
          </DialogTitle>

          <div className="ml-auto flex flex-wrap items-center gap-1.5">
            <Button
              size="sm"
              variant={follow ? "default" : "outline"}
              className="h-7 text-xs"
              onClick={() => setFollow((v) => !v)}
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
              onClick={clearLogs}
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
                <div key={`${projectId}-${index}`} className="flex">
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
