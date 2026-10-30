/**
 * 应用详情弹窗
 */

import {
  Dialog,
  DialogContent,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { ExternalLink, Code, Cpu } from "lucide-react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { AppIcon } from "./AppIcon";
import { formatVersion } from "@/lib/utils";
import type { App } from "@/hooks/useApps";

interface AppDetailDialogProps {
  app: App | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onDeploy: (app: App) => void;
}

/** 协议白名单：仅允许 http/https 链接，防止 javascript: 等协议注入 */
function safeHttpUrl(url: string | null | undefined): string | undefined {
  if (!url) return undefined;
  try {
    const parsed = new URL(url);
    return parsed.protocol === "http:" || parsed.protocol === "https:"
      ? url
      : undefined;
  } catch {
    return undefined;
  }
}

export function AppDetailDialog({
  app,
  open,
  onOpenChange,
  onDeploy,
}: AppDetailDialogProps) {
  if (!app) return null;

  const readme = (app as unknown as { readme?: string }).readme || "暂无说明";

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent showCloseButton={false} className="sm:max-w-3xl max-h-[85vh] flex flex-col p-0 overflow-hidden">
        {/* 顶部 header */}
        <div className="bg-gradient-to-br from-primary/5 to-transparent p-5 flex-shrink-0">
          <div className="flex items-start gap-5">
            <AppIcon
              app={app}
              className="h-20 w-20 rounded-2xl object-contain bg-white border border-border/60 shadow-sm text-3xl"
            />

            <div className="flex-1 min-w-0">
              <DialogTitle className="text-2xl font-semibold tracking-tight">
                {app.display_name}
              </DialogTitle>
              <DialogDescription className="mt-1.5 text-sm text-muted-foreground leading-relaxed line-clamp-2">
                {app.description || "暂无描述"}
              </DialogDescription>
              <div className="flex flex-wrap items-center gap-2 mt-3">
                {app.is_paid && (
                  <Badge
                    variant="outline"
                    className="text-xs font-normal border-amber-500/30 bg-amber-500/10 text-amber-700"
                  >
                    付费
                  </Badge>
                )}
                <Badge variant="outline" className="text-xs font-normal">
                  版本 {formatVersion(app.version)}
                </Badge>
                <Badge variant="outline" className="text-xs font-normal capitalize">
                  {app.type === "compose" ? "多容器编排" : "单容器"}
                </Badge>
                {app.image && (
                  <Badge variant="outline" className="text-xs font-normal hidden sm:inline-flex">
                    {app.image.split(":")[0].split("/").pop()}
                  </Badge>
                )}
              </div>
            </div>

            <Button
              className="rounded-full px-6 shrink-0 shadow-none"
              style={{ boxShadow: "none" }}
              onClick={() => {
                onOpenChange(false);
                onDeploy(app);
              }}
            >
              安装
            </Button>
          </div>
        </div>

        {/* 信息卡片 */}
        <div className="grid grid-cols-1 sm:grid-cols-3 gap-px bg-border border-y flex-shrink-0">
          <a
            href={safeHttpUrl(app.website)}
            target="_blank"
            rel="noopener noreferrer"
            className="bg-card p-4 hover:bg-muted/40 transition-colors"
          >
            <div className="flex items-center gap-2 text-muted-foreground mb-1">
              <ExternalLink className="h-3.5 w-3.5" />
              <span className="text-xs">官方网站</span>
            </div>
            <div className="text-sm text-primary font-medium truncate">
              {app.website ? "访问官网" : "未提供"}
            </div>
          </a>
          <a
            href={safeHttpUrl(app.source_url)}
            target="_blank"
            rel="noopener noreferrer"
            className="bg-card p-4 hover:bg-muted/40 transition-colors"
          >
            <div className="flex items-center gap-2 text-muted-foreground mb-1">
              <Code className="h-3.5 w-3.5" />
              <span className="text-xs">开源社区</span>
            </div>
            <div className="text-sm text-primary font-medium truncate">
              {app.source_url ? "项目仓库" : "未提供"}
            </div>
          </a>
          <div className="bg-card p-4">
            <div className="flex items-center gap-2 text-muted-foreground mb-1">
              <Cpu className="h-3.5 w-3.5" />
              <span className="text-xs">支持架构</span>
            </div>
            <div className="flex flex-wrap gap-1.5">
              {app.architectures && app.architectures.length > 0 ? (
                app.architectures.map((arch) => (
                  <Badge
                    key={arch}
                    variant="outline"
                    className="text-xs font-normal rounded-md px-2 py-0.5"
                  >
                    {arch}
                  </Badge>
                ))
              ) : (
                <span className="text-sm text-muted-foreground">-</span>
              )}
            </div>
          </div>
        </div>

        {/* README */}
        <div className="flex-1 overflow-y-auto p-6 bg-white">
          <div className="markdown-body">
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{readme}</ReactMarkdown>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
