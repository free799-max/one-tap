/**
 * MoviePilot 集成面板
 *
 * 展示下载器/媒体服务器自动装配结果、Webhook 回调地址与插件管理。
 */

import { useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Separator } from "@/components/ui/separator";
import type { ApiError } from "@/api/types";
import {
  useAppAssets,
  useAppPlugins,
  useCopyAppAssets,
  useInstallAppPlugin,
  useWireOrchestrationInstance,
  type AppIntegrations,
  type AppPlugin,
  type IntegrationRecord,
  type OrchestrationInstanceDetail,
} from "@/hooks/useOrchestrations";
import {
  Check,
  Copy,
  Download,
  HardDrive,
  Link2,
  Loader2,
  Package,
  RefreshCw,
  Unplug,
} from "lucide-react";

function formatWiredAt(value?: string) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN");
}

function WebhookUrlRow({ url }: { url: string }) {
  const [copied, setCopied] = useState(false);
  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(url);
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      // 剪贴板不可用时静默忽略，用户可手动全选复制
    }
  };
  return (
    <div className="mt-2 space-y-1.5">
      <div className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
        <Link2 className="h-3.5 w-3.5" />
        Webhook 回调地址
      </div>
      <div className="flex items-center gap-2">
        <Input
          readOnly
          value={url}
          className="h-8 font-mono text-xs"
          onFocus={(e) => e.target.select()}
        />
        <Button
          size="sm"
          variant="outline"
          className="h-8 shrink-0 gap-1.5 rounded-lg"
          onClick={handleCopy}
        >
          {copied ? (
            <Check className="h-3.5 w-3.5" />
          ) : (
            <Copy className="h-3.5 w-3.5" />
          )}
          {copied ? "已复制" : "复制"}
        </Button>
      </div>
      <p className="text-xs text-muted-foreground">
        需在 Jellyfin Webhook 插件中手动配置此地址
      </p>
    </div>
  );
}

function IntegrationRecordRow({ record }: { record: IntegrationRecord }) {
  const ok = record.status === "ok";
  return (
    <div className="rounded-lg border border-border px-3 py-2.5">
      <div className="flex items-center gap-2">
        <span className="text-sm font-medium">{record.name}</span>
        {record.type && (
          <span className="text-xs text-muted-foreground">{record.type}</span>
        )}
        <Badge
          variant={ok ? "secondary" : "destructive"}
          className={
            ok
              ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
              : undefined
          }
        >
          {ok ? "成功" : "失败"}
        </Badge>
      </div>
      {record.url && (
        <p className="mt-0.5 font-mono text-xs text-muted-foreground break-all">
          {record.url}
        </p>
      )}
      {record.message && (
        <p className="mt-0.5 text-xs text-muted-foreground break-words">
          {record.message}
        </p>
      )}
      {record.webhook_url && <WebhookUrlRow url={record.webhook_url} />}
    </div>
  );
}

function getPluginId(plugin: AppPlugin) {
  return String(plugin.id ?? plugin.plugin_id ?? "");
}

function getPluginName(plugin: AppPlugin) {
  return String(
    plugin.plugin_name ?? plugin.id ?? plugin.plugin_id ?? "未知插件"
  );
}

