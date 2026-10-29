/**
 * 镜像详情弹窗组件（方案 1：单栏垂直滚动）
 *
 * 将 Image details、Dockerfile details、Image layers 三个区块
 * 按顺序垂直展示在一个弹窗面板内，无 Tab 切换。
 */

import { useState } from "react";
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
import { Button } from "@/components/ui/button";
import { useImageDetail, type ImageDetail } from "@/hooks/useDocker";
import { formatBytes, formatDate } from "@/lib/utils";
import { Loader2, Copy, Check } from "lucide-react";

interface ImageDetailDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  imageId: string | null;
}

function CopyButton({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = () => {
    navigator.clipboard.writeText(text);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };
  return (
    <Button
      size="icon"
      variant="ghost"
      className="h-6 w-6 ml-1.5 shrink-0"
      onClick={handleCopy}
    >
      {copied ? (
        <Check className="h-3 w-3 text-green-500" />
      ) : (
        <Copy className="h-3 w-3" />
      )}
    </Button>
  );
}

function InfoRow({
  label,
  value,
}: {
  label: string;
  value: React.ReactNode;
}) {
  return (
    <div className="grid grid-cols-[9rem_1fr] gap-4 py-2.5 border-b last:border-0 border-border/50 items-start">
      <span className="text-sm text-muted-foreground text-left">
        {label}
      </span>
      <span className="text-sm text-left break-all">{value}</span>
    </div>
  );
}

function CodeBlock({ children }: { children: React.ReactNode }) {
  return (
    <code className="text-xs block break-all font-mono text-left">
      {children}
    </code>
  );
}

function formatCommand(args: string[]): string {
  return args
    .map((arg) => (arg.includes(" ") ? `"${arg}"` : arg))
    .join(" ");
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
            <span className="text-muted-foreground font-medium text-left">
              {key}
            </span>
            <span className="break-all text-left font-mono">
              {value}
            </span>
          </div>
        );
      })}
    </div>
  );
}

function ImageDetailsCard({ detail }: { detail: ImageDetail }) {
  return (
    <Card>
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-semibold">镜像信息</CardTitle>
      </CardHeader>
      <CardContent className="space-y-1">
        <InfoRow
          label="ID"
          value={
            <span className="font-mono text-xs flex items-center justify-start">
              {detail.id}
              <CopyButton text={detail.id} />
            </span>
          }
        />
        {detail.parent && (
          <InfoRow
            label="Parent"
            value={<span className="font-mono text-xs">{detail.parent}</span>}
          />
        )}
        <InfoRow label="Size" value={formatBytes(detail.size)} />
        <InfoRow label="Created" value={formatDate(detail.created)} />
        {detail.build && <InfoRow label="Build" value={detail.build} />}
      </CardContent>
    </Card>
  );
}

function DockerfileDetailsCard({ detail }: { detail: ImageDetail }) {
  const hasContent =
    detail.cmd ||
    detail.entrypoint ||
    (detail.env && detail.env.length > 0);
  if (!hasContent) return null;

  return (
    <Card className="mt-4">
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-semibold">
          Dockerfile 详情
        </CardTitle>
      </CardHeader>
      <CardContent className="space-y-3">
        {detail.cmd && (
          <InfoRow
            label="CMD"
            value={<CodeBlock>{formatCommand(detail.cmd)}</CodeBlock>}
          />
        )}
        {detail.entrypoint && (
          <InfoRow
            label="ENTRYPOINT"
            value={<CodeBlock>{formatCommand(detail.entrypoint)}</CodeBlock>}
          />
        )}
        {detail.env && detail.env.length > 0 && (
          <InfoRow label="ENV" value={<EnvList env={detail.env} />} />
        )}
      </CardContent>
    </Card>
  );
}

function ImageLayersCard({ detail }: { detail: ImageDetail }) {
  const layers = detail.layers_table;

  return (
    <Card className="mt-4">
      <CardHeader className="pb-2">
        <CardTitle className="text-sm font-semibold">镜像层</CardTitle>
      </CardHeader>
      <CardContent>
        {!layers || layers.length === 0 ? (
          <p className="text-sm text-muted-foreground text-center py-6">
            无层信息
          </p>
        ) : (
          <div className="border rounded-lg overflow-hidden">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-14 text-xs">Order</TableHead>
                  <TableHead className="w-20 text-xs">Size</TableHead>
                  <TableHead className="text-xs">Layer</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {layers.map((layer) => (
                  <TableRow key={layer.order}>
                    <TableCell className="text-xs text-muted-foreground">
                      {layer.order}
                    </TableCell>
                    <TableCell className="text-xs whitespace-nowrap">
                      {formatBytes(layer.size)}
                    </TableCell>
                    <TableCell className="text-xs font-mono max-w-0">
                      <span
                        className="block truncate"
                        title={layer.layer}
                      >
                        {layer.layer}
                      </span>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export function ImageDetailDialog({
  open,
  onOpenChange,
  imageId,
}: ImageDetailDialogProps) {
  const { data: detail, isLoading } = useImageDetail(imageId);

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-5xl max-h-[85vh] overflow-hidden flex flex-col p-0">
        <DialogHeader className="px-6 pt-5 pb-2 border-b shrink-0">
          <DialogTitle className="text-lg font-semibold">
            镜像详情
          </DialogTitle>
        </DialogHeader>

        <div className="overflow-y-auto px-6 py-3">
          {isLoading ? (
            <div className="flex justify-center py-12">
              <Loader2 className="h-6 w-6 animate-spin text-primary" />
            </div>
          ) : !detail ? (
            <p className="text-muted-foreground text-center py-12">
              Unable to load image details
            </p>
          ) : (
            <>
              <ImageDetailsCard detail={detail} />
              <DockerfileDetailsCard detail={detail} />
              <ImageLayersCard detail={detail} />
            </>
          )}
        </div>
      </DialogContent>
    </Dialog>
  );
}
