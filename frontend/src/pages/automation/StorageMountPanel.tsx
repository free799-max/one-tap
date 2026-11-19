/**
 * 存储挂载面板（巡影 Mediary Scout）
 *
 * 凭据都在巡影里：这里列出巡影已连接的网盘，一键把同一份凭据挂到
 * 同组的 OpenList，并展示实际挂载的目录与根目录下的条目。
 */

import { useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  Cloud,
  FolderOpen,
  Loader2,
  RefreshCw,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  useAppStorageMounts,
  useMountAppStorage,
  type StorageMountSource,
} from "@/hooks/useOrchestrations";
import { cn } from "@/lib/utils";

const PROVIDER_STYLE: Record<string, string> = {
  quark: "bg-indigo-500/10 text-indigo-500",
  uc: "bg-cyan-500/10 text-cyan-600",
  pan115: "bg-blue-500/10 text-blue-500",
  guangya: "bg-orange-500/10 text-orange-500",
  tianyi: "bg-rose-500/10 text-rose-500",
  pan123: "bg-emerald-500/10 text-emerald-500",
};

function StatusBadge({ source }: { source: StorageMountSource }) {
  if (source.status !== "active") {
    return (
      <Badge
        variant="outline"
        className="border-amber-500/30 text-amber-600 dark:text-amber-400"
      >
        已失效
      </Badge>
    );
  }
  return (
    <Badge
      variant="outline"
      className="border-emerald-500/30 text-emerald-600 dark:text-emerald-400"
    >
      可用
    </Badge>
  );
}

export function StorageMountPanel({
  instanceId,
  appName,
}: {
  instanceId: number;
  appName: string;
}) {
  const { data, isLoading, isFetching, refetch } = useAppStorageMounts(
    instanceId,
    appName
  );
  const mount = useMountAppStorage();
  const [pendingId, setPendingId] = useState<string | null>(null);

  const handleMount = (source: StorageMountSource) => {
    setPendingId(source.id);
    mount.mutate(
      { instanceId, appName, sourceId: source.id },
      { onSettled: () => setPendingId(null) }
    );
  };

  const sources = data?.sources ?? [];
  const error = data?.error;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <h3 className="text-sm font-medium">存储挂载</h3>
          <p className="text-xs text-muted-foreground">
            巡影已连接的网盘可以一键挂到同组的 OpenList，直接复用巡影里的登录凭据。
            挂载点指向巡影的媒体库根目录，下面会列出实际目录内容与 WebDAV 地址。
          </p>
        </div>
        <Button
          size="sm"
          variant="outline"
          className="h-8 gap-1.5"
          onClick={() => refetch()}
          disabled={isFetching}
        >
          <RefreshCw
            className={cn("h-3.5 w-3.5", isFetching && "animate-spin")}
          />
          刷新
        </Button>
      </div>

      {error ? (
        <div className="flex items-start gap-2 rounded-lg border border-amber-500/30 bg-amber-500/5 p-3 text-xs text-amber-700 dark:text-amber-400">
          <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{error}</span>
        </div>
      ) : null}

      {isLoading ? (
        <div className="flex items-center gap-2 rounded-lg border border-dashed p-6 text-sm text-muted-foreground">
          <Loader2 className="h-4 w-4 animate-spin" />
          正在读取巡影的网盘…
        </div>
      ) : sources.length === 0 ? (
        <div className="rounded-lg border border-dashed p-6 text-sm text-muted-foreground">
          巡影里还没有连接网盘。先在巡影「设置 → 网盘」里连接网盘，再回到这里挂载。
        </div>
      ) : (
        <div className="space-y-3">
          {sources.map((source) => (
            <div
              key={source.id}
              className="rounded-xl border border-border/70 p-4"
            >
              <div className="flex flex-wrap items-center gap-3">
                <div
                  className={cn(
                    "flex h-9 w-9 shrink-0 items-center justify-center rounded-lg",
                    PROVIDER_STYLE[source.provider] ??
                      "bg-primary/10 text-primary"
                  )}
                >
                  <Cloud className="h-4.5 w-4.5" />
                </div>
                <div className="min-w-0 flex-1">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="truncate text-sm font-medium">
                      {source.provider_label}
                    </span>
                    <StatusBadge source={source} />
                    {source.mounted ? (
                      <span className="inline-flex items-center gap-1 text-xs text-emerald-600 dark:text-emerald-400">
                        <CheckCircle2 className="h-3.5 w-3.5" />
                        已挂载
                      </span>
                    ) : (
                      <span className="text-xs text-muted-foreground">
                        未挂载
                      </span>
                    )}
                  </div>
                  <div className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-xs text-muted-foreground">
                    <span className="font-mono">{source.account}</span>
                    {source.driver ? <span>驱动 {source.driver}</span> : null}
                    {source.mounted && source.storage_id ? (
                      <span>存储 #{source.storage_id}</span>
                    ) : null}
                  </div>
                </div>
                <Button
                  size="sm"
                  className="h-8 shrink-0 gap-1.5"
                  variant={source.mounted ? "outline" : "default"}
                  disabled={!source.supported || pendingId === source.id}
                  onClick={() => handleMount(source)}
                  title={
                    source.supported
                      ? undefined
                      : "该网盘暂不支持自动挂载，可参考 OpenList 文档手动添加"
                  }
                >
                  {pendingId === source.id ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : null}
                  {source.supported
                    ? source.mounted
                      ? "重新挂载"
                      : "一键挂载"
                    : "暂不支持"}
                </Button>
              </div>

              <div className="mt-3 grid gap-1.5 border-t pt-3 text-xs">
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                  <span className="text-muted-foreground">挂载点</span>
                  <span className="font-mono">
                    {source.mounted && source.mount_path
                      ? source.mount_path
                      : "挂载后可在 OpenList 中浏览"}
                  </span>
                  <span className="text-muted-foreground">
                    （OpenList 内部路径）
                  </span>
                </div>
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                  <span className="text-muted-foreground">WebDAV</span>
                  <span className="break-all font-mono">
                    {source.webdav_url ?? "未挂载"}
                  </span>
                </div>
                {source.mounted ? (
                  source.contents_error ? (
                    <div className="flex items-center gap-1.5 text-amber-600 dark:text-amber-400">
                      <AlertTriangle className="h-3.5 w-3.5" />
                      目录读取失败：{source.contents_error}
                    </div>
                  ) : (
                    <div className="flex flex-wrap items-center gap-1.5">
                      <FolderOpen className="h-3.5 w-3.5 text-muted-foreground" />
                      {(source.contents ?? []).length === 0 ? (
                        <span className="text-muted-foreground">
                          根目录为空
                        </span>
                      ) : (
                        (source.contents ?? []).map((name) => (
                          <span
                            key={name}
                            className="rounded-md bg-muted px-1.5 py-0.5 font-mono"
                          >
                            {name}
                          </span>
                        ))
                      )}
                    </div>
                  )
                ) : null}
              </div>
            </div>
          ))}
        </div>
      )}

      <p className="text-xs text-muted-foreground">
        凭据只在本次请求里从巡影读取，不会在 OneTap 里另存一份；巡影里重新绑定网盘后，回到这里点「重新挂载」即可同步最新凭据。
      </p>
    </div>
  );
}
