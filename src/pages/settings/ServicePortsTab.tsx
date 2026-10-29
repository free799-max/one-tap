import { useEffect, useMemo, useState } from "react";
import {
  AlertCircle,
  Loader2,
  Plug,
  RefreshCw,
  Server,
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
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useContainers } from "@/hooks/docker/useContainers";
import { useSystemConfig, useUpdateSystemConfig } from "@/hooks/useSettings";
import type { ContainerInfo } from "@/api/docker";

interface PortUsageRow {
  container: ContainerInfo;
  host_port: string;
  host_ips: string[];
  container_ports: string[];
}

function formatListenScope(hostIps: string[]) {
  if (
    hostIps.some((ip) => !ip || ["0.0.0.0", "::", "[::]"].includes(ip))
  ) {
    return "所有网卡";
  }
  if (hostIps.every((ip) => ["127.0.0.1", "::1"].includes(ip))) {
    return "仅本机";
  }
  return hostIps.join("、");
}

function ServicePortConfig() {
  const { data: config, isLoading } = useSystemConfig();
  const updateConfig = useUpdateSystemConfig();
  const [portStart, setPortStart] = useState(10000);
  const [initialized, setInitialized] = useState(false);

  useEffect(() => {
    if (config && !initialized) {
      setPortStart(config.service_port_start);
      setInitialized(true);
    }
  }, [config, initialized]);

  if (isLoading || !config) {
    return <div className="text-sm text-muted-foreground">加载配置中...</div>;
  }

  const isDirty = portStart !== config.service_port_start;

  const handleSubmit = (event: React.FormEvent) => {
    event.preventDefault();
    updateConfig.mutate({
      http_proxy: config.http_proxy || null,
      https_proxy: config.https_proxy || null,
      service_port_start: portStart,
      default_admin_username: config.default_admin_username || "admin",
      default_admin_password: config.default_admin_password,
      docker_registry_mirrors: config.docker_registry_mirrors,
    });
  };

  return (
    <Card className="rounded-xl">
      <CardHeader className="pb-2">
        <CardTitle className="flex items-center gap-2 text-base">
          <Plug className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
          服务端口配置
        </CardTitle>
        <CardDescription>设置部署服务时自动分配端口的起始位置。</CardDescription>
      </CardHeader>
      <CardContent>
        <form
          onSubmit={handleSubmit}
          className="flex flex-col gap-3 lg:flex-row lg:items-center"
        >
          <Label
            htmlFor="service_port_start"
            className="shrink-0 text-xs font-medium"
          >
            服务起始端口
          </Label>
          <Input
            id="service_port_start"
            type="number"
            min={1}
            max={65535}
            required
            value={portStart}
            onChange={(event) =>
              setPortStart(Number.parseInt(event.target.value, 10) || 10000)
            }
            className="h-8 w-full rounded-md border-input bg-white px-2 text-sm shadow-none lg:w-36"
          />
          <p className="flex-1 text-xs text-muted-foreground">
            创建服务时从该端口开始按顺序分配，自动跳过已占用端口。
          </p>
          <Button
            type="submit"
            disabled={updateConfig.isPending || !isDirty}
            className="shrink-0 self-end rounded-md lg:self-auto"
          >
            {updateConfig.isPending ? "保存中..." : "保存配置"}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
}

function DockerPortUsage() {
  const {
    data: containers,
    isLoading,
    isRefetching,
    error,
    refetch,
  } = useContainers();

  const rows = useMemo<PortUsageRow[]>(() => {
    const groupedRows = new Map<string, PortUsageRow>();

    for (const container of containers ?? []) {
      for (const binding of container.port_bindings) {
        const key = `${container.id}:${binding.host_port}`;
        const existingRow = groupedRows.get(key);
        if (existingRow) {
          if (!existingRow.host_ips.includes(binding.host_ip)) {
            existingRow.host_ips.push(binding.host_ip);
          }
          if (!existingRow.container_ports.includes(binding.container_port)) {
            existingRow.container_ports.push(binding.container_port);
          }
        } else {
          groupedRows.set(key, {
            container,
            host_port: binding.host_port,
            host_ips: [binding.host_ip],
            container_ports: [binding.container_port],
          });
        }
      }
    }

    return Array.from(groupedRows.values()).sort((a, b) => {
      const portDifference =
        (Number(a.host_port) || 65536) - (Number(b.host_port) || 65536);
      return portDifference || a.container.name.localeCompare(b.container.name);
    });
  }, [containers]);

  const uniqueHostPorts = new Set(rows.map((row) => row.host_port)).size;

  return (
    <Card className="rounded-xl">
      <CardHeader className="pb-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="space-y-1.5">
            <CardTitle className="flex items-center gap-2 text-base">
              <Server className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
              Docker 端口占用
            </CardTitle>
            <CardDescription>
              查看 Docker 服务的宿主机端口映射，每 10 秒自动刷新。
            </CardDescription>
          </div>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={isRefetching}
            onClick={() => refetch()}
          >
            <RefreshCw
              className={isRefetching ? "animate-spin" : undefined}
              aria-hidden="true"
            />
            刷新
          </Button>
        </div>
      </CardHeader>
      <CardContent className="space-y-4">
        {isLoading ? (
          <div className="flex items-center gap-2 py-8 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            加载端口占用情况...
          </div>
        ) : error ? (
          <div className="flex flex-col items-start gap-3 py-4">
            <p className="flex items-center gap-2 text-sm text-destructive">
              <AlertCircle className="h-4 w-4" aria-hidden="true" />
              无法获取 Docker 端口占用情况，请检查 Docker 服务后重试。
            </p>
            <Button variant="outline" size="sm" onClick={() => refetch()}>
              重新加载
            </Button>
          </div>
        ) : rows.length === 0 ? (
          <div className="py-8 text-center text-sm text-muted-foreground">
            {containers?.length ? "当前 Docker 容器没有端口映射" : "暂无 Docker 容器"}
          </div>
        ) : (
          <>
            <div className="flex flex-wrap gap-x-5 gap-y-1 text-xs text-muted-foreground">
              <span>共 {rows.length} 条端口配置</span>
              <span>占用 {uniqueHostPorts} 个宿主机端口</span>
              <span>IPv4 与 IPv6 合并显示，已停止容器也会显示其端口配置</span>
            </div>
            <div className="overflow-hidden rounded-lg border">
              <Table className="min-w-[900px]">
                <TableHeader>
                  <TableRow>
                    <TableHead>宿主机端口</TableHead>
                    <TableHead>监听范围</TableHead>
                    <TableHead>容器端口</TableHead>
                    <TableHead>占用服务</TableHead>
                    <TableHead>镜像</TableHead>
                    <TableHead>容器状态</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {rows.map((row) => (
                    <TableRow key={`${row.container.id}-${row.host_port}`}>
                      <TableCell className="font-mono text-sm font-semibold">
                        {row.host_port || "-"}
                      </TableCell>
                      <TableCell
                        className="text-xs text-muted-foreground"
                        title={row.host_ips.join("、")}
                      >
                        {formatListenScope(row.host_ips)}
                      </TableCell>
                      <TableCell className="font-mono text-xs">
                        {row.container_ports.join("、")}
                      </TableCell>
                      <TableCell className="font-medium">
                        {row.container.name}
                      </TableCell>
                      <TableCell
                        className="max-w-64 truncate text-xs text-muted-foreground"
                        title={row.container.image}
                      >
                        {row.container.image}
                      </TableCell>
                      <TableCell>
                        <Badge
                          variant={row.container.status === "running" ? "default" : "secondary"}
                        >
                          {row.container.state}
                        </Badge>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          </>
        )}
      </CardContent>
    </Card>
  );
}

export function ServicePortsTab() {
  return (
    <div className="space-y-5">
      <ServicePortConfig />
      <DockerPortUsage />
    </div>
  );
}
