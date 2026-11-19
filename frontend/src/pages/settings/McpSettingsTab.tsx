import { useState } from "react";
import {
  AlertCircle,
  Calendar,
  Clock,
  Copy,
  Info,
  KeyRound,
  Loader2,
  RotateCcw,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useToast } from "@/components/ui/toast";
import { useMcpKey, useResetMcpKey } from "@/hooks/useMcp";
import { copyToClipboard, formatDate } from "@/lib/utils";
import { McpCatalog } from "./mcp/McpCatalog";
import { McpConnectCard } from "./mcp/McpConnectCard";

function LoadingMessage({ children }: { children: React.ReactNode }) {
  return (
    <div className="flex items-center gap-2 py-6 text-sm text-muted-foreground">
      <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
      {children}
    </div>
  );
}

function ErrorMessage({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}) {
  return (
    <div className="flex flex-wrap items-center gap-3 py-3 text-sm text-destructive">
      <AlertCircle className="h-4 w-4 shrink-0" aria-hidden="true" />
      <span>{message}</span>
      <Button type="button" variant="outline" size="sm" onClick={onRetry}>
        重新加载
      </Button>
    </div>
  );
}

function McpKeyCard() {
  const keyQuery = useMcpKey();
  const resetKey = useResetMcpKey();
  const toast = useToast();
  const [confirmReset, setConfirmReset] = useState(false);
  const key = keyQuery.data;

  const copyKey = async () => {
    if (!key) return;
    const copied = await copyToClipboard(key.key);
    if (copied) toast.success("API 密钥已复制");
    else toast.error("复制失败，请手动复制");
  };

  return (
    <Card className="rounded-xl" size="sm">
      <CardHeader className="pb-0">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div>
            <CardTitle className="flex items-center gap-2 text-base">
              <KeyRound className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
              API 密钥管理
              <Tooltip>
                <TooltipTrigger
                  delay={150}
                  render={
                    <button
                      type="button"
                      aria-label="密钥说明"
                      className="inline-flex h-4 w-4 cursor-help items-center justify-center rounded-full text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/30"
                    >
                      <Info className="h-3.5 w-3.5" aria-hidden="true" />
                    </button>
                  }
                />
                <TooltipContent
                  side="top"
                  className="max-w-64 whitespace-normal leading-relaxed"
                >
                  仅保留一个密钥，永不过期；重置后旧密钥立即失效。
                </TooltipContent>
              </Tooltip>
            </CardTitle>
          </div>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={keyQuery.isLoading || resetKey.isPending}
            onClick={() => setConfirmReset(true)}
          >
            {resetKey.isPending ? (
              <Loader2 className="animate-spin" aria-hidden="true" />
            ) : (
              <RotateCcw aria-hidden="true" />
            )}
            重置密钥
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        {keyQuery.isLoading ? (
          <LoadingMessage>加载 API 密钥...</LoadingMessage>
        ) : keyQuery.error || !key ? (
          <ErrorMessage
            message="API 密钥加载失败。"
            onRetry={() => keyQuery.refetch()}
          />
        ) : (
          <div className="flex w-full flex-wrap items-center justify-between gap-x-6 gap-y-3 text-xs sm:max-w-5xl">
            <div className="flex min-w-0 items-center gap-2">
              <KeyRound className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
              <span className="text-muted-foreground">密钥</span>
              <div className="flex min-w-0 items-center overflow-hidden rounded-md border">
                <code className="min-w-0 truncate bg-muted/40 px-2 py-1 font-mono text-foreground">
                  {key.masked_key}
                </code>
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="shrink-0 rounded-none border-l"
                  onClick={copyKey}
                >
                  <Copy aria-hidden="true" />
                  复制
                </Button>
              </div>
            </div>
            <div className="flex items-center gap-1.5 text-muted-foreground">
              <Calendar className="h-4 w-4" aria-hidden="true" />
              创建
              <span className="text-foreground">{formatDate(key.created_at)}</span>
            </div>
            <div className="flex items-center gap-1.5 text-muted-foreground">
              <Clock className="h-4 w-4" aria-hidden="true" />
              使用
              <span className="text-foreground">
                {key.last_used_at ? formatDate(key.last_used_at) : "—"}
              </span>
            </div>
          </div>
        )}
      </CardContent>
      <ConfirmDialog
        open={confirmReset}
        onOpenChange={setConfirmReset}
        title="确认重置 API 密钥？"
        description="重置后旧密钥立即失效，使用旧密钥的 MCP 客户端需要更新配置。"
        confirmText="确认重置"
        destructive
        isPending={resetKey.isPending}
        onConfirm={() => {
          resetKey.mutate(undefined, {
            onSuccess: () => setConfirmReset(false),
          });
        }}
      />
    </Card>
  );
}

export function McpSettingsTab() {
  return (
    <div className="space-y-4">
      <McpKeyCard />
      <McpConnectCard />
      <McpCatalog />
    </div>
  );
}
