/**
 * 自动化编排导入已有 Docker 部署弹窗
 *
 * 扫描运行中的 Docker 容器，按编排组合规则匹配应用，
 * 允许用户为每个应用填写访问地址与认证信息后导入。
 */

import { useEffect, useMemo, useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { PasswordInput } from "@/components/ui/password-input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import {
  useImportCandidates,
  useImportOrchestration,
  useVerifyAppAuth,
  type AppOrchestration,
  type AppCompositionItem,
  type ContainerMatch,
  type OrchestrationImportAppConfig,
} from "@/hooks/useOrchestrations";
import { AlertCircle, Check, Loader2, ShieldCheck } from "lucide-react";
import { cn, sanitizeInstanceName, slugify } from "@/lib/utils";
import { isAuthConfigReady } from "./auth-config-utils";

interface AutomationImportDialogProps {
  orchestration: AppOrchestration | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

const RELATION_LABELS: Record<AppCompositionItem["relation"], string> = {
  required: "必选",
  suggested: "推荐",
  optional: "可选",
  conflicting: "互斥",
};

const RELATION_VARIANTS: Record<
  AppCompositionItem["relation"],
  "default" | "secondary" | "destructive" | "outline"
> = {
  required: "destructive",
  suggested: "default",
  optional: "secondary",
  conflicting: "outline",
};

const AUTH_TYPE_LABELS: Record<
  NonNullable<OrchestrationImportAppConfig["auth_type"]>,
  string
> = {
  none: "无认证",
  basic: "账号密码",
  api_key: "API Key",
};

interface GroupInfo {
  group: string | null;
  items: AppCompositionItem[];
  mutex: boolean;
}

function buildGroups(composition: AppCompositionItem[]): GroupInfo[] {
  const groups: GroupInfo[] = [];
  for (const item of composition) {
    const last = groups[groups.length - 1];
    if (item.group && last && last.group === item.group) {
      last.items.push(item);
    } else {
      groups.push({ group: item.group ?? null, items: [item], mutex: false });
    }
  }
  // 同一组计算互斥标记
  for (const group of groups) {
    group.mutex = isMutuallyExclusive(group.items);
  }
  return groups;
}

function isMutuallyExclusive(items: AppCompositionItem[]): boolean {
  if (items.length < 2) return false;
  return items.every((a) =>
    items.every(
      (b) => a === b || (a.conflict_with?.includes(b.app_name) ?? false)
    )
  );
}

function AppIconDisplay({
  appName,
  icon,
  displayName,
  className,
}: {
  appName: string;
  icon: string | null;
  displayName: string;
  className?: string;
}) {
  const [failed, setFailed] = useState(false);
  const src = icon?.startsWith("http") ? icon : `/icons/apps/${appName}.svg`;

  if (failed) {
    return (
      <div
        className={cn(
          "flex items-center justify-center rounded-[22%] bg-gradient-to-br from-primary/15 to-primary/5 text-lg",
          className
        )}
      >
        🚀
      </div>
    );
  }

  return (
    <img
      src={src}
      alt={displayName}
      className={cn("rounded-[22%] object-cover", className)}
      onError={() => setFailed(true)}
    />
  );
}

function CandidateSelect({
  candidates,
  value,
  onChange,
}: {
  candidates: ContainerMatch[];
  value: string | null;
  onChange: (containerId: string) => void;
}) {
  if (candidates.length <= 1) {
    const candidate = candidates[0];
    if (!candidate) return null;
    return (
      <div className="text-xs text-muted-foreground space-y-0.5">
        {candidate.suggested_url && (
          <div className="flex items-center justify-between gap-2">
            <div className="text-primary truncate">{candidate.suggested_url}</div>
            {candidate.host_port && (
              <div className="shrink-0">
                宿主机{candidate.host_port} -&gt; 容器{candidate.container_port}
              </div>
            )}
          </div>
        )}
        {!candidate.suggested_url && candidate.host_port && (
          <div>
            宿主机{candidate.host_port} -&gt; 容器{candidate.container_port}
          </div>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-1">
      <select
        value={value ?? ""}
        onChange={(e) => onChange(e.target.value)}
        className="h-8 w-full rounded-lg border border-input bg-transparent px-2.5 text-sm outline-none focus-visible:border-foreground/60"
      >
        <option value="">选择容器</option>
        {candidates.map((c) => (
          <option key={c.container_id} value={c.container_id}>
            {c.container_name} ({c.suggested_url || c.container_port})
          </option>
        ))}
      </select>
    </div>
  );
}

export function AutomationImportDialog({
  orchestration,
  open,
  onOpenChange,
}: AutomationImportDialogProps) {
  const { data: candidates = [], isLoading } = useImportCandidates(
    orchestration?.name ?? ""
  );
  const importMutation = useImportOrchestration();
  const verifyMutation = useVerifyAppAuth();

  const [instanceName, setInstanceName] = useState("");
  const [selectedApps, setSelectedApps] = useState<Set<string>>(new Set());
  const [selectedContainer, setSelectedContainer] = useState<
    Record<string, string>
  >({});
  const [appConfigs, setAppConfigs] = useState<
    Record<string, OrchestrationImportAppConfig>
  >({});
  const [verifyingApp, setVerifyingApp] = useState<string | null>(null);

  const candidateMap = useMemo(
    () => Object.fromEntries(candidates.map((c) => [c.app_name, c])),
    [candidates]
  );

  const groups = useMemo(
    () => buildGroups(orchestration?.app_composition ?? []),
    [orchestration]
  );

  // 弹窗打开时初始化默认选中状态
  useEffect(() => {
    if (!open || !orchestration) return;

    const initialSelected = new Set<string>();
    const initialContainer: Record<string, string> = {};
    const initialConfigs: Record<string, OrchestrationImportAppConfig> = {};

    for (const item of orchestration.app_composition) {
      const candidate = candidateMap[item.app_name];
      if (item.relation === "required" && candidate?.candidates.length) {
        initialSelected.add(item.app_name);
      }
      if (candidate?.candidates.length) {
        initialContainer[item.app_name] = candidate.candidates[0].container_id;
        initialConfigs[item.app_name] = {
          auth_type: "none",
          url: candidate.candidates[0].suggested_url ?? "",
        };
      }
    }

    setInstanceName(slugify(orchestration.display_name || ""));
    setSelectedApps(initialSelected);
    setSelectedContainer(initialContainer);
    setAppConfigs(initialConfigs);
  }, [open, orchestration, candidateMap]);

  const toggleApp = (appName: string, groupInfo: GroupInfo) => {
    setSelectedApps((prev) => {
      const next = new Set(prev);
      if (groupInfo.mutex) {
        // 互斥组内单选
        if (next.has(appName)) {
          next.delete(appName);
        } else {
          for (const item of groupInfo.items) {
            next.delete(item.app_name);
          }
          next.add(appName);
        }
      } else {
        if (next.has(appName)) {
          next.delete(appName);
        } else {
          next.add(appName);
        }
      }
      return next;
    });
  };

  const updateConfig = (
    appName: string,
    patch: Partial<OrchestrationImportAppConfig>
  ) => {
    setAppConfigs((prev) => ({
      ...prev,
      [appName]: { ...(prev[appName] ?? {}), ...patch },
    }));
  };

  const handleVerify = (appName: string) => {
    const config = appConfigs[appName];
    if (!config || !isAuthConfigReady(config)) return;

    setVerifyingApp(appName);
    verifyMutation.mutate(
      {
        app_name: appName,
        url: config.url ?? "",
        auth_type: config.auth_type ?? "none",
        username: config.username ?? undefined,
        password: config.password ?? undefined,
        api_key: config.api_key ?? undefined,
      },
      {
        onSettled: () => setVerifyingApp(null),
      }
    );
  };

  const handleSubmit = async () => {
    if (!orchestration) return;

    const projectName = slugify(instanceName);
    if (!projectName) return;

    try {
      await importMutation.mutateAsync({
        name: orchestration.name,
        data: {
          instance_name: projectName,
          selected_apps: Array.from(selectedApps),
          app_configs: Object.fromEntries(
            Array.from(selectedApps).map((name) => [
              name,
              appConfigs[name] ?? {},
            ])
          ),
          shared_config: {},
        },
      });
      onOpenChange(false);
    } catch {
      // 错误提示由 useImportOrchestration 的 onError 统一处理，弹窗保持打开
    }
  };

  const canSubmit =
    slugify(instanceName).length > 0 &&
    selectedApps.size > 0 &&
    !importMutation.isPending;

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg max-h-[90vh] overflow-hidden p-0">
        <DialogHeader className="px-4 pt-4">
          <DialogTitle>导入已有应用组合</DialogTitle>
        </DialogHeader>

        <div className="px-4 overflow-y-auto max-h-[60vh] space-y-4">
          <div className="flex items-center gap-3">
            <Label htmlFor="instance-name" className="shrink-0">实例名称</Label>
            <Input
              id="instance-name"
              value={instanceName}
              onChange={(e) => setInstanceName(sanitizeInstanceName(e.target.value))}
              onBlur={() => setInstanceName(slugify(instanceName))}
              placeholder="例如：my-media-stack"
            />
          </div>

          {isLoading ? (
            <div className="flex items-center justify-center gap-2 py-8 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              正在扫描运行中的容器…
            </div>
          ) : (
            groups.map((group, groupIndex) => (
              <div key={group.group ?? `group-${groupIndex}`} className="space-y-2">
                {group.group && (
                  <div className="text-xs font-medium text-muted-foreground">
                    {group.group}
                    {group.mutex ? " · 二选一" : ""}
                  </div>
                )}
                {group.items.map((item) => {
                  const candidate = candidateMap[item.app_name];
                  const selected = selectedApps.has(item.app_name);
                  const disabled = !candidate?.matched;
                  const selectedCandidate = candidate?.candidates.find(
                    (c) => c.container_id === selectedContainer[item.app_name]
                  ) ?? candidate?.candidates[0];

                  return (
                    <div
                      key={item.app_name}
                      className={cn(
                        "rounded-xl border p-3 transition-colors",
                        selected
                          ? "border-primary/50 bg-primary/[0.02]"
                          : "border-border"
                      )}
                    >
                      <div className="space-y-3">
                        <div className="flex items-center gap-3">
                          <AppIconDisplay
                            appName={item.app_name}
                            icon={candidate?.icon ?? null}
                            displayName={candidate?.display_name ?? item.app_name}
                            className="h-10 w-10 shrink-0"
                          />
                          <div className="flex-1 min-w-0">
                            <div className="flex items-center justify-between gap-2">
                              <div className="font-medium text-sm truncate">
                                {candidate?.display_name ?? item.app_name}
                              </div>
                              <Badge variant={RELATION_VARIANTS[item.relation]}>
                                {RELATION_LABELS[item.relation]}
                              </Badge>
                            </div>
                            {selectedCandidate && (
                              <div className="text-xs text-muted-foreground truncate mt-0.5">
                                {selectedCandidate.image}
                              </div>
                            )}
                          </div>
                          <Button
                            type="button"
                            variant={selected ? "default" : "outline"}
                            size="icon-sm"
                            className="shrink-0"
                            disabled={disabled}
                            onClick={() => toggleApp(item.app_name, group)}
                          >
                            {selected ? (
                              <Check className="h-3.5 w-3.5" />
                            ) : (
                              <span className="text-xs">选</span>
                            )}
                          </Button>
                        </div>

                        <div className="mt-2 pl-[52px] space-y-2">
                          <div>
                            {candidate?.matched ? (
                              <CandidateSelect
                                candidates={candidate.candidates}
                                value={selectedContainer[item.app_name] ?? null}
                                onChange={(containerId) => {
                                  setSelectedContainer((prev) => ({
                                    ...prev,
                                    [item.app_name]: containerId,
                                  }));
                                  const match = candidate.candidates.find(
                                    (c) => c.container_id === containerId
                                  );
                                  if (match?.suggested_url) {
                                    updateConfig(item.app_name, {
                                      url: match.suggested_url,
                                      selected_container_id: containerId,
                                    });
                                  } else {
                                    updateConfig(item.app_name, {
                                      selected_container_id: containerId,
                                    });
                                  }
                                }}
                              />
                            ) : (
                              <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                                <AlertCircle className="h-3.5 w-3.5" />
                                未检测到运行中的容器
                              </div>
                            )}
                          </div>

                          <div className="flex items-center gap-3">
                            <Label className="text-xs shrink-0">访问地址</Label>
                            <Input
                              value={appConfigs[item.app_name]?.url ?? ""}
                              onChange={(e) =>
                                updateConfig(item.app_name, {
                                  url: e.target.value,
                                })
                              }
                              placeholder="http://localhost:8080"
                              className="h-7 text-xs"
                            />
                          </div>

                          <div className="flex items-center gap-2">
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
                                variant={
                                  (appConfigs[item.app_name]?.auth_type ?? "none") === type
                                    ? "default"
                                    : "outline"
                                }
                                size="sm"
                                className="h-7 text-xs"
                                onClick={() =>
                                  updateConfig(item.app_name, {
                                    auth_type: type,
                                  })
                                }
                              >
                                {label}
                              </Button>
                            ))}
                          </div>

                          {appConfigs[item.app_name]?.auth_type === "basic" && (
                            <div className="grid grid-cols-[1fr_1fr_auto] gap-2 items-end">
                              <div className="space-y-1">
                                <Label className="text-xs">用户名</Label>
                                <Input
                                  value={appConfigs[item.app_name]?.username ?? ""}
                                  onChange={(e) =>
                                    updateConfig(item.app_name, {
                                      username: e.target.value,
                                    })
                                  }
                                  placeholder="admin"
                                  className="h-7 text-xs"
                                />
                              </div>
                              <div className="space-y-1">
                                <Label className="text-xs">密码</Label>
                                <PasswordInput
                                  value={appConfigs[item.app_name]?.password ?? ""}
                                  onChange={(e) =>
                                    updateConfig(item.app_name, {
                                      password: e.target.value,
                                    })
                                  }
                                  placeholder="••••••"
                                  className="h-7 text-xs"
                                />
                              </div>
                              <Button
                                type="button"
                                variant="outline"
                                size="sm"
                                className="h-7 text-xs"
                                disabled={
                                  !isAuthConfigReady(appConfigs[item.app_name] ?? {}) ||
                                  verifyingApp === item.app_name
                                }
                                onClick={() => handleVerify(item.app_name)}
                              >
                                {verifyingApp === item.app_name ? (
                                  <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                                ) : (
                                  <ShieldCheck className="mr-1 h-3 w-3" />
                                )}
                                检测认证
                              </Button>
                            </div>
                          )}

                          {appConfigs[item.app_name]?.auth_type === "api_key" && (
                            <div className="flex items-end gap-2">
                              <div className="flex-1 space-y-1">
                                <Label className="text-xs">API Key</Label>
                                <Input
                                  value={appConfigs[item.app_name]?.api_key ?? ""}
                                  onChange={(e) =>
                                    updateConfig(item.app_name, {
                                      api_key: e.target.value,
                                    })
                                  }
                                  placeholder="可选"
                                  className="h-7 text-xs"
                                />
                              </div>
                              <Button
                                type="button"
                                variant="outline"
                                size="sm"
                                className="h-7 text-xs"
                                disabled={
                                  !isAuthConfigReady(appConfigs[item.app_name] ?? {}) ||
                                  verifyingApp === item.app_name
                                }
                                onClick={() => handleVerify(item.app_name)}
                              >
                                {verifyingApp === item.app_name ? (
                                  <Loader2 className="mr-1 h-3 w-3 animate-spin" />
                                ) : (
                                  <ShieldCheck className="mr-1 h-3 w-3" />
                                )}
                                检测认证
                              </Button>
                            </div>
                          )}

                          {appConfigs[item.app_name]?.auth_type === "none" && (
                            <div className="h-7" />
                          )}
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>
            ))
          )}
        </div>

        <DialogFooter className="!-mx-0 !-mb-0 px-4 py-3">
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={importMutation.isPending}
          >
            取消
          </Button>
          <Button
            onClick={handleSubmit}
            disabled={!canSubmit || importMutation.isPending}
          >
            {importMutation.isPending ? (
              <>
                <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />
                导入中…
              </>
            ) : (
              "确认导入"
            )}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
