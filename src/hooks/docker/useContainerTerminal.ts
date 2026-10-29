/**
 * Docker 容器交互式终端 Hook
 *
 * 通过 WebSocket 建立类似 `docker exec -it` 的伪终端会话，
 * 返回 terminal 挂载点与连接状态。
 */

import { useState, useEffect, useRef, useCallback } from "react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { getToken } from "@/lib/api";

/**
 * @param containerId 容器 ID，为 null 时不建立连接
 * @param options shell、workdir、user 等选项
 * @returns terminalRef、connected、error、fitTerminal、clearTerminal、reconnect
 */
export function useContainerTerminal(
  containerId: string | null,
  options: {
    shell?: string;
    workdir?: string | null;
    user?: string | null;
  } = {}
) {
  const { shell = "/bin/sh", workdir, user } = options;
  const terminalRef = useRef<HTMLDivElement>(null);
  const terminalInstanceRef = useRef<Terminal | null>(null);
  const fitAddonRef = useRef<FitAddon | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [connectKey, setConnectKey] = useState(0);

  useEffect(() => {
    if (!containerId || !terminalRef.current) return;

    const terminal = new Terminal({
      cursorBlink: true,
      fontSize: 13,
      fontFamily:
        'ui-monospace, SFMono-Regular, "SF Mono", Menlo, Consolas, monospace',
      theme: { background: "#0a0a0a", foreground: "#e5e5e5" },
      convertEol: true,
      scrollback: 10000,
    });
    const fitAddon = new FitAddon();
    terminal.loadAddon(fitAddon);
    terminal.open(terminalRef.current);
    fitAddon.fit();
    terminal.focus();

    terminalInstanceRef.current = terminal;
    fitAddonRef.current = fitAddon;

    const token = getToken() || "";
    const params = new URLSearchParams({ token, shell });
    if (workdir) params.set("workdir", workdir);
    if (user) params.set("user", user);
    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(
      `${protocol}//${window.location.host}/api/docker/containers/${containerId}/exec?${params.toString()}`
    );

    ws.onopen = () => {
      setConnected(true);
      setError(null);
      terminal.focus();
      const dims = fitAddon.proposeDimensions();
      if (dims) {
        ws.send(
          JSON.stringify({
            type: "resize",
            cols: dims.cols,
            rows: dims.rows,
          })
        );
      }
    };

    ws.onmessage = (event) => {
      terminal.write(event.data);
    };

    ws.onclose = () => {
      setConnected(false);
      terminal.write("\r\n\x1b[33m[连接已关闭]\x1b[0m\r\n");
    };

    ws.onerror = () => {
      setError("连接失败");
      setConnected(false);
    };

    const disposeOnData = terminal.onData((data) => {
      if (ws.readyState === WebSocket.OPEN) {
        ws.send(data);
      }
    });

    const handleResize = () => {
      fitAddon.fit();
      const dims = fitAddon.proposeDimensions();
      if (dims && ws.readyState === WebSocket.OPEN) {
        ws.send(
          JSON.stringify({
            type: "resize",
            cols: dims.cols,
            rows: dims.rows,
          })
        );
      }
    };
    window.addEventListener("resize", handleResize);

    wsRef.current = ws;

    return () => {
      disposeOnData.dispose();
      window.removeEventListener("resize", handleResize);
      ws.close();
      terminal.dispose();
      terminalInstanceRef.current = null;
      fitAddonRef.current = null;
      wsRef.current = null;
    };
  }, [containerId, shell, workdir, user, connectKey]);

  const focusTerminal = useCallback(() => {
    terminalInstanceRef.current?.focus();
  }, []);

  const fitTerminal = useCallback(() => {
    fitAddonRef.current?.fit();
    const dims = fitAddonRef.current?.proposeDimensions();
    const ws = wsRef.current;
    if (dims && ws && ws.readyState === WebSocket.OPEN) {
      ws.send(
        JSON.stringify({
          type: "resize",
          cols: dims.cols,
          rows: dims.rows,
        })
      );
    }
  }, []);

  const clearTerminal = useCallback(() => {
    terminalInstanceRef.current?.clear();
  }, []);

  const reconnect = useCallback(() => {
    wsRef.current?.close();
    setConnectKey((k) => k + 1);
  }, []);

  return {
    terminalRef,
    connected,
    error,
    fitTerminal,
    focusTerminal,
    clearTerminal,
    reconnect,
  };
}
