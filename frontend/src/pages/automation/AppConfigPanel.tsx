/**
 * 应用配置面板
 *
 * 头部展示应用图标、名称、描述与运行状态，并提供访问与认证设置入口；
 * 下方以 Tab 组织「概览 / 检测优化 / 集成」内容。概览按
 * 「访问与认证」「编排与集成」两组信息卡展示，避免同等权重的瓦片堆叠。
 */

import { useEffect, useState, type ReactNode } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { PasswordInput } from "@/components/ui/password-input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  AlertTriangle,
  BookOpen,
  Check,
  Copy,
  ExternalLink,
  Globe,
  Layers,
  LayoutGrid,
  Loader2,
  Plug,
  RefreshCw,
  ScrollText,
  Shield,
  ShieldCheck,
  Stethoscope,
  Wand2,
  type LucideIcon,
} from "lucide-react";
import { AppIcon } from "../apps/AppIcon";
import type { App } from "@/hooks/useApps";
import { cn } from "@/lib/utils";
import type {
  AppIntegrations,
  AppOrchestration,
  OrchestrationInstanceApp,
  OrchestrationInstanceDetail,
  OrchestrationInstanceUpdatePayload,
} from "@/hooks/useOrchestrations";
import {
  useVerifyAppAuth,
  useWireOrchestrationInstance,
} from "@/hooks/useOrchestrations";
import { isAuthConfigReady } from "./auth-config-utils";
import { AppInspectPanel } from "./AppInspectPanel";
import { StackLogsDialog } from "@/pages/docker/stacks/StackLogsDialog";
import { MoviePilotIntegrationPanel } from "./MoviePilotIntegrationPanel";
import { MusicAutoOrganizePanel } from "./MusicAutoOrganizePanel";
import { ReaderAutoSyncPanel } from "./ReaderAutoSyncPanel";
import { APP_STATUS_META, APP_STATUS_PILL } from "./app-status";
import { getAppRoleLabel } from "./composition-meta";

interface AppConfigPanelProps {
  app: OrchestrationInstanceApp | null;
  detail: OrchestrationInstanceDetail | null | undefined;
  orchestration?: AppOrchestration | null;
  description?: string | null;
  onSave: (payload: OrchestrationInstanceUpdatePayload) => void;
  isPending?: boolean;
}

// 后端认证类型枚举统一使用 api_key；历史配置里可能存成 apikey，读取时兼容
type AuthType = "none" | "basic" | "api_key";

const AUTH_TYPE_LABELS: Record<AuthType, string> = {
  none: "无认证",
  basic: "账号密码",
  api_key: "API Key",
};

const INSPECTABLE_APPS = new Set(["jellyfin", "moviepilot", "qbittorrent"]);

interface AppAuthConfig {
  url?: string;
  auth_type?: AuthType;
  username?: string;
  password?: string;
  api_key?: string;
}

function normalizeAuthType(raw: unknown): AuthType {
  const value = String(raw ?? "");
  if (value === "basic") return "basic";
  if (value === "api_key" || value === "apikey") return "api_key";
  return "none";
}

function getInitialAuthConfig(raw: unknown): AppAuthConfig {
  const config = (raw ?? {}) as Record<string, unknown>;
  return {
    url: typeof config.url === "string" ? config.url : "",
    auth_type: normalizeAuthType(config.auth_type),
    username: typeof config.username === "string" ? config.username : "",
    password: typeof config.password === "string" ? config.password : "",
    api_key: typeof config.api_key === "string" ? config.api_key : "",
  };
}

function buildAuthConfig(values: AppAuthConfig): Record<string, unknown> {
  // auth_type 只决定当前生效的一套凭据，另一套要原样保留：
  // 用户切换认证方式后仍能拿回之前填写的账号密码或 API Key
  return {
    url: values.url?.trim() || "",
    auth_type: values.auth_type,
    username: values.username?.trim() || "",
    password: values.password || "",
    api_key: values.api_key?.trim() || "",
  };
}

/** 信息分组卡片 */
function InfoSection({
  icon: Icon,
  title,
  action,
  children,
}: {
  icon: LucideIcon;
  title: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="overflow-hidden rounded-xl border border-border/70 bg-card">
      <div className="flex items-center gap-2 border-b border-border/60 bg-muted/20 px-4 py-2.5">
        <Icon className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
        <h3 className="min-w-0 flex-1 truncate text-xs font-medium text-muted-foreground">
          {title}
        </h3>
        {action}
      </div>
      <dl className="divide-y divide-border/60 px-4">{children}</dl>
    </section>
  );
}

