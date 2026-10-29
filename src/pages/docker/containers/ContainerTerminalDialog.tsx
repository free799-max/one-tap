/**
 * 容器交互式终端弹窗组件
 *
 * 基于 WebSocket + xterm.js 实现类似 `docker exec -it` 的终端体验。
 */

import { useState, useEffect } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { useContainerTerminal } from "@/hooks/useDocker";
import { Terminal, RotateCcw, Trash2, X, AlertCircle } from "lucide-react";
import "@xterm/xterm/css/xterm.css";

interface ContainerTerminalDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  containerId: string | null;
  containerName: string | null;
}

const SHELL_OPTIONS = [
  { value: "/bin/sh", label: "/bin/sh" },
  { value: "/bin/bash", label: "/bin/bash" },
];

export function ContainerTerminalDialog({
  open,
  onOpenChange,
  containerId,
  containerName,
}: ContainerTerminalDialogProps) {
  const [shell, setShell] = useState("/bin/sh");
  const { terminalRef, connected, error, fitTerminal, focusTerminal, clearTerminal, reconnect } =
    useContainerTerminal(containerId, { shell });

  // 弹窗打开后给布局一点过渡时间再 fit 并聚焦，避免尺寸计算错误
  useEffect(() => {
    if (open) {
      const timer = setTimeout(() => {
        fitTerminal();
        focusTerminal();
      }, 150);
      return () => clearTimeout(timer);
    }
  }, [open, fitTerminal, focusTerminal]);

  const handleOpenChange = (open: boolean) => {
    if (!open) {
      setShell("/bin/sh");
    }
    onOpenChange(open);
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-5xl max-h-[90vh] overflow-hidden flex flex-col p-0 gap-0">
        <DialogHeader className="px-4 py-3 border-b shrink-0">
          <DialogTitle className="text-base font-semibold flex items-center gap-2">
            <Terminal className="h-4 w-4 text-muted-foreground" />
            终端
            {containerName && (
              <span className="text-sm font-medium text-foreground">
                {containerName}
              </span>
            )}
          </DialogTitle>
        </DialogHeader>

        <div className="flex items-center justify-between gap-2 px-4 py-2 border-b shrink-0 bg-muted/30">
          <div className="flex items-center gap-3">
            <select
              value={shell}
              onChange={(e) => setShell(e.target.value)}
              className="h-8 rounded-md border bg-background px-2 text-xs focus:outline-none focus:ring-1 focus:ring-ring"
              disabled={connected}
            >
              {SHELL_OPTIONS.map((opt) => (
                <option key={opt.value} value={opt.value}>
                  {opt.label}
                </option>
              ))}
            </select>
            <div className="flex items-center gap-1.5 text-xs">
              <span
                className={`h-2 w-2 rounded-full ${
                  connected ? "bg-green-500" : "bg-red-500"
                }`}
              />
              <span className="text-muted-foreground">
                {connected ? "已连接" : "未连接"}
              </span>
            </div>
            {error && (
              <div className="flex items-center gap-1 text-xs text-red-500">
                <AlertCircle className="h-3.5 w-3.5" />
                {error}
              </div>
            )}
          </div>

          <div className="flex items-center gap-2">
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-xs"
              onClick={clearTerminal}
            >
              <Trash2 className="h-3.5 w-3.5 mr-1" />
              清空
            </Button>
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-xs"
              onClick={reconnect}
            >
              <RotateCcw className="h-3.5 w-3.5 mr-1" />
              重连
            </Button>
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-xs"
              onClick={() => handleOpenChange(false)}
            >
              <X className="h-3.5 w-3.5 mr-1" />
              关闭
            </Button>
          </div>
        </div>

        <div
          className="flex-1 min-h-[400px] bg-black p-2 overflow-hidden"
          onClick={focusTerminal}
        >
          <div ref={terminalRef} className="h-full w-full" />
        </div>
      </DialogContent>
    </Dialog>
  );
}
