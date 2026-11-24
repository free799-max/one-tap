/**
 * 编排/应用日志弹窗（实时 SSE 版）
 *
 * 通过 SSE 实时流式展示 docker compose logs（或指定应用的日志流）输出，
 * 支持跟随滚动、暂停、清空、复制。
 */

import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { useToast } from "@/components/ui/toast";
import { useComposeStatus } from "@/hooks/useCompose";
import { useLogsStream, withLogToken } from "@/hooks/useLogsStream";
import { LogStreamDialog } from "../shared/LogStreamDialog";

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
  streamPath,
  title = "编排日志",
  statusText,
}: {
  projectId: number | null;
  projectName: string | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 直接指定日志流地址（不含 tail/token），用于非 Compose 项目来源 */
  streamPath?: string | null;
  title?: string;
  /** 外部传入的状态文案，优先于 Compose 状态徽标 */
  statusText?: string | null;
}) {
  const toast = useToast();
  const [tail, setTail] = useState(100);
  const [follow, setFollow] = useState(true);
  const basePath =
    streamPath ?? (projectId ? `/api/docker/compose/${projectId}/logs/stream` : null);
  const logsUrl =
    open && basePath
      ? withLogToken(
          `${basePath}${basePath.includes("?") ? "&" : "?"}tail=${tail}&follow=true`
        )
      : null;
  const { logs, connected, clearLogs } = useLogsStream(logsUrl, (message) => {
    toast.error(message);
    onOpenChange(false);
  });
  const { data: status } = useComposeStatus(streamPath ? null : projectId);
  const statusLabel = statusText || status?.status;

  const handleTailChange = (value: number) => {
    setTail(value);
    clearLogs();
  };

  return (
    <LogStreamDialog
      open={open}
      onOpenChange={onOpenChange}
      title={title}
      name={projectName}
      badge={
        statusLabel ? (
          <Badge variant={statusVariant(statusLabel)} className="text-xs">
            {statusLabel}
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
      lineKeyPrefix={projectId}
    />
  );
}
