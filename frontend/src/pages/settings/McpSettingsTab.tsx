import { useState } from "react";
import {
  AlertCircle,
  Copy,
  History,
  KeyRound,
  ListChecks,
  Loader2,
  Network,
  Plus,
  RefreshCw,
  Server,
  Trash2,
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
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useToast } from "@/components/ui/toast";
import {
  useCreateMcpToken,
  useMcpAudit,
  useMcpConfig,
  useMcpTokens,
  useRevokeMcpToken,
  useUpdateMcpConfig,
  type McpCreatedToken,
  type McpToken,
} from "@/hooks/useMcp";
import { cn, copyToClipboard, formatDate } from "@/lib/utils";
import { McpCatalog } from "./mcp/McpCatalog";
import { McpSourcesCard } from "./mcp/McpSourcesCard";

const INPUT_CLASS =
  "h-8 rounded-md border-input bg-white px-2 text-sm shadow-none dark:bg-input/30";

function formatTime(value: string | null) {
  return value ? formatDate(value) : "-";
}

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

function GlobalSettings() {
  const configQuery = useMcpConfig();
  const updateConfig = useUpdateMcpConfig();
  const toast = useToast();
  const endpoint = configQuery.data?.endpoint ?? "/mcp";

  const copyEndpoint = async () => {
    const copied = await copyToClipboard(endpoint);
    if (copied) toast.success("MCP 地址已复制");
    else toast.error("复制失败，请手动复制");
  };

  return (
    <Card className="rounded-xl" size="sm">
      <CardContent>
        {configQuery.isLoading ? (
          <LoadingMessage>加载 MCP 配置...</LoadingMessage>
        ) : configQuery.error ? (
          <ErrorMessage
            message="MCP 配置加载失败。"
            onRetry={() => configQuery.refetch()}
          />
        ) : (
          <div className="space-y-3">
            <div className="flex flex-col gap-3 xl:flex-row xl:items-center">
              <div className="flex shrink-0 flex-wrap items-center gap-2.5">
                <Network className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
                <Label htmlFor="mcp-enabled" className="text-sm font-medium">
                  MCP 聚合接入
                </Label>
                <Switch
                  id="mcp-enabled"
                  checked={configQuery.data?.enabled ?? false}
                  disabled={updateConfig.isPending}
                  onCheckedChange={(enabled) => updateConfig.mutate({ enabled })}
                />
                <Badge
                  variant="outline"
                  className={
                    configQuery.data?.enabled
                      ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400"
                      : "bg-muted text-muted-foreground"
                  }
                >
                  {configQuery.data?.enabled ? "已启用" : "已停用"}
                </Badge>
                {updateConfig.isPending && (
                  <Loader2 className="h-3.5 w-3.5 animate-spin text-muted-foreground" aria-label="正在保存" />
                )}
              </div>
              <div className="flex min-w-0 flex-1 flex-col gap-2 sm:flex-row sm:items-center xl:justify-end">
                <Label htmlFor="mcp-endpoint" className="shrink-0 text-xs text-muted-foreground">
                  MCP 地址
                </Label>
                <div className="flex min-w-0 gap-2 sm:max-w-md sm:flex-1">
                  <Input
                    id="mcp-endpoint"
                    value={endpoint}
                    readOnly
                    onFocus={(event) => event.currentTarget.select()}
                    className={cn(INPUT_CLASS, "font-mono")}
                  />
                  <Button type="button" variant="outline" size="sm" onClick={copyEndpoint}>
                    <Copy aria-hidden="true" />
                    复制
                  </Button>
                </div>
              </div>
            </div>
            <p className="text-[11px] leading-relaxed text-muted-foreground">
              客户端在请求头中设置
              <code className="mx-1 rounded bg-muted px-1.5 py-0.5 font-mono text-foreground">
                Authorization: Bearer &lt;Token&gt;
              </code>
              ；目录中的“实际生效”同时取决于全局、来源和单项权限。
            </p>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

function CreatedTokenDialog({
  token,
  onClose,
}: {
  token: McpCreatedToken;
  onClose: () => void;
}) {
  const toast = useToast();

  const copyToken = async () => {
    const copied = await copyToClipboard(token.token);
    if (copied) toast.success("Token 已复制");
    else toast.error("复制失败，请手动复制");
  };

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Token 创建成功</DialogTitle>
          <DialogDescription>
            明文仅此一次展示，关闭后无法再次查看，请立即复制并妥善保存。
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-2">
          <Label htmlFor="mcp-created-token">{token.name}</Label>
          <Input
            id="mcp-created-token"
            value={token.token}
            readOnly
            autoFocus
            onFocus={(event) => event.currentTarget.select()}
            className={cn(INPUT_CLASS, "h-auto min-h-8 py-2 font-mono break-all")}
          />
        </div>
        <DialogFooter>
          <Button type="button" variant="outline" onClick={copyToken}>
            <Copy aria-hidden="true" />
            复制 Token
          </Button>
          <Button type="button" onClick={onClose}>
            已保存，关闭
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function TokensCard() {
  const tokensQuery = useMcpTokens();
  const createToken = useCreateMcpToken();
  const revokeToken = useRevokeMcpToken();
  const toast = useToast();
  const [name, setName] = useState("");
  const [expiresDate, setExpiresDate] = useState("");
  const [createdToken, setCreatedToken] = useState<McpCreatedToken | null>(null);
  const [revokingToken, setRevokingToken] = useState<McpToken | null>(null);
  const [renderedAt] = useState(() => Date.now());
  const tokens = tokensQuery.data ?? [];

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    const trimmedName = name.trim();
    if (!trimmedName) {
      toast.error("请输入 Token 名称");
      return;
    }

    let expiresAt: string | null = null;
    if (expiresDate) {
      const date = new Date(`${expiresDate}T23:59:59.999`);
      if (Number.isNaN(date.getTime()) || date.getTime() <= Date.now()) {
        toast.error("过期日期不能早于今天");
        return;
      }
      expiresAt = date.toISOString();
    }

    createToken.mutate(
      { name: trimmedName, expires_at: expiresAt },
      {
        onSuccess: (token) => {
          setCreatedToken(token);
          setName("");
          setExpiresDate("");
        },
      }
    );
  };

  const closeCreatedToken = () => {
    setCreatedToken(null);
    createToken.reset();
  };

  return (
    <Card className="rounded-xl">
      <CardHeader className="pb-3">
        <CardTitle className="flex items-center gap-2 text-base">
          <KeyRound className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
          访问 Token
        </CardTitle>
        <CardDescription>
          为 MCP 客户端创建独立凭据；撤销后使用该 Token 的客户端将立即无法访问。
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-5">
        <form
          onSubmit={submit}
          className="grid grid-cols-1 items-end gap-3 rounded-lg border bg-muted/20 p-3 md:grid-cols-[minmax(0,1fr)_220px_auto]"
        >
          <div className="space-y-1.5">
            <Label htmlFor="mcp-token-name" className="text-xs font-medium">
              名称
            </Label>
            <Input
              id="mcp-token-name"
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="如 Claude Desktop"
              required
              className={INPUT_CLASS}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="mcp-token-expires" className="text-xs font-medium">
              过期日期（可选）
            </Label>
            <Input
              id="mcp-token-expires"
              type="date"
              value={expiresDate}
              onChange={(event) => setExpiresDate(event.target.value)}
              className={INPUT_CLASS}
            />
            <p className="text-[11px] text-muted-foreground">留空表示永不过期</p>
          </div>
          <Button
            type="submit"
            size="sm"
            disabled={!name.trim() || createToken.isPending}
            className="md:mb-5"
          >
            {createToken.isPending ? (
              <Loader2 className="animate-spin" aria-hidden="true" />
            ) : (
              <Plus aria-hidden="true" />
            )}
            {createToken.isPending ? "创建中..." : "创建 Token"}
          </Button>
        </form>

        {tokensQuery.isLoading ? (
          <LoadingMessage>加载 Token...</LoadingMessage>
        ) : tokensQuery.error ? (
          <ErrorMessage
            message="Token 列表加载失败。"
            onRetry={() => tokensQuery.refetch()}
          />
        ) : tokens.length === 0 ? (
          <div className="rounded-lg border border-dashed py-8 text-center text-sm text-muted-foreground">
            尚未创建 Token。
          </div>
        ) : (
          <div className="overflow-x-auto rounded-lg border">
            <Table className="min-w-[900px]">
              <TableHeader>
                <TableRow>
                  <TableHead>名称</TableHead>
                  <TableHead>前缀</TableHead>
                  <TableHead>状态</TableHead>
                  <TableHead>创建时间</TableHead>
                  <TableHead>过期时间</TableHead>
                  <TableHead>最近使用</TableHead>
                  <TableHead className="text-right">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {tokens.map((token) => {
                  const expired =
                    !token.revoked_at &&
                    token.expires_at !== null &&
                    new Date(token.expires_at).getTime() <= renderedAt;
                  return (
                    <TableRow key={token.id}>
                      <TableCell className="font-medium">{token.name}</TableCell>
                      <TableCell className="font-mono text-xs">{token.prefix}</TableCell>
                      <TableCell>
                        <Badge
                          variant="outline"
                          className={
                            token.revoked_at || expired
                              ? "bg-muted text-muted-foreground"
                              : "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400"
                          }
                        >
                          {token.revoked_at ? "已撤销" : expired ? "已过期" : "有效"}
                        </Badge>
                      </TableCell>
                      <TableCell className="text-xs">{formatTime(token.created_at)}</TableCell>
                      <TableCell className="text-xs">
                        {token.expires_at ? formatTime(token.expires_at) : "永不过期"}
                      </TableCell>
                      <TableCell className="text-xs">{formatTime(token.last_used_at)}</TableCell>
                      <TableCell className="text-right">
                        <Button
                          type="button"
                          variant="ghost"
                          size="icon-sm"
                          disabled={Boolean(token.revoked_at) || revokeToken.isPending}
                          onClick={() => setRevokingToken(token)}
                          title={token.revoked_at ? "已撤销" : "撤销 Token"}
                          aria-label={`撤销 ${token.name}`}
                        >
                          <Trash2 className="h-4 w-4" aria-hidden="true" />
                        </Button>
                      </TableCell>
                    </TableRow>
                  );
                })}
              </TableBody>
            </Table>
          </div>
        )}
      </CardContent>

      {createdToken && (
        <CreatedTokenDialog token={createdToken} onClose={closeCreatedToken} />
      )}
      <ConfirmDialog
        open={revokingToken !== null}
        onOpenChange={(open) => !open && setRevokingToken(null)}
        title={`确认撤销 ${revokingToken?.name ?? "Token"}？`}
        description="撤销后无法恢复，使用该 Token 的 MCP 客户端将无法继续访问。"
        confirmText="确认撤销"
        destructive
        isPending={revokeToken.isPending}
        onConfirm={() => {
          if (!revokingToken) return;
          revokeToken.mutate(revokingToken.id, {
            onSuccess: () => setRevokingToken(null),
          });
        }}
      />
    </Card>
  );
}

function AuditCard() {
  const auditQuery = useMcpAudit(100);
  const records = auditQuery.data ?? [];

  return (
    <Card className="rounded-xl">
      <CardHeader className="pb-3">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="space-y-1.5">
            <CardTitle className="flex items-center gap-2 text-base">
              <History className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
              最近审计记录
            </CardTitle>
            <CardDescription>最近 100 次工具调用及其执行结果。</CardDescription>
          </div>
          <Button
            type="button"
            variant="outline"
            size="sm"
            disabled={auditQuery.isRefetching}
            onClick={() => auditQuery.refetch()}
          >
            <RefreshCw
              className={auditQuery.isRefetching ? "animate-spin" : undefined}
              aria-hidden="true"
            />
            刷新
          </Button>
        </div>
      </CardHeader>
      <CardContent>
        {auditQuery.isLoading ? (
          <LoadingMessage>加载审计记录...</LoadingMessage>
        ) : auditQuery.error ? (
          <ErrorMessage
            message="审计记录加载失败。"
            onRetry={() => auditQuery.refetch()}
          />
        ) : records.length === 0 ? (
          <div className="rounded-lg border border-dashed py-8 text-center text-sm text-muted-foreground">
            暂无工具调用记录。
          </div>
        ) : (
          <div className="overflow-x-auto rounded-lg border">
            <Table className="min-w-[1000px]">
              <TableHeader>
                <TableRow>
                  <TableHead>工具</TableHead>
                  <TableHead>来源</TableHead>
                  <TableHead>结果</TableHead>
                  <TableHead>耗时</TableHead>
                  <TableHead>时间</TableHead>
                  <TableHead>错误</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {records.map((record) => (
                  <TableRow key={record.id}>
                    <TableCell className="font-mono text-xs">{record.tool_name}</TableCell>
                    <TableCell>
                      <div>{record.source}</div>
                      {record.upstream_instance_id !== null && (
                        <div className="text-xs text-muted-foreground">
                          实例 #{record.upstream_instance_id}
                        </div>
                      )}
                    </TableCell>
                    <TableCell>
                      <Badge
                        variant="outline"
                        className={
                          record.success
                            ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400"
                            : "border-destructive/30 bg-destructive/10 text-destructive"
                        }
                      >
                        {record.success ? "成功" : "失败"}
                      </Badge>
                    </TableCell>
                    <TableCell className="tabular-nums">
                      {record.duration_ms < 1000
                        ? `${Math.round(record.duration_ms)} ms`
                        : `${(record.duration_ms / 1000).toFixed(2)} s`}
                    </TableCell>
                    <TableCell className="text-xs">{formatTime(record.created_at)}</TableCell>
                    <TableCell
                      className={cn(
                        "max-w-72 truncate text-xs",
                        record.error_message ? "text-destructive" : "text-muted-foreground"
                      )}
                      title={record.error_message ?? undefined}
                    >
                      {record.error_message || "-"}
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

export function McpSettingsTab() {
  const [activeTab, setActiveTab] = useState("catalog");
  const [catalogSource, setCatalogSource] = useState("all");

  const browseSource = (source: string) => {
    setCatalogSource(source);
    setActiveTab("catalog");
  };

  return (
    <div className="space-y-4">
      <GlobalSettings />
      <Tabs value={activeTab} onValueChange={setActiveTab} className="w-full">
        <TabsList className="grid h-auto! w-full grid-cols-2 gap-1 sm:flex sm:h-8! sm:w-fit">
          <TabsTrigger value="catalog" className="gap-1.5 px-3">
            <ListChecks aria-hidden="true" />
            接口目录
          </TabsTrigger>
          <TabsTrigger value="sources" className="gap-1.5 px-3">
            <Server aria-hidden="true" />
            来源管理
          </TabsTrigger>
          <TabsTrigger value="tokens" className="gap-1.5 px-3">
            <KeyRound aria-hidden="true" />
            访问 Token
          </TabsTrigger>
          <TabsTrigger value="audit" className="gap-1.5 px-3">
            <History aria-hidden="true" />
            调用审计
          </TabsTrigger>
        </TabsList>
        <TabsContent value="catalog" className="mt-4">
          <McpCatalog
            sourceFilter={catalogSource}
            onSourceFilterChange={setCatalogSource}
          />
        </TabsContent>
        <TabsContent value="sources" className="mt-4">
          <McpSourcesCard onBrowseSource={browseSource} />
        </TabsContent>
        <TabsContent value="tokens" className="mt-4">
          <TokensCard />
        </TabsContent>
        <TabsContent value="audit" className="mt-4">
          <AuditCard />
        </TabsContent>
      </Tabs>
    </div>
  );
}
