/**
 * 容器详情弹窗组件
 *
 * 展示容器基础信息、状态、端口映射、挂载、环境变量、网络等。
 */

import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { useContainerDetail } from "@/hooks/useDocker";
import { formatDate } from "@/lib/utils";
import { Loader2, Container } from "lucide-react";

interface ContainerDetailDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  containerId: string | null;
}

function InfoRow({
  label,
  value,
}: {
  label: string;
  value: React.ReactNode;
}) {
  return (
    <div className="grid grid-cols-[8rem_1fr] gap-4 py-2.5 border-b last:border-0 border-border/50 items-start">
      <span className="text-sm text-muted-foreground text-left">{label}</span>
      <span className="text-sm text-left break-all">{value}</span>
    </div>
  );
}

function EnvList({ env }: { env: string[] }) {
  return (
    <div className="space-y-1.5 w-full">
      {env.map((item, index) => {
        const eqIndex = item.indexOf("=");
        const key = eqIndex >= 0 ? item.slice(0, eqIndex) : item;
        const value = eqIndex >= 0 ? item.slice(eqIndex + 1) : "";
        return (
          <div
            key={`${key}-${index}`}
            className="grid grid-cols-[9rem_1fr] gap-4 text-xs items-start"
          >
            <span className="text-muted-foreground font-medium text-left">{key}</span>
            <span className="break-all text-left font-mono">{value}</span>
          </div>
        );
      })}
    </div>
  );
}

