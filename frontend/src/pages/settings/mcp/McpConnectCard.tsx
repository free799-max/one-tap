import { useState } from "react";
import { ChevronDown, Copy, Info, Loader2, PlugZap } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import { useToast } from "@/components/ui/toast";
import { useMcpConnect } from "@/hooks/useMcp";
import { cn, copyToClipboard } from "@/lib/utils";

export function McpConnectCard() {
  const connectQuery = useMcpConnect();
  const toast = useToast();
  const [showPrompt, setShowPrompt] = useState(false);
  const data = connectQuery.data;

  const copyPrompt = async () => {
    if (!data) return;
    const copied = await copyToClipboard(data.prompt);
    if (copied) toast.success("接入提示词已复制，粘贴给 AI 即可");
    else toast.error("复制失败，请手动复制");
  };

  const copyConfig = async () => {
    if (!data) return;
    const copied = await copyToClipboard(data.config_json);
    if (copied) toast.success("MCP 配置已复制");
    else toast.error("复制失败，请手动复制");
  };

  return (
    <Card className="rounded-xl" size="sm">
      <CardHeader className="pb-0">
        <div className="flex flex-wrap items-start justify-between gap-2">
          <div className="min-w-0">
            <CardTitle className="flex items-center gap-2 text-base">
              <PlugZap className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
              AI 一键接入
              <Tooltip>
                <TooltipTrigger
                  delay={150}
                  render={
                    <button
                      type="button"
                      aria-label="接入说明"
                      className="inline-flex h-4 w-4 cursor-help items-center justify-center rounded-full text-muted-foreground transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/30"
                    >
                      <Info className="h-3.5 w-3.5" aria-hidden="true" />
                    </button>
                  }
                />
                <TooltipContent
                  side="top"
                  className="max-w-72 whitespace-normal leading-relaxed"
                >
                  提示词里含完整密钥，只粘贴给本机可信的 AI Agent；密钥重置后需重新复制。
                </TooltipContent>
              </Tooltip>
            </CardTitle>
            <CardDescription className="mt-1 text-xs">
              复制提示词发给任意 AI Agent（Claude Code、Codex CLI、Cursor、Gemini
              CLI 等），它会自动写入 MCP 配置和技能文件，之后就能查询、订阅、下载、整理、
              通知和替换影视、漫画、音乐、小说资源。
            </CardDescription>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={!data}
              onClick={() => setShowPrompt((value) => !value)}
            >
              <ChevronDown
                className={cn("transition-transform", showPrompt && "rotate-180")}
                aria-hidden="true"
              />
              {showPrompt ? "收起提示词" : "预览提示词"}
            </Button>
            <Button
              type="button"
              size="sm"
              disabled={!data || connectQuery.isLoading}
              onClick={copyPrompt}
            >
              <Copy aria-hidden="true" />
              复制提示词
            </Button>
          </div>
        </div>
      </CardHeader>
      <CardContent>
        {connectQuery.isLoading ? (
          <div className="flex items-center gap-2 py-2 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
            生成接入提示词...
          </div>
        ) : connectQuery.error || !data ? (
          <div className="flex items-center gap-2 py-2 text-sm text-destructive">
            接入提示词生成失败，请刷新页面重试。
          </div>
        ) : (
          <div className="space-y-2">
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-muted-foreground">
              <span className="flex min-w-0 items-center gap-1.5">
                地址
                <code className="min-w-0 truncate rounded bg-muted px-1.5 py-0.5 font-mono text-foreground">
                  {data.endpoint}
                </code>
              </span>
              <span className="flex min-w-0 items-center gap-1.5">
                密钥
                <code className="rounded bg-muted px-1.5 py-0.5 font-mono text-foreground">
                  {data.masked_key}
                </code>
              </span>
              <Button
                type="button"
                variant="ghost"
                size="sm"
                className="h-6 px-2 text-xs text-muted-foreground"
                onClick={copyConfig}
              >
                <Copy aria-hidden="true" />
                仅复制 MCP 配置
              </Button>
            </div>
            {showPrompt && (
              <pre className="max-h-72 overflow-auto whitespace-pre-wrap rounded-md border bg-muted/30 p-3 font-mono text-[11px] leading-relaxed text-muted-foreground">
                {data.prompt}
              </pre>
            )}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
