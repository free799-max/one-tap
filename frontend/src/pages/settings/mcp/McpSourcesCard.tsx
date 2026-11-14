import { useState } from "react";
import {
  AlertCircle,
  Boxes,
  ChevronRight,
  Loader2,
  Server,
  Wrench,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Switch } from "@/components/ui/switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  useMcpCatalog,
  useMcpUpstreams,
  useUpdateMcpUpstream,
  type McpUpstream,
} from "@/hooks/useMcp";
import { cn } from "@/lib/utils";

function UpstreamStatus({ upstream }: { upstream: McpUpstream }) {
  return (
    <div>
      <Badge
        variant="outline"
        className={cn(
          "font-normal",
          upstream.available
            ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400"
            : "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-400"
        )}
      >
        {upstream.available ? "可读取" : "不可用"}
      </Badge>
      {!upstream.available && upstream.unavailable_reason && (
        <p
          className="mt-1 break-words text-[11px] leading-relaxed text-muted-foreground"
          title={upstream.unavailable_reason}
        >
          {upstream.unavailable_reason}
        </p>
      )}
    </div>
  );
}

export function McpSourcesCard({
  onBrowseSource,
}: {
  onBrowseSource: (source: string) => void;
}) {
  const upstreamsQuery = useMcpUpstreams();
  const catalogQuery = useMcpCatalog();
  const updateUpstream = useUpdateMcpUpstream();
  const [pendingInstance, setPendingInstance] = useState<number | null>(null);
  const upstreams = upstreamsQuery.data ?? [];
  const sources = catalogQuery.data?.sources ?? [];
  const builtInSources = sources.filter(
    (source) => source.id === "onetap" || source.id === "local"
  );
  const sourceCounts = new Map(sources.map((source) => [source.id, source.count]));

  const toggleUpstream = async (upstream: McpUpstream, enabled: boolean) => {
    if (pendingInstance !== null) return;
    setPendingInstance(upstream.instance_id);
    try {
      await updateUpstream.mutateAsync({
        instanceId: upstream.instance_id,
        data: { enabled, allowed_tools: upstream.allowed_tools },
      });
    } catch {
      // mutation hook 已显示错误，列表继续使用未修改的服务端数据。
    } finally {
      setPendingInstance(null);
    }
  };

  const isLoading = upstreamsQuery.isLoading || catalogQuery.isLoading;
  const hasError = upstreamsQuery.error || catalogQuery.error;

  return (
    <Card className="rounded-xl">
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-base">
          <Server className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
          来源管理
        </CardTitle>
        <CardDescription>
          实例开关控制整个远程来源；单项接口与工具权限请进入目录设置。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-4">
        {isLoading ? (
          <div className="flex items-center justify-center gap-2 py-12 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            正在读取来源与实例状态...
          </div>
        ) : hasError ? (
          <div className="flex flex-col items-center gap-3 rounded-xl border border-destructive/30 bg-destructive/5 py-8 text-center">
            <AlertCircle className="h-5 w-5 text-destructive" aria-hidden="true" />
            <p className="text-sm text-destructive">来源信息加载失败。</p>
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() => {
                void upstreamsQuery.refetch();
                void catalogQuery.refetch();
              }}
            >
              重新加载
            </Button>
          </div>
        ) : (
          <>
            <div className="grid gap-3 md:grid-cols-2">
              {builtInSources.map((source) => (
                <article
                  key={source.id}
                  className="flex min-w-0 items-center gap-3 rounded-xl border bg-muted/20 p-3"
                >
                  <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-background text-muted-foreground shadow-sm">
                    {source.id === "local" ? (
                      <Wrench className="h-4 w-4" aria-hidden="true" />
                    ) : (
                      <Boxes className="h-4 w-4" aria-hidden="true" />
                    )}
                  </div>
                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-2">
                      <h3 className="text-sm font-medium">{source.name}</h3>
                      <Badge variant="secondary" className="font-normal">
                        始终启用
                      </Badge>
                    </div>
                    <p className="mt-1 text-xs text-muted-foreground">
                      目录共 {source.count} 项，逐项授权在接口目录中管理。
                    </p>
                  </div>
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => onBrowseSource(source.id)}
                  >
                    查看目录
                    <ChevronRight aria-hidden="true" />
                  </Button>
                </article>
              ))}
            </div>

            <div className="flex items-center justify-between gap-3 border-t pt-3">
              <div>
                <h3 className="text-sm font-medium">MoviePilot 实例</h3>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  停用实例不会删除已配置的工具权限。
                </p>
              </div>
              <Badge variant="outline">{upstreams.length} 个实例</Badge>
            </div>

            {upstreams.length === 0 ? (
              <div className="rounded-xl border border-dashed py-10 text-center">
                <Server className="mx-auto mb-2 h-5 w-5 text-muted-foreground" aria-hidden="true" />
                <p className="text-sm text-muted-foreground">暂无 MoviePilot 实例。</p>
                <p className="mt-1 text-xs text-muted-foreground">
                  部署并配置可访问的实例后，会出现在这里。
                </p>
              </div>
            ) : (
              <>
                <div className="hidden overflow-hidden rounded-xl border md:block">
                  <Table className="table-fixed">
                    <TableHeader>
                      <TableRow>
                        <TableHead className="w-[27%]">实例</TableHead>
                        <TableHead className="w-[20%]">可用性</TableHead>
                        <TableHead className="w-[16%]">工具前缀</TableHead>
                        <TableHead className="w-[13%]">权限</TableHead>
                        <TableHead className="w-[10%]">实例启用</TableHead>
                        <TableHead className="w-[14%] text-right">目录</TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {upstreams.map((upstream) => (
                        <TableRow key={upstream.instance_id}>
                          <TableCell className="align-top whitespace-normal">
                            <div className="font-medium break-words">
                              {upstream.instance_name}
                            </div>
                            <div className="mt-1 text-xs break-words text-muted-foreground">
                              {upstream.display_name} · {upstream.status}
                            </div>
                          </TableCell>
                          <TableCell className="align-top whitespace-normal">
                            <UpstreamStatus upstream={upstream} />
                          </TableCell>
                          <TableCell className="align-top break-all whitespace-normal font-mono text-xs">
                            {upstream.prefix}
                          </TableCell>
                          <TableCell className="align-top whitespace-normal text-xs">
                            已授权 {upstream.allowed_tools.length}
                            <div className="mt-1 text-[11px] text-muted-foreground">
                              目录 {sourceCounts.get(`upstream:${upstream.instance_id}`) ?? 0} 项
                            </div>
                          </TableCell>
                          <TableCell className="align-top whitespace-normal">
                            {pendingInstance === upstream.instance_id ? (
                              <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" aria-label="正在保存实例设置" />
                            ) : (
                              <Switch
                                aria-label={`${upstream.enabled ? "停用" : "启用"} ${upstream.instance_name}`}
                                checked={upstream.enabled}
                                disabled={pendingInstance !== null}
                                onCheckedChange={(enabled) =>
                                  void toggleUpstream(upstream, enabled)
                                }
                              />
                            )}
                          </TableCell>
                          <TableCell className="align-top text-right whitespace-normal">
                            <Button
                              type="button"
                              variant="outline"
                              size="sm"
                              onClick={() =>
                                onBrowseSource(`upstream:${upstream.instance_id}`)
                              }
                            >
                              查看
                              <ChevronRight aria-hidden="true" />
                            </Button>
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>

                <div className="space-y-3 md:hidden">
                  {upstreams.map((upstream) => (
                    <article
                      key={upstream.instance_id}
                      className="space-y-3 rounded-xl border bg-card p-3"
                    >
                      <div className="flex min-w-0 items-start justify-between gap-3">
                        <div className="min-w-0">
                          <h4 className="text-sm font-medium break-words">
                            {upstream.instance_name}
                          </h4>
                          <p className="mt-1 text-xs break-words text-muted-foreground">
                            {upstream.display_name} · {upstream.app_name}
                          </p>
                        </div>
                        <Badge variant="secondary">{upstream.status}</Badge>
                      </div>
                      <UpstreamStatus upstream={upstream} />
                      <div className="grid grid-cols-[72px_minmax(0,1fr)] gap-x-2 gap-y-1.5 text-xs">
                        <span className="text-muted-foreground">工具前缀</span>
                        <code className="break-all font-mono">{upstream.prefix}</code>
                        <span className="text-muted-foreground">权限</span>
                        <span>
                          已授权 {upstream.allowed_tools.length} / 目录 {sourceCounts.get(`upstream:${upstream.instance_id}`) ?? 0} 项
                        </span>
                      </div>
                      <div className="flex items-center justify-between border-t pt-3">
                        <div className="flex items-center gap-2">
                          {pendingInstance === upstream.instance_id ? (
                            <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" aria-label="正在保存实例设置" />
                          ) : (
                            <Switch
                              aria-label={`${upstream.enabled ? "停用" : "启用"} ${upstream.instance_name}`}
                              checked={upstream.enabled}
                              disabled={pendingInstance !== null}
                              onCheckedChange={(enabled) =>
                                void toggleUpstream(upstream, enabled)
                              }
                            />
                          )}
                          <span className="text-xs text-muted-foreground">
                            {upstream.enabled ? "实例已启用" : "实例已停用"}
                          </span>
                        </div>
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          onClick={() =>
                            onBrowseSource(`upstream:${upstream.instance_id}`)
                          }
                        >
                          查看目录
                          <ChevronRight aria-hidden="true" />
                        </Button>
                      </div>
                    </article>
                  ))}
                </div>
              </>
            )}
          </>
        )}
      </CardContent>
    </Card>
  );
}
