/**
 * 镜像加速维护弹窗
 *
 * 免费地址从配套管理端获取，授权设备的专属域名也会在此展示；
 * 拉取镜像时自动按源站选择加速域名并改回原 tag，无需修改 daemon.json。
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
import type {
  DockerMirrorTestItem,
  RemoteDockerMirror,
  RemoteDockerMirrors,
} from "@/api/settings";
import { cn, formatDate } from "@/lib/utils";
import { Download, Gauge, Loader2, Minus, Plus } from "lucide-react";

const MAX_DOCKER_REGISTRY_MIRRORS = 20;

const SOURCE_LABELS: Record<string, string> = {
  "docker.io": "Docker Hub",
  "ghcr.io": "GitHub",
  "lscr.io": "LinuxServer",
  "docker.n8n.io": "n8n",
};

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
  const [leased, setLeased] = useState<RemoteDockerMirror[]>([]);
  const [licensed, setLicensed] = useState(false);
  const [results, setResults] = useState<Record<string, DockerMirrorTestItem>>(
    {}
  );

  // 外部配置可能在弹窗打开期间刷新，只在打开瞬间取一次快照
  const mirrorsRef = useRef(mirrors);
  useEffect(() => {
    mirrorsRef.current = mirrors;
  }, [mirrors]);

  const applyRemote = (data: RemoteDockerMirrors, notify: boolean) => {
    const free = data.mirrors.filter((mirror) => mirror.tier !== "subscription");
    const dedicated = data.mirrors.filter(
      (mirror) => mirror.tier === "subscription"
    );
    setLicensed(Boolean(data.licensed));
    setLeased(dedicated);
    if (notify && free.length) {
      setItems(free.map((mirror) => mirror.url));
    }
    if (!notify) return;
    if (!data.configured) {
      toast.error("未配置管理端地址，无法获取镜像加速地址");
      return;
    }
    if (free.length === 0 && dedicated.length === 0) {
      toast.error("管理端暂无启用的镜像加速地址");
      return;
    }
    toast.success(
      `已获取 ${free.length} 个免费地址` +
        (dedicated.length ? `、${dedicated.length} 个专属域名` : "")
    );
  };

  useEffect(() => {
    if (!open) return;
    const source = mirrorsRef.current;
    setItems(source.length ? [...source] : [""]);
    setResults({});
    setLeased([]);
    setLicensed(false);
    remote.mutate(undefined, {
      onSuccess: (data) => applyRemote(data, false),
    });
    // 打开弹窗时自动拉一次，避免用户漏掉专属域名
    // eslint-disable-next-line react-hooks/exhaustive-deps
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
      onSuccess: (data) => applyRemote(data, true),
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
            拉取镜像时自动按源站选择加速域名下载，成功后改回原始 tag，
            无需修改 daemon.json。免费地址由管理端统一下发，授权设备的
            专属域名会在下方单独显示。
          </DialogDescription>
        </DialogHeader>

        <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-5 py-4">
          {(leased.length > 0 || licensed) && (
            <div className="space-y-1.5 rounded-md border border-emerald-500/30 bg-emerald-500/5 px-3 py-2">
              <p className="text-xs font-medium text-emerald-700 dark:text-emerald-400">
                授权专属加速域名（自动用于镜像下载）
              </p>
              {leased.length === 0 && (
                <p className="text-xs text-muted-foreground">
                  本机已激活，但管理端暂无可用专属域名
                </p>
              )}
              {leased.map((mirror) => (
                <div
                  key={mirror.url}
                  className="flex flex-wrap items-center gap-2 text-xs"
                >
                  <span className="rounded-full border px-2 py-0.5 text-[11px]">
                    {SOURCE_LABELS[mirror.source] ?? mirror.source}
                  </span>
                  <span className="break-all font-mono">{mirror.url}</span>
                  <span className="text-muted-foreground">
                    {mirror.expires_at
                      ? `到期 ${formatDate(mirror.expires_at)}`
                      : "长期"}
                  </span>
                </div>
              ))}
            </div>
          )}

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
