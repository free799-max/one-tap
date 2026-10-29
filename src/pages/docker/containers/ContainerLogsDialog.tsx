/**
 * 容器日志弹窗组件
 *
 * 通过 SSE 实时流式展示容器日志，支持跟随滚动、暂停、清空、复制。
 * 首条 SSE 消息携带容器元数据，用于展示运行状态；获取失败时直接 toast 报错并关闭弹窗。
 */

import { useRef, useEffect, useState, useMemo } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Badge } from "@/components/ui/badge";
import { useToast } from "@/components/ui/toast";
import { useContainerLogsStream, useContainers } from "@/hooks/useDocker";
import {
  Scroll,
  ScrollText,
  Trash2,
  Copy,
  Check,
  FileText,
} from "lucide-react";

interface ContainerLogsDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  containerId: string | null;
}

/** 根据 Docker 状态返回 Badge 样式 */
function statusVariant(status: string) {
  switch (status) {
    case "running":
      return "default";
    case "exited":
      return "secondary";
    case "paused":
      return "outline";
    case "dead":
      return "destructive";
    default:
      return "outline";
  }
}

export function ContainerLogsDialog({
  open,
  onOpenChange,
  containerId,
}: ContainerLogsDialogProps) {
  const toast = useToast();
  const [tail, setTail] = useState(100);
  const [follow, setFollow] = useState(true);
  const { logs, connected, meta, clearLogs } = useContainerLogsStream(
    open ? containerId : null,
    tail,
    (message) => {
      toast.error(message);
      onOpenChange(false);
    }
  );
  const { data: containers = [] } = useContainers();
  const fallbackName = useMemo(() => {
    if (!containerId) return null;
    return containers.find((c) => c.id === containerId)?.name || null;
  }, [containers, containerId]);
  const displayName = meta?.name || fallbackName;
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
      <DialogContent className="max-w-[85vw] sm:max-w-[85vw] max-h-[85vh] overflow-hidden flex flex-col p-0">
        <DialogHeader className="px-6 pt-5 pb-2 border-b shrink-0">
          <DialogTitle className="text-lg font-semibold flex items-center gap-2 flex-wrap">
            <FileText className="h-5 w-5 text-muted-foreground" />
            容器日志
            {displayName && (
              <span className="text-base font-medium text-foreground">
                {displayName}
              </span>
            )}
            {meta?.status && (
              <Badge variant={statusVariant(meta.status)} className="text-xs">
                {meta.state}
              </Badge>
            )}
          </DialogTitle>
        </DialogHeader>

        <div className="flex items-center gap-2 px-6 py-2 border-b shrink-0">
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
          <div className="flex items-center gap-2 ml-auto">
            <span className="text-xs text-muted-foreground">Tail:</span>
            <Input
              type="number"
              min={1}
              max={10000}
              value={tail}
              onChange={handleTailChange}
              className="h-7 w-20 text-xs"
            />
            <span
              className={`text-xs ${connected ? "text-green-500" : "text-muted-foreground"}`}
            >
              {connected ? "连接中" : "已断开"}
            </span>
          </div>
        </div>

        <div className="flex-1 overflow-auto px-2 py-3 bg-black/95 font-mono text-xs">
          {logs.length === 0 ? (
            <p className="text-muted-foreground text-center py-12">暂无日志</p>
          ) : (
            <div className="space-y-0">
              {logs.map((line, index) => (
                <div key={`${meta?.container_id ?? containerId}-${index}`} className="flex">
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
