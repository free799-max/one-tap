/**
 * Docker 容器管理页面组件（重构版）
 *
 * 按镜像管理样式重构，提供：
 * - 容器列表表格（搜索、Shift 多选、批量操作）
 * - 启动、停止、重启、创建容器
 * - 查看容器详情
 * - 流式日志查看
 * - 容器内执行命令
 */

import { useState } from "react";
import { useDockerStatus } from "@/hooks/useDocker";
import { ContainerListSection } from "./ContainerListSection";
import { ContainerDetailDialog } from "./ContainerDetailDialog";
import { ContainerLogsDialog } from "./ContainerLogsDialog";
import { ContainerTerminalDialog } from "./ContainerTerminalDialog";
import { ContainerCreateDialog } from "./ContainerCreateDialog";

export function DockerPage() {
  const { data: dockerStatus } = useDockerStatus();

  const [detailId, setDetailId] = useState<string | null>(null);
  const [logsId, setLogsId] = useState<string | null>(null);
  const [terminalId, setTerminalId] = useState<string | null>(null);
  const [terminalName, setTerminalName] = useState<string | null>(null);
  const [showCreateDialog, setShowCreateDialog] = useState(false);
  const [showDetailDialog, setShowDetailDialog] = useState(false);
  const [showLogsDialog, setShowLogsDialog] = useState(false);
  const [showTerminalDialog, setShowTerminalDialog] = useState(false);

  const openDetail = (id: string) => {
    setDetailId(id);
    setShowDetailDialog(true);
  };

  const openLogs = (id: string) => {
    setLogsId(id);
    setShowLogsDialog(true);
  };

  const openTerminal = (id: string, name: string) => {
    setTerminalId(id);
    setTerminalName(name);
    setShowTerminalDialog(true);
  };

  if (dockerStatus && !dockerStatus.available) {
    return (
      <div>
        <p className="text-muted-foreground">Docker 不可用或未连接。</p>
      </div>
    );
  }

  return (
    <div className="space-y-6">
      <ContainerListSection
        onOpenDetail={openDetail}
        onOpenLogs={openLogs}
        onOpenTerminal={openTerminal}
        onOpenCreate={() => setShowCreateDialog(true)}
      />

      <ContainerDetailDialog
        open={showDetailDialog}
        onOpenChange={(open) => {
          setShowDetailDialog(open);
          if (!open) setDetailId(null);
        }}
        containerId={detailId}
      />

      <ContainerLogsDialog
        open={showLogsDialog}
        onOpenChange={(open) => {
          setShowLogsDialog(open);
          if (!open) setLogsId(null);
        }}
        containerId={logsId}
      />

      <ContainerTerminalDialog
        open={showTerminalDialog}
        onOpenChange={(open) => {
          setShowTerminalDialog(open);
          if (!open) {
            setTerminalId(null);
            setTerminalName(null);
          }
        }}
        containerId={terminalId}
        containerName={terminalName}
      />

      <ContainerCreateDialog
        open={showCreateDialog}
        onOpenChange={setShowCreateDialog}
      />
    </div>
  );
}
