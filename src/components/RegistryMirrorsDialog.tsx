/**
 * 镜像加速维护弹窗
 *
 * 地址支持从配套管理端获取，也可在本地手动调整；
 * 弹窗内可直接检测各地址的连通性与响应耗时。
 */

import { useEffect, useRef, useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useToast } from "@/components/ui/toast";
import {
  useRemoteDockerMirrors,
  useTestDockerMirrors,
  useUpdateSystemConfig,
} from "@/hooks/useSettings";
import type { ApiError } from "@/api/types";
import type { DockerMirrorTestItem } from "@/api/settings";
import { cn } from "@/lib/utils";
import { Copy, Download, Gauge, Loader2, Minus, Plus } from "lucide-react";

const MAX_DOCKER_REGISTRY_MIRRORS = 20;

interface RegistryMirrorsDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  mirrors: string[];
  onSaved: (mirrors: string[]) => void;
}

/** 去空白、去尾部斜杠、去重并校验协议 */
function normalizeMirrors(items: string[]): {
  urls: string[];
  error?: string;
} {
  const urls = items.map((url) => url.trim().replace(/\/+$/, "")).filter(Boolean);
  const invalid = urls.find((url) => !/^https?:\/\//i.test(url));
  if (invalid) {
    return {
      urls,
      error: `镜像加速地址必须以 http:// 或 https:// 开头：${invalid}`,
    };
  }
  if (urls.length > MAX_DOCKER_REGISTRY_MIRRORS) {
    return {
      urls,
      error: `镜像加速地址最多 ${MAX_DOCKER_REGISTRY_MIRRORS} 个`,
    };
  }
  return { urls: [...new Set(urls)] };
}

/** 复制文本；局域网 HTTP 访问时 Clipboard API 不可用，回退到 execCommand */
async function copyText(text: string): Promise<boolean> {
  if (window.isSecureContext && navigator.clipboard) {
    try {
      await navigator.clipboard.writeText(text);
      return true;
    } catch {
      // 继续尝试回退方案
    }
  }

  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.select();
  let ok = false;
  try {
    ok = document.execCommand("copy");
  } catch {
    ok = false;
  }
  document.body.removeChild(textarea);
  return ok;
}

const RESULT_COLUMN_CLASS = "w-28 shrink-0";

function ResultBadge({
  item,
  pending,
}: {
  item?: DockerMirrorTestItem;
  pending: boolean;
}) {
  if (pending) {
    return (
      <span
        className={cn(
          RESULT_COLUMN_CLASS,
          "inline-flex items-center justify-end gap-1 text-[11px] text-muted-foreground"
        )}
      >
        <Loader2 className="h-3 w-3 animate-spin" />
        检测中
      </span>
    );
  }
  if (!item) return null;
  return (
    <span
      className={cn(
        RESULT_COLUMN_CLASS,
        "inline-flex items-center justify-end"
      )}
    >
      <span
        title={item.error ?? `HTTP ${item.status_code ?? "-"}`}
        className={cn(
          "inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2 py-0.5 text-[11px] tabular-nums",
          item.ok
            ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400"
            : "border-destructive/30 bg-destructive/10 text-destructive"
        )}
      >
        <span
          className={cn(
            "h-1.5 w-1.5 shrink-0 rounded-full",
            item.ok ? "bg-emerald-500" : "bg-destructive"
          )}
        />
        {item.ok ? `正常 · ${item.latency_ms} ms` : "失败"}
      </span>
    </span>
  );
}

export function RegistryMirrorsDialog({
  open,
  onOpenChange,
  mirrors,
  onSaved,
}: RegistryMirrorsDialogProps) {
  const toast = useToast();
  const remote = useRemoteDockerMirrors();
  const test = useTestDockerMirrors();
  const updateConfig = useUpdateSystemConfig();

  const [items, setItems] = useState<string[]>([]);
  const [results, setResults] = useState<Record<string, DockerMirrorTestItem>>(
    {}
  );

  // 外部配置可能在弹窗打开期间刷新，只在打开瞬间取一次快照
  const mirrorsRef = useRef(mirrors);
  useEffect(() => {
    mirrorsRef.current = mirrors;
  }, [mirrors]);

  useEffect(() => {
    if (!open) return;
    const source = mirrorsRef.current;
    setItems(source.length ? [...source] : [""]);
    setResults({});
  }, [open]);

  const addItem = () => {
    if (items.length >= MAX_DOCKER_REGISTRY_MIRRORS) {
      toast.error(`镜像加速地址最多 ${MAX_DOCKER_REGISTRY_MIRRORS} 个`);
      return;
    }
    setItems((prev) => [...prev, ""]);
  };

  const updateItem = (index: number, value: string) => {
    setItems((prev) => prev.map((item, i) => (i === index ? value : item)));
  };

  const removeItem = (index: number) => {
    setItems((prev) => prev.filter((_, i) => i !== index));
  };

  const loadRemote = () => {
    remote.mutate(undefined, {
      onSuccess: (data) => {
        if (!data.configured) {
          toast.error("未配置管理端地址，无法获取镜像加速地址");
          return;
        }
        if (data.mirrors.length === 0) {
          toast.error("管理端暂无启用的镜像加速地址");
          return;
        }
        setItems(data.mirrors.map((mirror) => mirror.url));
        setResults({});
        toast.success(`已从管理端获取 ${data.mirrors.length} 个地址`);
      },
      onError: (error) => {
        toast.error((error as ApiError).displayMessage || "获取失败");
      },
    });
  };

  const runTest = () => {
    const { urls, error } = normalizeMirrors(items);
    if (error) {
      toast.error(error);
      return;
    }
    if (urls.length === 0) {
      toast.error("请先填写镜像加速地址");
      return;
    }

    test.mutate(urls, {
      onSuccess: (data) => {
        const next: Record<string, DockerMirrorTestItem> = {};
        data.results.forEach((item) => {
          next[item.url] = item;
        });
        setResults(next);
        toast.success(`检测完成：${data.ok_count}/${data.total} 个地址可用`);
      },
      onError: (error) => {
        toast.error((error as ApiError).displayMessage || "检测失败");
      },
    });
  };

  const copyDaemonConfig = async () => {
    const { urls, error } = normalizeMirrors(items);
    if (error) {
      toast.error(error);
      return;
    }
    if (urls.length === 0) {
      toast.error("请先填写镜像加速地址");
      return;
    }

    const config = JSON.stringify({ "registry-mirrors": urls }, null, 2);
    if (await copyText(config)) {
      toast.success("已复制 daemon.json 配置");
    } else {
      toast.error("复制失败，请检查浏览器剪贴板权限");
    }
  };

  const handleSave = () => {
    const { urls, error } = normalizeMirrors(items);
    if (error) {
      toast.error(error);
      return;
    }

    updateConfig.mutate(
      { docker_registry_mirrors: urls },
      {
        onSuccess: (data) => {
          onSaved(data.docker_registry_mirrors ?? urls);
          onOpenChange(false);
        },
      }
    );
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl max-h-[85vh] flex flex-col gap-0 p-0 overflow-hidden">
        <DialogHeader className="shrink-0 border-b px-5 pb-3 pt-5">
          <DialogTitle>镜像加速</DialogTitle>
          <DialogDescription>
            地址可由配套管理端统一维护，也可在此手动调整；顺序即优先级。检测会请求每个地址，
            返回 5xx 以下响应即视为可用。
          </DialogDescription>
        </DialogHeader>

        <div className="min-h-0 flex-1 space-y-2 overflow-y-auto px-5 py-4">
          {items.length === 0 && (
            <p className="text-sm text-muted-foreground">暂无镜像加速地址</p>
          )}
          {items.map((url, index) => {
            const key = url.trim().replace(/\/+$/, "");
            return (
              <div key={index} className="flex items-center gap-2">
                <span className="w-6 shrink-0 text-xs text-muted-foreground">
                  {index + 1}.
                </span>
                <Input
                  value={url}
                  onChange={(e) => updateItem(index, e.target.value)}
                  placeholder="https://docker.m.daocloud.io"
                  className="h-8 flex-1 rounded-md border-input bg-white px-2 text-sm shadow-none"
                />
                <Button
                  type="button"
                  variant="ghost"
                  size="icon-sm"
                  className="shrink-0"
                  onClick={() => removeItem(index)}
                  aria-label="删除镜像加速地址"
                >
                  <Minus className="h-4 w-4" />
                </Button>
                {(results[key] || (test.isPending && Boolean(key))) && (
                  <ResultBadge
                    item={results[key]}
                    pending={test.isPending && Boolean(key)}
                  />
                )}
              </div>
            );
          })}

          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={addItem}
            disabled={items.length >= MAX_DOCKER_REGISTRY_MIRRORS}
          >
            <Plus className="h-4 w-4" />
            新增地址
          </Button>
        </div>

        <div className="flex shrink-0 flex-col-reverse gap-2 border-t bg-muted/50 px-5 py-3 sm:flex-row sm:items-center sm:justify-end">
          <div className="flex flex-1 flex-wrap gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={loadRemote}
              disabled={remote.isPending}
            >
              {remote.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Download className="h-4 w-4" />
              )}
              从管理端获取
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={runTest}
              disabled={test.isPending}
            >
              {test.isPending ? (
                <Loader2 className="h-4 w-4 animate-spin" />
              ) : (
                <Gauge className="h-4 w-4" />
              )}
              检测
            </Button>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={copyDaemonConfig}
              title="复制为 daemon.json 的 registry-mirrors 配置"
            >
              <Copy className="h-4 w-4" />
              复制 daemon.json
            </Button>
          </div>
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => onOpenChange(false)}
            disabled={updateConfig.isPending}
          >
            取消
          </Button>
          <Button
            type="button"
            size="sm"
            onClick={handleSave}
            disabled={updateConfig.isPending}
          >
            {updateConfig.isPending ? "保存中..." : "保存"}
          </Button>
        </div>
      </DialogContent>
    </Dialog>
  );
}