export function MoviePilotIntegrationPanel({
  instanceId,
  detail,
}: {
  instanceId: number;
  detail: OrchestrationInstanceDetail;
}) {
  const wireMutation = useWireOrchestrationInstance();
  const pluginsQuery = useAppPlugins(instanceId, "moviepilot");
  const installMutation = useInstallAppPlugin();
  const assetsQuery = useAppAssets(instanceId, "moviepilot");
  const copyAssetsMutation = useCopyAppAssets();
  const [installingId, setInstallingId] = useState<string | null>(null);

  const assets = assetsQuery.data;

  const rawEntry = (detail.app_configs?.["moviepilot"] ?? {}) as Record<
    string,
    unknown
  >;
  const integrations = rawEntry.integrations as AppIntegrations | undefined;
  const downloaders = integrations?.downloaders ?? [];
  const mediaservers = integrations?.mediaservers ?? [];
  const hasIntegrations =
    !!integrations && (downloaders.length > 0 || mediaservers.length > 0);

  const pluginsError = pluginsQuery.error as ApiError | null;
  const installed = pluginsQuery.data?.installed ?? [];
  const market = pluginsQuery.data?.market ?? [];
  const installedIds = new Set(installed.map(getPluginId));

  const handleInstall = (plugin: AppPlugin) => {
    const pluginId = getPluginId(plugin);
    if (!pluginId) return;
    setInstallingId(pluginId);
    installMutation.mutate(
      {
        instanceId,
        appName: "moviepilot",
        pluginId,
        repoUrl:
          typeof plugin.repo_url === "string" ? plugin.repo_url : undefined,
      },
      { onSettled: () => setInstallingId(null) }
    );
  };

  const renderPluginRow = (plugin: AppPlugin, isInstalled: boolean) => {
    const pluginId = getPluginId(plugin);
    return (
      <div
        key={pluginId || getPluginName(plugin)}
        className="flex items-center gap-3 rounded-lg border border-border px-3 py-2.5"
      >
        <div className="min-w-0 flex-1">
          <div className="flex items-center gap-2">
            <span className="truncate text-sm font-medium">
              {getPluginName(plugin)}
            </span>
            {plugin.plugin_version && (
              <span className="font-mono text-xs text-muted-foreground">
                v{String(plugin.plugin_version)}
              </span>
            )}
            {isInstalled && (
              <Badge
                variant="secondary"
                className="bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
              >
                已安装
              </Badge>
            )}
          </div>
          {typeof plugin.plugin_desc === "string" && plugin.plugin_desc && (
            <p className="mt-0.5 line-clamp-2 text-xs text-muted-foreground">
              {plugin.plugin_desc}
            </p>
          )}
        </div>
        {!isInstalled && (
          <Button
            size="sm"
            variant="outline"
            className="h-8 shrink-0 gap-1.5 rounded-lg"
            onClick={() => handleInstall(plugin)}
            disabled={!pluginId || installingId !== null}
          >
            {installingId === pluginId ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Download className="h-3.5 w-3.5" />
            )}
            安装
          </Button>
        )}
      </div>
    );
  };

  return (
    <div className="space-y-5">
      <div className="space-y-3">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 text-sm font-medium">
            自动装配
            {integrations?.wired_at && (
              <span className="text-xs font-normal text-muted-foreground">
                装配于 {formatWiredAt(integrations.wired_at)}
              </span>
            )}
          </div>
          <Button
            size="sm"
            variant="outline"
            className="h-8 shrink-0 gap-1.5 rounded-lg"
            onClick={() => wireMutation.mutate(instanceId)}
            disabled={wireMutation.isPending}
          >
            {wireMutation.isPending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <RefreshCw className="h-3.5 w-3.5" />
            )}
            重新装配
          </Button>
        </div>

        {!hasIntegrations ? (
          <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-border py-8 text-muted-foreground">
            <Unplug className="h-6 w-6" />
            <span className="text-xs">尚未自动装配</span>
          </div>
        ) : (
          <div className="space-y-3">
            {downloaders.length > 0 && (
              <div className="space-y-2">
                <div className="text-xs font-medium text-muted-foreground">
                  下载器
                </div>
                {downloaders.map((record) => (
                  <IntegrationRecordRow
                    key={`dl-${record.name}`}
                    record={record}
                  />
                ))}
              </div>
            )}
            {mediaservers.length > 0 && (
              <div className="space-y-2">
                <div className="text-xs font-medium text-muted-foreground">
                  媒体服务器
                </div>
                {mediaservers.map((record) => (
                  <IntegrationRecordRow
                    key={`ms-${record.name}`}
                    record={record}
                  />
                ))}
              </div>
            )}
          </div>
        )}
      </div>

      <Separator />

      <div className="space-y-3">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 text-sm font-medium">
            <HardDrive className="h-4 w-4 text-muted-foreground" />
            离线数据
          </div>
          <Button
            size="sm"
            variant="outline"
            className="h-8 shrink-0 gap-1.5 rounded-lg"
            onClick={() =>
              copyAssetsMutation.mutate({
                instanceId,
                appName: "moviepilot",
              })
            }
            disabled={
              copyAssetsMutation.isPending ||
              !assets?.supported ||
              !assets?.container
            }
          >
            {copyAssetsMutation.isPending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Download className="h-3.5 w-3.5" />
            )}
            {assets?.copied ? "重新拷贝到容器" : "拷贝到容器"}
          </Button>
        </div>

        <p className="text-xs text-muted-foreground">
          插件依赖和浏览器内核会从资源镜像直接拷贝进容器，拷贝期间应用会短暂停止；容器重建后需要重新拷贝。
        </p>

        {assetsQuery.isLoading ? (
          <div className="flex items-center justify-center gap-2 rounded-lg border border-border bg-muted/30 px-3 py-6 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            正在查询离线数据状态…
          </div>
        ) : !assets?.supported ? (
          <div className="rounded-lg border border-dashed border-border py-6 text-center text-xs text-muted-foreground">
            当前应用没有可用的离线数据
          </div>
        ) : (
          <div className="space-y-2 rounded-lg border border-border px-3 py-2.5">
            <div className="flex flex-wrap items-center gap-2">
              <Badge
                variant={assets.copied ? "secondary" : "outline"}
                className={
                  assets.copied
                    ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
                    : "text-muted-foreground"
                }
              >
                {assets.copied ? "已拷贝" : "未拷贝"}
              </Badge>
              {assets.copied_at && (
                <span className="text-xs text-muted-foreground">
                  拷贝于 {formatWiredAt(assets.copied_at)}
                </span>
              )}
              {assets.assets_image && (
                <span className="font-mono text-[11px] text-muted-foreground">
                  {assets.assets_image}
                </span>
              )}
            </div>
            {(assets.copies ?? []).map((item) => (
              <div
                key={item.source}
                className="flex items-center justify-between gap-2 text-xs text-muted-foreground"
              >
                <span>{item.label}</span>
                <span>{item.size}</span>
              </div>
            ))}
            {assets.message && (
              <p className="text-xs text-amber-600">{assets.message}</p>
            )}
          </div>
        )}
      </div>

      <Separator />

      <div className="space-y-3">
        <div className="flex items-center gap-2 text-sm font-medium">
          <Package className="h-4 w-4 text-muted-foreground" />
          插件管理
        </div>

        {pluginsQuery.isLoading ? (
          <div className="flex items-center justify-center gap-2 rounded-lg border border-border bg-muted/30 px-3 py-6 text-sm text-muted-foreground">
            <Loader2 className="h-4 w-4 animate-spin" />
            正在查询插件列表…
          </div>
        ) : pluginsError ? (
          <div className="rounded-lg border border-red-200 bg-red-50 px-3 py-2.5 text-xs text-red-600 dark:border-red-900/50 dark:bg-red-950/30">
            查询插件列表失败：
            {pluginsError.displayMessage ||
              "MoviePilot 未就绪或未认证，请稍后在概览页检查访问配置"}
          </div>
        ) : installed.length === 0 && market.length === 0 ? (
          <div className="rounded-lg border border-dashed border-border py-6 text-center text-xs text-muted-foreground">
            暂无插件
          </div>
        ) : (
          <div className="space-y-3">
            {installed.length > 0 && (
              <div className="space-y-2">
                <div className="text-xs font-medium text-muted-foreground">
                  已安装
                </div>
                {installed.map((plugin) => renderPluginRow(plugin, true))}
              </div>
            )}
            {market.filter((p) => !installedIds.has(getPluginId(p))).length >
              0 && (
              <div className="space-y-2">
                <div className="text-xs font-medium text-muted-foreground">
                  插件市场
                </div>
                {market
                  .filter((p) => !installedIds.has(getPluginId(p)))
                  .map((plugin) => renderPluginRow(plugin, false))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
