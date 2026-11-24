/**
 * Docker 宿主机信息页面组件
 *
 * 以紧凑卡片布局展示 Docker 宿主机综合信息，包括：
 * - 资源概览（CPU、内存、磁盘、容器总数、运行中、暂停、已停止、镜像；右上角 info 图标悬停查看主机/引擎详情）
 * - Docker 网络列表
 *
 * 当 Docker 不可用或未连接时显示提示信息。
 */
import { useMemo, useState } from "react";
import {
  Card,
  CardAction,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  useDeleteDockerNetwork,
  useDockerHostInfo,
  useDockerStatus,
} from "@/hooks/useDocker";
import { useToast } from "@/components/ui/toast";
import { useAuth } from "@/hooks/useAuth";
import { formatBytes } from "@/lib/utils";
import type { NetworkInfo } from "@/api/docker";
import {
  Cpu,
  MemoryStick,
  HardDrive,
  Box,
  Play,
  Pause,
  Square,
  Image,
  Network,
  LayoutDashboard,
  Info,
  Trash2,
  BrushCleaning,
} from "lucide-react";

/**
 * Docker 宿主机信息页面组件
 */
export function DockerHostPage() {
  const { data: dockerStatus } = useDockerStatus();
  const { data: hostInfo, isLoading } = useDockerHostInfo();
  const { user } = useAuth();
  const toast = useToast();
  const deleteNetworkMutation = useDeleteDockerNetwork();
  const [pendingNetwork, setPendingNetwork] = useState<NetworkInfo | null>(null);
  const [cleanupOpen, setCleanupOpen] = useState(false);
  const [cleanupProgress, setCleanupProgress] = useState<{
    done: number;
    total: number;
  } | null>(null);

  const isAdmin = user?.role === "admin";

  const networks = useMemo(() => {
    const list = [...(hostInfo?.networks ?? [])];
    return list.sort((a, b) => {
      const builtinDelta = Number(a.is_builtin ?? false) - Number(b.is_builtin ?? false);
      if (builtinDelta !== 0) return builtinDelta;
      const usedDelta =
        Number((a.container_count ?? 0) > 0) - Number((b.container_count ?? 0) > 0);
      if (usedDelta !== 0) return usedDelta;
      return a.name.localeCompare(b.name);
    });
  }, [hostInfo?.networks]);

  const unusedNetworks = useMemo(
    () =>
      networks.filter(
        (net) => (net.container_count ?? 0) === 0 && !net.is_builtin
      ),
    [networks]
  );

  const unusedCount = unusedNetworks.length;

  const cleaningUp = cleanupProgress !== null;

  const handleConfirmDelete = () => {
    if (!pendingNetwork) return;
    const name = pendingNetwork.name;
    deleteNetworkMutation.mutate(pendingNetwork.id, {
      onSuccess: () => {
        toast.success(`已删除网络 ${name}`);
        setPendingNetwork(null);
      },
      onError: (error) => {
        const message =
          (error as { displayMessage?: string }).displayMessage ||
          `删除网络 ${name} 失败`;
        toast.error(message);
        setPendingNetwork(null);
      },
    });
  };

  const handleCleanup = async () => {
    if (!isAdmin || unusedNetworks.length === 0) return;
    const targets = unusedNetworks;
    let deleted = 0;
    const failures: string[] = [];
    setCleanupProgress({ done: 0, total: targets.length });
    for (const net of targets) {
      try {
        await deleteNetworkMutation.mutateAsync(net.id);
        deleted += 1;
      } catch (error) {
        const message = (error as { displayMessage?: string }).displayMessage;
        failures.push(message ? `${net.name}：${message}` : net.name);
      }
      setCleanupProgress({ done: deleted + failures.length, total: targets.length });
    }
    setCleanupProgress(null);
    setCleanupOpen(false);
    if (failures.length === 0) {
      toast.success(`已清理 ${deleted} 个未使用网络`);
    } else if (deleted === 0) {
      toast.error(`清理失败：${failures[0]}`);
    } else {
      toast.error(`已清理 ${deleted} 个网络，${failures.length} 个失败：${failures[0]}`);
    }
  };

  // 加载中状态（优先判断，避免闪烁）
  if (isLoading) {
    return (
      <div className="space-y-6">
        <p className="text-muted-foreground">加载中...</p>
      </div>
    );
  }

  // Docker 不可用或未连接时，显示提示信息
  if (dockerStatus && !dockerStatus.available) {
    return (
      <div className="space-y-6">
        <p className="text-muted-foreground">Docker 不可用或未连接。</p>
      </div>
    );
  }

  if (!hostInfo) {
    return (
      <div className="space-y-6">
        <p className="text-muted-foreground">无法获取主机信息。</p>
      </div>
    );
  }

  const { resources, stats, docker_version } = hostInfo;

  return (
    <div className="space-y-6">
      {/* 资源概览：硬件资源 + Docker 统计整合到一个卡片 */}
      <Card className="rounded-xl">
        <CardHeader className="pb-2">
          <div className="flex items-center justify-between">
            <CardTitle className="text-base flex items-center gap-2">
              <LayoutDashboard className="h-4 w-4 text-muted-foreground" />
              资源概览
            </CardTitle>
            <div className="relative group">
              <Info className="h-4 w-4 text-muted-foreground cursor-pointer hover:text-foreground transition-colors" />
              <div className="absolute right-0 top-full mt-2 hidden group-hover:block z-50 w-64 p-3 rounded-lg border bg-popover text-popover-foreground shadow-md text-xs">
                <div className="space-y-1.5">
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">主机名</span>
                    <span className="font-medium">{hostInfo.hostname}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">系统</span>
                    <span className="font-medium">
                      {hostInfo.os} ({hostInfo.arch})
                    </span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">Docker</span>
                    <span className="font-medium">{docker_version.version}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">API</span>
                    <span className="font-medium">{docker_version.api_version}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">存储驱动</span>
                    <span className="font-medium">{hostInfo.storage_driver}</span>
                  </div>
                  <div className="flex justify-between gap-2">
                    <span className="text-muted-foreground shrink-0">根目录</span>
                    <span className="font-medium truncate" title={hostInfo.docker_root_dir}>
                      {hostInfo.docker_root_dir}
                    </span>
                  </div>
                </div>
              </div>
            </div>
          </div>
        </CardHeader>
        <CardContent className="space-y-5">
          {/* 硬件资源 */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
            <div className="flex items-center gap-4">
              <div className="p-3 rounded-xl bg-muted">
                <Cpu className="h-6 w-6 text-muted-foreground" />
              </div>
              <div>
                <div className="text-sm font-medium text-muted-foreground">
                  CPU
                </div>
                <div className="text-2xl font-bold">{resources.cpu_cores} 核</div>
              </div>
            </div>
            <div className="flex items-center gap-4">
              <div className="p-3 rounded-xl bg-muted">
                <MemoryStick className="h-6 w-6 text-muted-foreground" />
              </div>
              <div>
                <div className="text-sm font-medium text-muted-foreground">
                  内存
                </div>
                <div className="text-2xl font-bold">
                  {formatBytes(resources.memory_total)}
                </div>
              </div>
            </div>
            <div className="flex items-center gap-4">
              <div className="p-3 rounded-xl bg-muted">
                <HardDrive className="h-6 w-6 text-muted-foreground" />
              </div>
              <div className="flex-1 min-w-0">
                <div className="text-sm font-medium text-muted-foreground">
                  磁盘
                </div>
                <div className="text-2xl font-bold">
                  {formatBytes(resources.disk_total)}
                </div>
                {resources.disk_total === 0 ? (
                  <p className="text-xs text-muted-foreground mt-1">
                    无法获取磁盘信息
                  </p>
                ) : (
                  <>
                    <div className="flex justify-between text-xs text-muted-foreground mt-1.5">
                      <span>已用 {formatBytes(resources.disk_used)}</span>
                      <span>{resources.disk_usage_percent}%</span>
                    </div>
                    <div className="h-2 w-full rounded-full bg-muted overflow-hidden mt-1.5">
                      <div
                        className="h-full rounded-full bg-primary transition-all"
                        style={{ width: `${resources.disk_usage_percent}%` }}
                      />
                    </div>
                  </>
                )}
              </div>
            </div>
          </div>

          <Separator />

          {/* Docker 统计 */}
          <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-5 gap-3">
            <div className="flex items-center justify-between rounded-xl bg-muted/50 p-3">
              <div>
                <div className="text-xs font-medium text-muted-foreground">
                  容器总数
                </div>
                <div className="text-2xl font-bold">{stats.containers_total}</div>
              </div>
              <Box className="h-5 w-5 text-muted-foreground/50" />
            </div>
            <div className="flex items-center justify-between rounded-xl bg-muted/50 p-3">
              <div>
                <div className="text-xs font-medium text-muted-foreground">
                  运行中
                </div>
                <div className="text-2xl font-bold text-green-600">
                  {stats.containers_running}
                </div>
              </div>
              <Play className="h-5 w-5 text-green-500/60" />
            </div>
            <div className="flex items-center justify-between rounded-xl bg-muted/50 p-3">
              <div>
                <div className="text-xs font-medium text-muted-foreground">
                  暂停
                </div>
                <div className="text-2xl font-bold text-yellow-600">
                  {stats.containers_paused}
                </div>
              </div>
              <Pause className="h-5 w-5 text-yellow-500/60" />
            </div>
            <div className="flex items-center justify-between rounded-xl bg-muted/50 p-3">
              <div>
                <div className="text-xs font-medium text-muted-foreground">
                  已停止
                </div>
                <div className="text-2xl font-bold text-red-600">
                  {stats.containers_stopped}
                </div>
              </div>
              <Square className="h-5 w-5 text-red-500/60" />
            </div>
            <div className="flex items-center justify-between rounded-xl bg-muted/50 p-3">
              <div>
                <div className="text-xs font-medium text-muted-foreground">
                  镜像
                </div>
                <div className="text-2xl font-bold text-blue-600">
                  {stats.images}
                </div>
              </div>
              <Image className="h-5 w-5 text-blue-500/60" />
            </div>
          </div>
        </CardContent>
      </Card>

      {/* Docker 网络列表 */}
      <Card className="rounded-xl">
        <CardHeader className="pb-3">
          <CardTitle className="flex items-center gap-2 text-base">
            <Network className="h-4 w-4 text-muted-foreground" />
            Docker 网络
            <Badge
              variant="secondary"
              className="h-5 px-1.5 text-[11px] font-medium tabular-nums"
            >
              {networks.length}
            </Badge>
          </CardTitle>
          <p className="text-xs text-muted-foreground">
            {unusedCount > 0
              ? `${unusedCount} 个网络未关联容器，可直接删除`
              : "所有自定义网络都在使用中"}
          </p>
          <CardAction className="self-center">
            <Button
              variant="outline"
              disabled={!isAdmin || unusedCount === 0 || cleaningUp}
              title={
                !isAdmin
                  ? "需要管理员权限"
                  : unusedCount === 0
                    ? "没有可清理的网络"
                    : `清理 ${unusedCount} 个未使用网络`
              }
              onClick={() => setCleanupOpen(true)}
            >
              <BrushCleaning />
              一键清理
            </Button>
          </CardAction>
        </CardHeader>
        <CardContent className="pt-0">
          {networks.length === 0 ? (
            <p className="py-6 text-center text-sm text-muted-foreground">
              暂无网络。
            </p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow className="hover:bg-transparent">
                  <TableHead className="text-xs">网络</TableHead>
                  <TableHead className="text-xs">驱动</TableHead>
                  <TableHead className="text-xs">关联应用</TableHead>
                  <TableHead className="w-16 text-right text-xs">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {networks.map((net) => {
                  const count = net.container_count ?? 0;
                  const names = net.container_names ?? [];
                  const deletable = isAdmin && !net.is_builtin && count === 0;
                  const disabledReason = net.is_builtin
                    ? "Docker 预定义网络不可删除"
                    : count > 0
                      ? `仍有 ${count} 个应用在使用该网络`
                      : !isAdmin
                        ? "需要管理员权限"
                        : undefined;
                  return (
                    <TableRow key={net.id}>
                      <TableCell>
                        <div className="text-sm font-medium">{net.name}</div>
                        <div className="mt-0.5 font-mono text-[11px] text-muted-foreground">
                          {net.id}
                        </div>
                      </TableCell>
                      <TableCell>
                        <div className="flex items-center gap-1.5">
                          <Badge
                            variant="outline"
                            className="h-5 px-1.5 text-[11px] font-normal text-muted-foreground"
                          >
                            {net.driver}
                          </Badge>
                          {net.is_builtin && (
                            <span className="text-[11px] text-muted-foreground/70">
                              预定义
                            </span>
                          )}
                        </div>
                      </TableCell>
                      <TableCell>
                        {count > 0 ? (
                          names.length > 0 ? (
                            <div className="flex flex-wrap items-center gap-1.5">
                              {names.slice(0, 3).map((name) => (
                                <span
                                  key={name}
                                  title={name}
                                  className="max-w-[11rem] truncate rounded-md border border-border/60 bg-muted/40 px-1.5 py-0.5 font-mono text-[11px] text-foreground"
                                >
                                  {name}
                                </span>
                              ))}
                              {count > 3 && (
                                <span
                                  title={names.join("、")}
                                  className="rounded-md border border-border/60 bg-muted/40 px-1.5 py-0.5 text-[11px] tabular-nums text-muted-foreground"
                                >
                                  +{count - 3}
                                </span>
                              )}
                            </div>
                          ) : (
                            <span
                              className="inline-flex items-center gap-1 rounded-md bg-muted px-1.5 py-0.5 text-xs tabular-nums text-foreground"
                              title={names.join("、")}
                            >
                              <Box className="size-3 text-muted-foreground" />
                              {count} 个应用
                            </span>
                          )
                        ) : (
                          <span className="text-xs text-muted-foreground">
                            未使用
                          </span>
                        )}
                      </TableCell>
                      <TableCell className="text-right">
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          disabled={!deletable}
                          title={disabledReason ?? `删除网络 ${net.name}`}
                          aria-label={`删除网络 ${net.name}`}
                          onClick={() => setPendingNetwork(net)}
                          className="text-muted-foreground hover:text-destructive disabled:opacity-30"
                        >
                          <Trash2 className="size-3.5" />
                        </Button>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <ConfirmDialog
        open={!!pendingNetwork}
        onOpenChange={(open) => {
          if (!open && deleteNetworkMutation.isPending) return;
          if (!open) setPendingNetwork(null);
        }}
        title="删除 Docker 网络"
        description={
          pendingNetwork
            ? `确定删除网络 ${pendingNetwork.name}？该网络没有关联容器，删除后无法恢复。`
            : undefined
        }
        confirmText="删除"
        cancelText="取消"
        onConfirm={handleConfirmDelete}
        destructive
        isPending={deleteNetworkMutation.isPending}
      />

      <ConfirmDialog
        open={cleanupOpen}
        onOpenChange={(open) => {
          if (!open && cleaningUp) return;
          if (!open) setCleanupOpen(false);
        }}
        title="清理未使用网络"
        description={
          cleanupProgress
            ? `正在清理 ${cleanupProgress.done}/${cleanupProgress.total}…`
            : `将删除 ${unusedCount} 个未关联容器的自定义网络：${unusedNetworks
                .map((net) => net.name)
                .join("、")}。删除后无法恢复。`
        }
        confirmText={cleaningUp ? "清理中…" : `删除 ${unusedCount} 个`}
        cancelText="取消"
        onConfirm={handleCleanup}
        destructive
        isPending={cleaningUp}
      />
    </div>
  );
}
