/**
 * Docker 宿主机信息页面组件
 *
 * 以紧凑卡片布局展示 Docker 宿主机综合信息，包括：
 * - 资源概览（CPU、内存、磁盘、容器总数、运行中、暂停、已停止、镜像；右上角 info 图标悬停查看主机/引擎详情）
 * - Docker 网络列表
 *
 * 当 Docker 不可用或未连接时显示提示信息。
 */
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Separator } from "@/components/ui/separator";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useDockerHostInfo, useDockerStatus } from "@/hooks/useDocker";
import { formatBytes } from "@/lib/utils";
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
} from "lucide-react";

/**
 * Docker 宿主机信息页面组件
 */
export function DockerHostPage() {
  const { data: dockerStatus } = useDockerStatus();
  const { data: hostInfo, isLoading } = useDockerHostInfo();

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
        <CardHeader className="pb-2">
          <CardTitle className="text-base flex items-center gap-2">
            <Network className="h-4 w-4 text-muted-foreground" />
            Docker 网络
          </CardTitle>
        </CardHeader>
        <CardContent>
          {hostInfo.networks.length === 0 ? (
            <p className="text-sm text-muted-foreground">暂无网络。</p>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="text-xs">名称</TableHead>
                  <TableHead className="text-xs">驱动</TableHead>
                  <TableHead className="text-xs">作用域</TableHead>
                  <TableHead className="text-xs">ID</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {hostInfo.networks.map((net) => (
                  <TableRow key={net.id}>
                    <TableCell className="text-sm font-medium">{net.name}</TableCell>
                    <TableCell className="text-sm">{net.driver}</TableCell>
                    <TableCell className="text-sm">{net.scope}</TableCell>
                    <TableCell className="text-sm text-muted-foreground font-mono">
                      {net.id}
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