/** 信息行：左侧固定标签，右侧自适应内容 */
function InfoRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex items-start gap-4 py-3">
      <dt className="w-[5.5rem] shrink-0 pt-px text-xs leading-5 text-muted-foreground">
        {label}
      </dt>
      <dd className="min-w-0 flex-1 text-sm leading-5">{children}</dd>
    </div>
  );
}

export function AppConfigPanel({
  app,
  detail,
  orchestration,
  description,
  onSave,
  isPending = false,
}: AppConfigPanelProps) {
  const [authConfig, setAuthConfig] = useState<AppAuthConfig>(
    getInitialAuthConfig(detail?.app_configs?.[app?.app_name ?? ""])
  );
  const [dialogOpen, setDialogOpen] = useState(false);
  const [logsOpen, setLogsOpen] = useState(false);
  const [draft, setDraft] = useState<AppAuthConfig>(authConfig);
  const [isVerifying, setIsVerifying] = useState(false);
  const [copiedUrl, setCopiedUrl] = useState(false);
  const verifyMutation = useVerifyAppAuth();
  const wireMutation = useWireOrchestrationInstance();

  useEffect(() => {
    const next = getInitialAuthConfig(detail?.app_configs?.[app?.app_name ?? ""]);
    setAuthConfig(next);
    setDraft(next);
  }, [detail, app]);

  if (!app || !detail) {
    return null;
  }

  const handleOpen = () => {
    setDraft({ ...authConfig });
    setDialogOpen(true);
  };

  const handleVerify = () => {
    if (!app || !isAuthConfigReady(draft)) return;

    setIsVerifying(true);
    verifyMutation.mutate(
      {
        app_name: app.app_name,
        url: draft.url ?? "",
        auth_type: draft.auth_type ?? "none",
        username: draft.username ?? undefined,
        password: draft.password ?? undefined,
        api_key: draft.api_key ?? undefined,
      },
      {
        onSettled: () => setIsVerifying(false),
      }
    );
  };

  const handleSave = () => {
    onSave({
      shared_config: {},
      app_configs: {
        ...detail.app_configs,
        // 与已有条目合并，避免保存认证信息时丢掉自动配置结果与集成记录
        [app.app_name]: {
          ...(detail.app_configs?.[app.app_name] ?? {}),
          ...buildAuthConfig(draft),
        },
      },
    });
    setDialogOpen(false);
  };

  const url = authConfig.url?.trim() || "";
  const isValidUrl =
    url.startsWith("http://") || url.startsWith("https://");

  // 另一种认证方式的凭据仍保存在配置里，提示用户并未丢失
  const inactiveCredentials =
    authConfig.auth_type === "basic"
      ? authConfig.api_key
        ? "已保存 API Key，切换认证方式即可使用"
        : ""
      : authConfig.username && authConfig.password
        ? `已保存账号密码（${authConfig.username}），切换认证方式即可使用`
        : "";

  // 部署后账号自动配置的结果（后端 provision 流程写入）
  const rawEntry = (detail?.app_configs?.[app?.app_name ?? ""] ?? {}) as Record<
    string,
    unknown
  >;
  const provisioned = rawEntry.provisioned === true;
  const provisionError =
    typeof rawEntry.provision_error === "string"
      ? rawEntry.provision_error
      : "";
  const autoUsername =
    typeof rawEntry.username === "string" ? rawEntry.username : "";
  const autoDownloadDir =
    typeof rawEntry.download_dir === "string" ? rawEntry.download_dir : "";

  // MoviePilot 的下载器/媒体服务器自动装配结果
  const integrations = rawEntry.integrations as AppIntegrations | undefined;
  const downloaders = integrations?.downloaders ?? [];
  const mediaservers = integrations?.mediaservers ?? [];
  const integrationRecords = [...downloaders, ...mediaservers];
  const integrationOk = integrationRecords.filter(
    (record) => record.status === "ok"
  ).length;
  const downloaderOk = downloaders.filter(
    (record) => record.status === "ok"
  ).length;
  const mediaserverOk = mediaservers.filter(
    (record) => record.status === "ok"
  ).length;

  // 当前应用在编排组合中的角色（必选、播放器等）
  const roleLabel = getAppRoleLabel(
    orchestration?.app_composition.find(
      (item) => item.app_name === app.app_name
    )
  );

  const handleCopyUrl = async () => {
    if (!url) return;
    try {
      await navigator.clipboard.writeText(url);
      setCopiedUrl(true);
      setTimeout(() => setCopiedUrl(false), 1500);
    } catch {
      // 剪贴板不可用时静默忽略，用户可手动选中复制
    }
  };

  const statusMeta = APP_STATUS_META[app.status] ?? APP_STATUS_META.stopped;

  return (
    <>
      {/* 应用头信息与主操作 */}
      <div className="flex flex-wrap items-start gap-4">
        <AppIcon
          app={
            {
              name: app.app_name,
              display_name: app.display_name,
              icon: app.icon,
            } as App
          }
          className="h-12 w-12 shrink-0 rounded-[24%] object-cover shadow-sm ring-1 ring-foreground/5"
        />

        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <h2 className="truncate text-lg font-semibold tracking-tight text-foreground">
              {app.display_name}
            </h2>
            <span
              className={cn(
                "flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium",
                APP_STATUS_PILL[app.status] ?? APP_STATUS_PILL.stopped
              )}
            >
              <span
                className={cn("h-1.5 w-1.5 rounded-full", statusMeta.dot)}
              />
              {statusMeta.label}
            </span>
          </div>
          <p className="mt-0.5 font-mono text-[11px] text-muted-foreground">
            {app.app_name}
          </p>
          {description && (
            <p className="mt-2 line-clamp-2 max-w-2xl text-sm leading-5 text-muted-foreground">
              {description}
            </p>
          )}
        </div>

        <div className="flex w-full flex-wrap items-center gap-2 sm:w-auto sm:shrink-0">
          <Tooltip>
            <TooltipTrigger
              delay={150}
              render={
                <Button
                  size="icon"
                  variant="outline"
                  onClick={() => setLogsOpen(true)}
                  disabled={!app.project_id}
                  aria-label="编排日志"
                >
                  <ScrollText className="h-4 w-4" />
                </Button>
              }
            />
            <TooltipContent side="bottom" align="end">
              {app.project_id
                ? "编排日志"
                : "该应用未通过 Compose 编排部署，无编排日志"}
            </TooltipContent>
          </Tooltip>
          <Tooltip>
            <TooltipTrigger
              delay={150}
              render={
                <Button
                  size="icon"
                  variant="outline"
                  onClick={handleOpen}
                  disabled={isPending}
                  aria-label="认证设置"
                >
                  <Shield className="h-4 w-4" />
                </Button>
              }
            />
            <TooltipContent side="bottom" align="end">
              认证设置
            </TooltipContent>
          </Tooltip>
          {isValidUrl && (
            <Tooltip>
              <TooltipTrigger
                delay={150}
                render={
                  <Button
                    size="icon"
                    variant="outline"
                    aria-label="打开应用"
                    onClick={() => window.open(url, "_blank", "noreferrer")}
                  >
                    <ExternalLink className="h-4 w-4" />
                  </Button>
                }
              />
              <TooltipContent side="bottom" align="end">
                打开应用
              </TooltipContent>
            </Tooltip>
          )}
        </div>
      </div>

      <Tabs
        key={app.app_name}
        defaultValue="overview"
        className="mt-5 border-0! bg-transparent! shadow-none! outline-none! ring-0!"
      >
        <TabsList className="scrollbar-hidden h-auto max-w-full gap-6 overflow-x-auto rounded-none border-0! bg-transparent! p-0 shadow-none! outline-none! ring-0!">
          <TabsTrigger
            value="overview"
            className="h-9 flex-none gap-2 rounded-none border-0! bg-transparent! px-0 text-sm font-medium text-muted-foreground shadow-none! outline-none! ring-0! transition-colors duration-200 after:hidden hover:text-foreground motion-reduce:transition-none data-active:text-primary"
          >
            <LayoutGrid className="h-4 w-4" aria-hidden="true" />
            概览
          </TabsTrigger>
          {INSPECTABLE_APPS.has(app.app_name) && (
            <TabsTrigger
              value="inspect"
              className="h-9 flex-none gap-2 rounded-none border-0! bg-transparent! px-0 text-sm font-medium text-muted-foreground shadow-none! outline-none! ring-0! transition-colors duration-200 after:hidden hover:text-foreground motion-reduce:transition-none data-active:text-primary"
            >
              <Stethoscope className="h-4 w-4" aria-hidden="true" />
              检测优化
            </TabsTrigger>
          )}
          {app.app_name === "music-tag-web" && (
            <TabsTrigger
              value="organize"
              className="h-9 flex-none gap-2 rounded-none border-0! bg-transparent! px-0 text-sm font-medium text-muted-foreground shadow-none! outline-none! ring-0! transition-colors duration-200 after:hidden hover:text-foreground motion-reduce:transition-none data-active:text-primary"
            >
              <Wand2 className="h-4 w-4" aria-hidden="true" />
              自动整理
            </TabsTrigger>
          )}
          {app.app_name === "reader" && (
            <TabsTrigger
              value="auto-sync"
              className="h-9 flex-none gap-2 rounded-none border-0! bg-transparent! px-0 text-sm font-medium text-muted-foreground shadow-none! outline-none! ring-0! transition-colors duration-200 after:hidden hover:text-foreground motion-reduce:transition-none data-active:text-primary"
            >
              <BookOpen className="h-4 w-4" aria-hidden="true" />
              自动同步
            </TabsTrigger>
          )}
          {app.app_name === "moviepilot" && (
            <TabsTrigger
              value="integration"
              className="h-9 flex-none gap-2 rounded-none border-0! bg-transparent! px-0 text-sm font-medium text-muted-foreground shadow-none! outline-none! ring-0! transition-colors duration-200 after:hidden hover:text-foreground motion-reduce:transition-none data-active:text-primary"
            >
              <Plug className="h-4 w-4" aria-hidden="true" />
              集成
            </TabsTrigger>
          )}
        </TabsList>

        <TabsContent value="overview" className="pt-4">
          <div className="grid gap-4 xl:grid-cols-2">
            <InfoSection icon={Globe} title="访问与认证">
              <InfoRow label="访问地址">
                <div className="flex items-center gap-1.5">
                  <span
                    className={cn(
                      "min-w-0 flex-1 truncate font-mono text-[13px]",
                      url ? "text-foreground" : "text-muted-foreground"
                    )}
                    title={url || undefined}
                  >
                    {url || "未配置"}
                  </span>
                  {isValidUrl ? (
                    <>
                      <button
                        type="button"
                        title="复制地址"
                        aria-label="复制访问地址"
                        onClick={handleCopyUrl}
                        className="shrink-0 rounded-md p-1 text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                      >
                        {copiedUrl ? (
                          <Check className="h-3.5 w-3.5 text-emerald-600" />
                        ) : (
                          <Copy className="h-3.5 w-3.5" />
                        )}
                      </button>
                      <Button
                        size="icon-xs"
                        variant="ghost"
                        className="text-muted-foreground"
                        title="打开应用"
                        aria-label="打开应用"
                        onClick={() => window.open(url, "_blank", "noreferrer")}
                      >
                        <ExternalLink className="h-3.5 w-3.5" />
                      </Button>
                    </>
                  ) : (
                    <Button
                      size="xs"
                      variant="outline"
                      className="h-6 shrink-0"
                      onClick={handleOpen}
                      disabled={isPending}
                    >
                      去配置
                    </Button>
                  )}
                </div>
              </InfoRow>

              <InfoRow label="认证方式">
                <div className="space-y-0.5">
                  <div className="flex flex-wrap items-center gap-x-2 gap-y-0.5">
                    <span>
                      {AUTH_TYPE_LABELS[authConfig.auth_type ?? "none"]}
                    </span>
                    {authConfig.auth_type === "basic" &&
                      authConfig.username && (
                        <span className="text-xs text-muted-foreground">
                          · {authConfig.username}
                        </span>
                      )}
                    {authConfig.auth_type === "api_key" &&
                      authConfig.api_key && (
                        <span className="text-xs text-muted-foreground">
                          · API Key 已配置
                        </span>
                      )}
                  </div>
                  {inactiveCredentials && (
                    <p className="text-xs text-muted-foreground">
                      {inactiveCredentials}
                    </p>
                  )}
                </div>
              </InfoRow>

              <InfoRow label="账号自动配置">
                {provisioned ? (
                  <div className="flex items-center gap-1.5 text-emerald-600 dark:text-emerald-400">
                    <ShieldCheck className="h-4 w-4 shrink-0" />
                    <span className="truncate">
                      已配置{autoUsername ? `（${autoUsername}）` : ""}
                    </span>
                  </div>
                ) : provisionError ? (
                  <div className="text-destructive">
                    <div className="flex items-center gap-1.5">
                      <AlertTriangle className="h-4 w-4 shrink-0" />
                      配置失败
                    </div>
                    <p className="mt-1 line-clamp-2 text-xs text-muted-foreground">
                      {provisionError}
                    </p>
                  </div>
                ) : (
                  <span className="text-muted-foreground">未自动配置</span>
                )}
              </InfoRow>

              {autoDownloadDir && (
                <InfoRow label="下载目录">
                  <span className="truncate font-mono text-[13px]">
                    {autoDownloadDir}
                  </span>
                </InfoRow>
              )}
            </InfoSection>

            <InfoSection
              icon={Layers}
              title="编排与集成"
              action={
                (integrationRecords.length > 0 ||
                  !!provisionError ||
                  app.app_name === "moviepilot" ||
                  app.app_name === "comic-auto" ||
                  app.app_name === "music-dl" ||
                  app.app_name === "music-tag-web" ||
                  app.app_name === "navidrome" ||
                  app.app_name === "reader" ||
                  app.app_name === "ermao-library") && (
                  <Button
                    size="xs"
                    variant="outline"
                    className="h-6 shrink-0 gap-1 bg-card px-2 text-[11px] font-normal"
                    onClick={() => wireMutation.mutate(detail.id)}
                    disabled={wireMutation.isPending}
                  >
                    {wireMutation.isPending ? (
                      <Loader2 className="h-3 w-3 animate-spin" />
                    ) : (
                      <RefreshCw className="h-3 w-3" />
                    )}
                    重新装配
                  </Button>
                )
              }
            >
              {orchestration && (
                <InfoRow label="所属组合">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="truncate">
                      {orchestration.display_name}
                    </span>
                    {roleLabel && (
                      <Badge
                        variant="secondary"
                        className="h-5 text-[11px] font-normal"
                      >
                        {roleLabel}
                      </Badge>
                    )}
                  </div>
                </InfoRow>
              )}

              <InfoRow label="应用集成">
                <div className="space-y-2">
                  {integrationRecords.length > 0 ? (
                    <>
                      <div className="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-0.5">
                        <span
                          className={cn(
                            "font-medium",
                            integrationOk === integrationRecords.length
                              ? "text-emerald-600 dark:text-emerald-400"
                              : "text-amber-600 dark:text-amber-400"
                          )}
                        >
                          {integrationOk}/{integrationRecords.length} 项装配成功
                        </span>
                        <span className="text-xs text-muted-foreground">
                          {downloaders.length > 0 && (
                            <>
                              下载器 {downloaderOk}/{downloaders.length}
                            </>
                          )}
                          {downloaders.length > 0 &&
                            mediaservers.length > 0 &&
                            " · "}
                          {mediaservers.length > 0 && (
                            <>
                              媒体服务器 {mediaserverOk}/{mediaservers.length}
                            </>
                          )}
                        </span>
                      </div>
                      <Progress
                        value={
                          (integrationOk / integrationRecords.length) * 100
                        }
                        className="h-1"
                      />
                    </>
                  ) : (
                    <span className="text-muted-foreground">
                      {app.app_name === "moviepilot" ||
                      app.app_name === "comic-auto"
                        ? "尚未装配"
                        : "无额外集成"}
                    </span>
                  )}
                </div>
              </InfoRow>
            </InfoSection>
          </div>
        </TabsContent>

        {INSPECTABLE_APPS.has(app.app_name) && (
          <TabsContent value="inspect" className="pt-4">
            <AppInspectPanel instanceId={detail.id} app={app} />
          </TabsContent>
        )}

        {app.app_name === "music-tag-web" && detail && (
          <TabsContent value="organize" className="pt-4">
            <MusicAutoOrganizePanel
              instanceId={detail.id}
              appName={app.app_name}
            />
          </TabsContent>
        )}

        {app.app_name === "reader" && detail && (
          <TabsContent value="auto-sync" className="pt-4">
            <ReaderAutoSyncPanel
              instanceId={detail.id}
              appName={app.app_name}
            />
          </TabsContent>
        )}

        {app.app_name === "moviepilot" && (
          <TabsContent value="integration" className="pt-4">
            <MoviePilotIntegrationPanel
              instanceId={detail.id}
              detail={detail}
            />
          </TabsContent>
        )}
      </Tabs>

      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>配置 {app.display_name}</DialogTitle>
          </DialogHeader>

          <div className="space-y-5 py-2">
            <div className="flex items-center gap-3">
              <Label htmlFor="config-url" className="shrink-0">
                访问地址
              </Label>
              <Input
                id="config-url"
                value={draft.url}
                onChange={(e) =>
                  setDraft((prev) => ({ ...prev, url: e.target.value }))
                }
                placeholder="http://192.168.1.1:3000"
              />
            </div>

            <div className="flex items-center gap-3">
              <Label className="shrink-0">认证方式</Label>
              <div className="flex flex-1 flex-wrap gap-2">
                {(
                  [
                    ["none", AUTH_TYPE_LABELS.none],
                    ["basic", AUTH_TYPE_LABELS.basic],
                    ["api_key", AUTH_TYPE_LABELS.api_key],
                  ] as const
                ).map(([type, label]) => (
                  <Button
                    key={type}
                    type="button"
                    variant={draft.auth_type === type ? "default" : "outline"}
                    size="sm"
                    className="h-8 min-w-[5.5rem] flex-1 text-xs sm:flex-none"
                    onClick={() =>
                      setDraft((prev) => ({
                        ...prev,
                        auth_type: type,
                      }))
                    }
                  >
                    {label}
                  </Button>
                ))}
              </div>
            </div>

            {draft.auth_type === "basic" && (
              <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                <div className="space-y-1.5">
                  <Label htmlFor="config-username">用户名</Label>
                  <Input
                    id="config-username"
                    value={draft.username}
                    onChange={(e) =>
                      setDraft((prev) => ({
                        ...prev,
                        username: e.target.value,
                      }))
                    }
                    placeholder="admin"
                    autoComplete="off"
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="config-password">密码</Label>
                  <PasswordInput
                    id="config-password"
                    value={draft.password}
                    onChange={(e) =>
                      setDraft((prev) => ({
                        ...prev,
                        password: e.target.value,
                      }))
                    }
                    placeholder="••••••"
                    autoComplete="off"
                  />
                </div>
              </div>
            )}

            {draft.auth_type === "api_key" && (
              <div className="space-y-1.5">
                <Label htmlFor="config-api-key">API Key</Label>
                <Input
                  id="config-api-key"
                  value={draft.api_key}
                  onChange={(e) =>
                    setDraft((prev) => ({
                      ...prev,
                      api_key: e.target.value,
                    }))
                  }
                  placeholder="请输入 API Key"
                  autoComplete="off"
                />
              </div>
            )}
          </div>

          <div className="-mx-4 -mb-4 flex flex-col-reverse gap-2 rounded-b-xl border-t bg-muted/50 p-2 sm:flex-row sm:items-center sm:justify-end">
            {draft.auth_type !== "none" && (
              <Button
                type="button"
                variant="outline"
                onClick={handleVerify}
                disabled={!isAuthConfigReady(draft) || isVerifying || isPending}
              >
                {isVerifying ? (
                  <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" />
                ) : (
                  <Shield className="mr-1.5 h-3.5 w-3.5" />
                )}
                检测认证
              </Button>
            )}
            <div className="flex flex-col-reverse gap-2 sm:flex-row">
              <Button
                type="button"
                variant="outline"
                onClick={() => setDialogOpen(false)}
                disabled={isPending}
              >
                取消
              </Button>
              <Button
                type="button"
                onClick={handleSave}
                disabled={isPending}
              >
                保存配置
              </Button>
            </div>
          </div>
        </DialogContent>
      </Dialog>

      <StackLogsDialog
        projectId={app.project_id ?? null}
        projectName={app.display_name}
        open={logsOpen}
        onOpenChange={setLogsOpen}
      />
    </>
  );
}