export function ContainerDetailDialog({
  open,
  onOpenChange,
  containerId,
}: ContainerDetailDialogProps) {
  const { data: detail, isLoading } = useContainerDetail(containerId);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-5xl max-h-[85vh] overflow-hidden flex flex-col p-0">
        <DialogHeader className="px-6 pt-5 pb-2 border-b shrink-0">
          <DialogTitle className="text-lg font-semibold flex items-center gap-2">
            <Container className="h-5 w-5 text-muted-foreground" />
            容器详情
          </DialogTitle>
        </DialogHeader>

        <div className="overflow-y-auto px-6 py-3 space-y-4">
          {isLoading ? (
            <div className="flex justify-center py-12">
              <Loader2 className="h-6 w-6 animate-spin text-primary" />
            </div>
          ) : !detail ? (
            <p className="text-muted-foreground text-center py-12">
              无法加载容器详情
            </p>
          ) : (
            <>
              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm font-semibold">基础信息</CardTitle>
                </CardHeader>
                <CardContent className="space-y-1">
                  <InfoRow label="ID" value={<span className="font-mono text-xs">{detail.id}</span>} />
                  <InfoRow label="名称" value={detail.name} />
                  <InfoRow label="镜像" value={detail.image} />
                  <InfoRow
                    label="状态"
                    value={
                      <div className="flex items-center gap-2">
                        {detail.state}
                        {detail.health !== "unknown" && (
                          <Badge
                            variant={
                              detail.health === "healthy" ? "default" : "destructive"
                            }
                            className="text-xs"
                          >
                            {detail.health}
                          </Badge>
                        )}
                      </div>
                    }
                  />
                  <InfoRow label="退出码" value={String(detail.exit_code)} />
                  {detail.error && <InfoRow label="错误" value={detail.error} />}
                  <InfoRow label="创建时间" value={formatDate(detail.created)} />
                  {detail.started_at && (
                    <InfoRow label="启动时间" value={formatDate(detail.started_at)} />
                  )}
                  {detail.finished_at && detail.status !== "running" && (
                    <InfoRow label="停止时间" value={formatDate(detail.finished_at)} />
                  )}
                  <InfoRow label="工作目录" value={detail.working_dir || "-"} />
                  <InfoRow label="用户" value={detail.user || "-"} />
                  <InfoRow label="网络模式" value={detail.network_mode} />
                  <InfoRow label="重启策略" value={detail.restart_policy} />
                  <InfoRow
                    label="特权容器"
                    value={detail.privileged ? "是" : "否"}
                  />
                </CardContent>
              </Card>

              {(detail.command && detail.command.length > 0) && (
                <Card>
                  <CardHeader className="pb-2">
                    <CardTitle className="text-sm font-semibold">命令</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <code className="text-xs block break-all font-mono">
                      {detail.command.join(" ")}
                    </code>
                  </CardContent>
                </Card>
              )}

              {detail.entrypoint && detail.entrypoint.length > 0 && (
                <Card>
                  <CardHeader className="pb-2">
                    <CardTitle className="text-sm font-semibold">入口点</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <code className="text-xs block break-all font-mono">
                      {detail.entrypoint.join(" ")}
                    </code>
                  </CardContent>
                </Card>
              )}

              {detail.env && detail.env.length > 0 && (
                <Card>
                  <CardHeader className="pb-2">
                    <CardTitle className="text-sm font-semibold">环境变量</CardTitle>
                  </CardHeader>
                  <CardContent>
                    <EnvList env={detail.env} />
                  </CardContent>
                </Card>
              )}

              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm font-semibold">端口映射</CardTitle>
                </CardHeader>
                <CardContent>
                  {detail.ports.length === 0 ? (
                    <p className="text-sm text-muted-foreground">无端口映射</p>
                  ) : (
                    <div className="border rounded-lg overflow-hidden">
                      <Table>
                        <TableHeader>
                          <TableRow>
                            <TableHead className="text-xs">容器端口</TableHead>
                            <TableHead className="text-xs">宿主机 IP</TableHead>
                            <TableHead className="text-xs">宿主机端口</TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {detail.ports.map((port, index) => (
                            <TableRow key={index}>
                              <TableCell className="font-mono text-xs">{port.container_port}</TableCell>
                              <TableCell className="text-xs">{port.host_ip || "0.0.0.0"}</TableCell>
                              <TableCell className="font-mono text-xs">{port.host_port}</TableCell>
                            </TableRow>
                          ))}
                        </TableBody>
                      </Table>
                    </div>
                  )}
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm font-semibold">挂载</CardTitle>
                </CardHeader>
                <CardContent>
                  {detail.mounts.length === 0 ? (
                    <p className="text-sm text-muted-foreground">无挂载</p>
                  ) : (
                    <div className="border rounded-lg overflow-hidden">
                      <Table>
                        <TableHeader>
                          <TableRow>
                            <TableHead className="text-xs">类型</TableHead>
                            <TableHead className="text-xs">宿主机路径</TableHead>
                            <TableHead className="text-xs">容器路径</TableHead>
                            <TableHead className="text-xs">模式</TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {detail.mounts.map((mount, index) => (
                            <TableRow key={index}>
                              <TableCell className="text-xs">{mount.type}</TableCell>
                              <TableCell className="font-mono text-xs">{mount.source}</TableCell>
                              <TableCell className="font-mono text-xs">{mount.destination}</TableCell>
                              <TableCell className="text-xs">{mount.mode}</TableCell>
                            </TableRow>
                          ))}
                        </TableBody>
                      </Table>
                    </div>
                  )}
                </CardContent>
              </Card>

              <Card>
                <CardHeader className="pb-2">
                  <CardTitle className="text-sm font-semibold">网络</CardTitle>
                </CardHeader>
                <CardContent>
                  {detail.networks.length === 0 ? (
                    <p className="text-sm text-muted-foreground">无网络信息</p>
                  ) : (
                    <div className="border rounded-lg overflow-hidden">
                      <Table>
                        <TableHeader>
                          <TableRow>
                            <TableHead className="text-xs">网络</TableHead>
                            <TableHead className="text-xs">IP 地址</TableHead>
                            <TableHead className="text-xs">网关</TableHead>
                            <TableHead className="text-xs">MAC 地址</TableHead>
                          </TableRow>
                        </TableHeader>
                        <TableBody>
                          {detail.networks.map((net, index) => (
                            <TableRow key={index}>
                              <TableCell className="text-xs">{net.name}</TableCell>
                              <TableCell className="font-mono text-xs">{net.ip_address}</TableCell>
                              <TableCell className="font-mono text-xs">{net.gateway}</TableCell>
                              <TableCell className="font-mono text-xs">{net.mac_address}</TableCell>
                            </TableRow>
                          ))}
                        </TableBody>
                      </Table>
                    </div>
                  )}
                </CardContent>
              </Card>
            </>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
