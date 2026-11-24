/**
 * 容器日志弹窗组件
 *
 * 通过 SSE 实时流式展示容器日志，支持跟随滚动、暂停、清空、复制。
 * 首条 SSE 消息携带容器元数据，用于展示运行状态；获取失败时直接 toast 报错并关闭弹窗。
 */

import { useMemo, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { useToast } from "@/components/ui/toast";
import { useContainerLogsStream, useContainers } from "@/hooks/useDocker";
import { LogStreamDialog } from "../shared/LogStreamDialog";

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

  const handleTailChange = (value: number) => {
    setTail(value);
    clearLogs();
  };

  return (
    <LogStreamDialog
      open={open}
      onOpenChange={onOpenChange}
      title="容器日志"
      name={displayName}
      badge={
        meta?.status ? (
          <Badge variant={statusVariant(meta.status)} className="text-xs">
            {meta.state}
          </Badge>
        ) : null
      }
      logs={logs}
      connected={connected}
      follow={follow}
      onFollowChange={setFollow}
      tail={tail}
      onTailChange={handleTailChange}
      onClear={clearLogs}
      lineKeyPrefix={meta?.container_id ?? containerId}
    />
  );
}
