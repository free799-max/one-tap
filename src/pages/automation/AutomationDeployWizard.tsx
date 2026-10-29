/**
 * 自动化组合部署向导
 *
 * 多步向导：选择应用 -> 共享配置 -> 各应用配置 -> 确认部署。
 */

import { useEffect, useMemo, useRef, useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Card, CardContent } from "@/components/ui/card";
import { SchemaForm } from "@/components/SchemaForm";
import { CodeBlock } from "@/components/ui/code-block";
import { useToast } from "@/components/ui/toast";
import { previewApp } from "@/api/apps";
import {
  useDeployOrchestration,
  useRedeployOrchestrationInstance,
  useUpgradeOrchestrationInstance,
  type AppOrchestration,
  type OrchestrationInstanceDetail,
} from "@/hooks/useOrchestrations";
import { useApps } from "@/hooks/useApps";
import { useDeployTasks } from "@/hooks/useDeployTasks";
import { usePortSuggestions } from "@/hooks/useSettings";
import { cn, sanitizeInstanceName, slugify } from "@/lib/utils";
import {
  applyPortSuggestions,
  countSchemaPorts,
  extractDefaults,
  fillEmptyPasswords,
  mergeSharedConfig,
} from "@/lib/schema-utils";
import { AppIcon } from "../apps/AppIcon";
import {
  AlertTriangle,
  Check,
  ChevronRight,
  ChevronLeft,
  ChevronUp,
  ChevronDown,
  Sparkles,
} from "lucide-react";

type Step = "select" | "shared" | "apps" | "confirm";

interface AutomationDeployWizardProps {
  orchestration: AppOrchestration | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** 提供时进入修复模式：预填组内配置并调用 redeploy 接口 */
  redeployGroup?: OrchestrationInstanceDetail | null;
  /** 提供时进入升级模式：把实例组升级到当前方案版本 */
  upgradeGroup?: OrchestrationInstanceDetail | null;
}

export function AutomationDeployWizard({
  orchestration,
  open,
  onOpenChange,
  redeployGroup,
  upgradeGroup,
}: AutomationDeployWizardProps) {
  const toast = useToast();
  const deployMutation = useDeployOrchestration();
  const redeployMutation = useRedeployOrchestrationInstance();
  const upgradeMutation = useUpgradeOrchestrationInstance();
  const submitMutation = upgradeGroup
    ? upgradeMutation
    : redeployGroup
      ? redeployMutation
      : deployMutation;
  const { startTask } = useDeployTasks();

  const [step, setStep] = useState<Step>("select");
  const [instanceName, setInstanceName] = useState("");
  const [selectedApps, setSelectedApps] = useState<Set<string>>(new Set());
  const [sharedConfig, setSharedConfig] = useState<Record<string, unknown>>({});
  const [appConfigs, setAppConfigs] = useState<
    Record<string, Record<string, unknown>>
  >({});
  const [previews, setPreviews] = useState<
    Record<string, { yaml: string; error: string | null }>
  >({});
  const [previewsLoading, setPreviewsLoading] = useState(false);
  const [expandedPreviews, setExpandedPreviews] = useState<Set<string>>(
    new Set()
  );
  // 已初始化的目标（模板 + 修复/升级组），用于避免后台刷新数据时
  // 重置用户正在填写的表单
  const initializedTargetRef = useRef<string | null>(null);

  // 重置状态当 orchestration 变化
  useEffect(() => {
    if (!open) {
      initializedTargetRef.current = null;
      return;
    }
    if (!orchestration) {
      setStep("select");
      setInstanceName("");
      setSelectedApps(new Set());
      setSharedConfig({});
      setAppConfigs({});
      return;
    }

    const targetKey = `${orchestration.name}|${redeployGroup?.id ?? ""}|${
      upgradeGroup?.id ?? ""
    }`;
    if (initializedTargetRef.current === targetKey) return;
    initializedTargetRef.current = targetKey;

    setStep("select");

    if (upgradeGroup) {
      // 升级模式：沿用实例名称与历史配置，预选组内仍存在的应用，
      // 以及模板中新增的必选应用；新应用的配置随后由默认值补齐。
      const deployedApps = new Set(
        upgradeGroup.apps.map((app) => app.app_name)
      );
      const initialSelected = new Set<string>();
      for (const item of orchestration.app_composition) {
        if (deployedApps.has(item.app_name) || item.relation === "required") {
          initialSelected.add(item.app_name);
        }
      }
      setInstanceName(upgradeGroup.instance_name);
      setSelectedApps(initialSelected);
      setSharedConfig(upgradeGroup.shared_config || {});
      const initialConfigs: Record<string, Record<string, unknown>> = {};
      for (const app of upgradeGroup.apps) {
        initialConfigs[app.app_name] = app.config || {};
      }
      setAppConfigs(initialConfigs);
      return;
    }

    if (redeployGroup) {
      // 修复模式：沿用组内的实例名称与历史配置，
      // 预选只勾选未在运行的应用（error/deploying/stopped 等），运行中的应用不动
      setInstanceName(redeployGroup.instance_name);
      setSelectedApps(
        new Set(
          redeployGroup.apps
            .filter((app) => app.status !== "running")
            .map((app) => app.app_name)
        )
      );
      setSharedConfig(redeployGroup.shared_config || {});
      const initialConfigs: Record<string, Record<string, unknown>> = {};
      for (const app of redeployGroup.apps) {
        initialConfigs[app.app_name] = app.config || {};
      }
      setAppConfigs(initialConfigs);
      return;
    }

    // 默认实例名：优先取可转为 Compose 项目名的显示名；
    // 中文名称会被 slugify 清空，此时改用方案自身的名称（如 music-auto），
    // 避免落到某个组件名（如 music-dl）上。
    const primaryApp = orchestration.app_composition.find(
      (item) => item.relation === "required"
    );
    setInstanceName(
      slugify(orchestration.display_name || "") ||
        slugify(orchestration.name) ||
        primaryApp?.app_name ||
        ""
    );
    setSharedConfig(extractDefaults(orchestration.shared_config_schema));

    const initialSelected = new Set<string>();
    for (const item of orchestration.app_composition) {
      if (item.relation === "required" || item.relation === "suggested") {
        initialSelected.add(item.app_name);
      }
    }

    setSelectedApps(initialSelected);
  }, [open, orchestration, redeployGroup, upgradeGroup]);

  // 获取所有应用商店应用，用于渲染选中应用的配置表单
  const { data: allApps = [], isLoading: appsLoading } = useApps();
  const appDetailsMap = useMemo(() => {
    return Object.fromEntries(allApps.map((app) => [app.name, app]));
  }, [allApps]);

  // 应用商店应用加载完成后，为各应用生成默认配置（含密码自动生成）
  useEffect(() => {
    if (!orchestration || appsLoading || allApps.length === 0) return;

    setAppConfigs((prev) => {
      const next = { ...prev };
      let changed = false;
      for (const item of orchestration.app_composition) {
        if (next[item.app_name] !== undefined) continue;
        const app = appDetailsMap[item.app_name];
        if (!app) continue;
        const defaults = extractDefaults(app.config_schema);
        next[item.app_name] = fillEmptyPasswords(app.config_schema, defaults);
        changed = true;
      }
      // 无新增时返回原引用，避免下游预览 effect 因引用变化而不必要地重跑
      return changed ? next : prev;
    });
  }, [orchestration, appsLoading, allApps, appDetailsMap]);

  const compositionByName = useMemo(() => {
    if (!orchestration) return {};
    return Object.fromEntries(
      orchestration.app_composition.map((item) => [item.app_name, item])
    );
  }, [orchestration]);

  // 升级模式：组内已部署应用与模板应用的差异
  const upgradeDeployedApps = useMemo(
    () => new Set(upgradeGroup?.apps.map((app) => app.app_name) ?? []),
    [upgradeGroup]
  );

  const removedApps = useMemo(() => {
    if (!upgradeGroup || !orchestration) return [];
    return upgradeGroup.apps.filter(
      (app) => !compositionByName[app.app_name]
    );
  }, [upgradeGroup, orchestration, compositionByName]);

  const templateChangelog = (
    upgradeGroup?.template_changelog ??
    orchestration?.changelog ??
    ""
  ).trim();

  const selectedAppList = useMemo(
    () => Array.from(selectedApps),
    [selectedApps]
  );

  // 端口顺序分配：统计选中应用的端口总数，向后端请求建议端口。
  // 统计口径与后端 _validate_and_prepare 一致（单端口字段 + ports 数组项）。
  const totalPorts = useMemo(() => {
    let total = 0;
    for (const appName of selectedAppList) {
      const app = appDetailsMap[appName];
      if (!app) continue;
      total += countSchemaPorts(
        app.config_schema,
        extractDefaults(app.config_schema)
      );
    }
    return total;
  }, [selectedAppList, appDetailsMap]);

  // 修复/升级模式沿用旧配置中的端口，不再请求端口建议
  const { data: portSuggestions } = usePortSuggestions(
    redeployGroup || upgradeGroup ? 0 : totalPorts
  );

  // 记录已预填过建议端口的应用，避免覆盖用户手动修改
  const portsFilledRef = useRef<Set<string>>(new Set());

  // 组合或选中集合变化时重新分配（选中顺序变化会导致后续应用的端口偏移）
  const selectedKey = selectedAppList.join(",");
  useEffect(() => {
    portsFilledRef.current = new Set();
  }, [orchestration, selectedKey]);

  // 建议端口到达后，按选中顺序为尚未填充的应用预填端口
  useEffect(() => {
    if (redeployGroup || upgradeGroup) return;
    if (!orchestration || !portSuggestions || portSuggestions.length === 0) {
      return;
    }

    const updates: Record<string, Record<string, unknown>> = {};
    let offset = 0;
    for (const appName of selectedAppList) {
      const app = appDetailsMap[appName];
      if (!app) continue;
      const schema = app.config_schema;
      const config = appConfigs[appName];
      if (config === undefined) {
        // 配置尚未初始化：仅推进偏移，待初始化后的下一轮再填充
        offset += countSchemaPorts(schema, extractDefaults(schema));
        continue;
      }
      if (portsFilledRef.current.has(appName)) {
        offset += countSchemaPorts(schema, config);
        continue;
      }
      const result = applyPortSuggestions(schema, config, portSuggestions, offset);
      updates[appName] = result.config;
      offset = result.nextOffset;
    }

    const names = Object.keys(updates);
    if (names.length === 0) return;
    for (const name of names) {
      portsFilledRef.current.add(name);
    }
    setAppConfigs((prev) => ({ ...prev, ...updates }));
  }, [orchestration, redeployGroup, upgradeGroup, portSuggestions, selectedAppList, appDetailsMap, appConfigs]);

  // 进入确认步骤后，并发加载各选中的应用 Compose YAML 预览
  useEffect(() => {
    if (step !== "confirm" || !orchestration) return;

    const projectName = slugify(instanceName);
    if (!projectName || selectedAppList.length === 0) return;

    const controller = new AbortController();
    // 标记当前批次是否已被取消（依赖变化触发的 cleanup），
    // 避免被 abort 的旧批次在网络返回前写回空结果并提前关闭 loading
    let cancelled = false;
    setPreviewsLoading(true);
    setPreviews({});

    Promise.all(
      selectedAppList.map(async (appName) => {
        const app = appDetailsMap[appName];
        if (!app) {
          return { appName, yaml: "", error: "未找到应用信息" };
        }
        const config = appConfigs[appName] || {};
        const mergedConfig = mergeSharedConfig(
          sharedConfig,
          config,
          orchestration.shared_config_schema as Record<string, unknown>,
          app.config_schema as Record<string, unknown>,
          appName
        );
        const subInstanceName = `${projectName}-${appName}`;
        try {
          const res = await previewApp(
            appName,
            {
              instance_name: subInstanceName,
              config: mergedConfig,
            },
            controller.signal
          );
          return {
            appName,
            yaml: res.yaml || "",
            error: res.error || null,
          };
        } catch (err) {
          const errorObj = err as { code?: string; displayMessage?: string; message?: string } | undefined;
          if (errorObj?.code === "ERR_CANCELED") {
            return { appName, yaml: "", error: null };
          }
          return {
            appName,
            yaml: "",
            error: errorObj?.displayMessage || errorObj?.message || "预览加载失败",
          };
        }
      })
    )
      .then((results) => {
        if (cancelled) return;
        const next: Record<string, { yaml: string; error: string | null }> = {};
        for (const r of results) {
          next[r.appName] = { yaml: r.yaml, error: r.error };
        }
        setPreviews(next);
      })
      .finally(() => {
        if (cancelled) return;
        setPreviewsLoading(false);
      });

    return () => {
      cancelled = true;
      controller.abort();
    };
  }, [step, instanceName, selectedAppList, appDetailsMap, appConfigs, sharedConfig, orchestration]);

  if (!orchestration) return null;

  const toggleApp = (appName: string) => {
    const item = compositionByName[appName];
    if (!item) return;
    // 修复模式下按需选择要重部署的应用，必选应用也可取消/重新勾选
    if (item.relation === "required" && !redeployGroup) return;

    setSelectedApps((prev) => {
      const next = new Set(prev);
      if (next.has(appName)) {
        next.delete(appName);
      } else {
        // 互斥检测：如果新选中的应用与已选中的应用互斥，则取消已选中的
        const conflicts = new Set(item.conflict_with || []);
        for (const selected of next) {
          if (conflicts.has(selected)) {
            next.delete(selected);
          }
        }
        // 同时检查已选中的应用是否把新应用列为互斥
        for (const selected of next) {
          const selectedItem = compositionByName[selected];
          if (selectedItem?.conflict_with?.includes(appName)) {
            next.delete(selected);
          }
        }
        next.add(appName);
      }
      return next;
    });
  };

  const validateSharedConfig = (): boolean => {
    const volumes = sharedConfig.volumes;
    if (!Array.isArray(volumes)) return true;
    for (let index = 0; index < volumes.length; index += 1) {
      const row = volumes[index];
      if (!row || typeof row !== "object") continue;
      const entry = row as Record<string, unknown>;
      const hostPath = String(entry.host_path ?? "").trim();
      const containerPath = String(entry.container_path ?? "").trim();
      if (!hostPath) {
        toast.error(`存储空间第 ${index + 1} 项缺少本地路径`);
        return false;
      }
      if (!containerPath) {
        toast.error(`存储空间第 ${index + 1} 项缺少容器路径`);
        return false;
      }
    }
    return true;
  };

  const validateStep = (): boolean => {
    if (step === "select") {
      const projectName = slugify(instanceName);
      if (!projectName) {
        toast.error("请输入有效的实例名称");
        return false;
      }
      if (redeployGroup) {
        // 修复模式按需选择应用，不强制包含必选应用，但至少要选一个
        if (selectedApps.size === 0) {
          toast.error("请至少选择一个要修复的应用");
          return false;
        }
      } else {
        // 校验必选应用都已选中
        for (const item of orchestration.app_composition) {
          if (item.relation === "required" && !selectedApps.has(item.app_name)) {
            toast.error(`${item.app_name} 为必选应用`);
            return false;
          }
        }
      }
      return true;
    }
    if (step === "shared") {
      return validateSharedConfig();
    }
    return true;
  };

  const handleNext = () => {
    if (!validateStep()) return;

    const steps: Step[] = ["select", "shared", "apps", "confirm"];
    const idx = steps.indexOf(step);
    if (idx < steps.length - 1) {
      setStep(steps[idx + 1]);
    }
  };

  const handleBack = () => {
    const steps: Step[] = ["select", "shared", "apps", "confirm"];
    const idx = steps.indexOf(step);
    if (idx > 0) {
      setStep(steps[idx - 1]);
    }
  };

  const handleDeploy = () => {
    const projectName = slugify(instanceName);
    if (!projectName) {
      toast.error("请输入有效的实例名称");
      return;
    }
    if (!validateSharedConfig()) return;

    const filteredAppConfigs: Record<string, Record<string, unknown>> = {};
    for (const appName of selectedApps) {
      filteredAppConfigs[appName] = appConfigs[appName] || {};
    }

    const payload = {
      instance_name: projectName,
      selected_apps: Array.from(selectedApps),
      app_configs: filteredAppConfigs,
      shared_config: sharedConfig,
    };
    const options = {
      onSuccess: (response: { task_ids: string[] }) => {
        if (!upgradeGroup) {
          toast.success(redeployGroup ? "修复已启动" : "组合部署已启动");
        }
        for (const taskId of response.task_ids) {
          startTask(taskId);
        }
        onOpenChange(false);
      },
    };

    if (upgradeGroup) {
      upgradeMutation.mutate({ id: upgradeGroup.id, data: payload }, options);
    } else if (redeployGroup) {
      redeployMutation.mutate({ id: redeployGroup.id, data: payload }, options);
    } else {
      deployMutation.mutate({ name: orchestration.name, data: payload }, options);
    }
  };

  const togglePreview = (appName: string) => {
    setExpandedPreviews((prev) => {
      const next = new Set(prev);
      if (next.has(appName)) next.delete(appName);
      else next.add(appName);
      return next;
    });
  };

  const renderSelectStep = () => (
    <div className="space-y-4">
      {upgradeGroup && templateChangelog && (
        <div className="rounded-xl border border-primary/25 bg-primary/5 px-3 py-2.5">
          <div className="flex items-center gap-1.5 text-xs font-medium text-primary">
            <Sparkles className="h-3.5 w-3.5" />
            本次更新内容
            {upgradeGroup.template_version && (
              <span className="font-normal text-muted-foreground">
                （目标版本 v{upgradeGroup.template_version}）
              </span>
            )}
          </div>
          <div className="mt-1.5 max-h-40 overflow-y-auto whitespace-pre-wrap text-xs leading-relaxed text-muted-foreground">
            {templateChangelog}
          </div>
        </div>
      )}

      <div className="space-y-1">
        <Label htmlFor="instance-name">实例名称</Label>
        <Input
          id="instance-name"
          value={instanceName}
          onChange={(e) => setInstanceName(sanitizeInstanceName(e.target.value))}
          onBlur={() => setInstanceName(slugify(instanceName))}
          placeholder="如：my-media-stack"
          disabled={!!upgradeGroup}
        />
        <p className="text-xs text-muted-foreground">
          {upgradeGroup
            ? "升级时实例名称保持不变"
            : "仅支持小写字母、数字、- 和 _，不能包含中文或特殊字符"}
        </p>
      </div>

      {upgradeGroup && removedApps.length > 0 && (
        <div className="rounded-xl border border-destructive/30 bg-destructive/5 px-3 py-2.5">
          <div className="flex items-center gap-1.5 text-xs font-medium text-destructive">
            <AlertTriangle className="h-3.5 w-3.5 shrink-0" />
            以下应用在新方案中已移除，升级时将被删除
          </div>
          <div className="mt-1.5 flex flex-wrap gap-1.5">
            {removedApps.map((app) => (
              <Badge
                key={app.id}
                variant="destructive"
                className="font-normal"
              >
                {app.display_name}
              </Badge>
            ))}
          </div>
        </div>
      )}

      <div className="space-y-2">
        <Label>{upgradeGroup ? "确认要升级的应用" : "选择要部署的应用"}</Label>
        <div className="grid grid-cols-2 gap-3">
          {orchestration.app_composition.map((item) => {
            const app = appDetailsMap[item.app_name];
            const selected = selectedApps.has(item.app_name);
            const disabled = item.relation === "required" && !redeployGroup;
            const isNewApp =
              !!upgradeGroup && !upgradeDeployedApps.has(item.app_name);
            const isDeployed =
              !!upgradeGroup && upgradeDeployedApps.has(item.app_name);
            return (
              <Card
                key={item.app_name}
                onClick={() => !disabled && toggleApp(item.app_name)}
                className={cn(
                  "rounded-xl p-3 border transition-all",
                  selected
                    ? "border-primary bg-primary/5 ring-1 ring-primary/20"
                    : "border-border bg-white hover:bg-muted/30",
                  disabled ? "opacity-80 cursor-default" : "cursor-pointer"
                )}
              >
                <CardContent className="p-0 flex items-center gap-3">
                  {app ? (
                    <AppIcon
                      app={app}
                      className="h-12 w-12 rounded-[22%] bg-white border border-border/60 shrink-0"
                    />
                  ) : (
                    <div className="h-12 w-12 rounded-[22%] bg-gradient-to-br from-primary/15 to-primary/5 flex items-center justify-center text-2xl shrink-0">
                      🚀
                    </div>
                  )}

                  <div className="min-w-0 flex-1">
                    <div className="flex flex-wrap items-center gap-1.5">
                      <span className="text-sm font-medium truncate">
                        {app?.display_name || item.app_name}
                      </span>
                      <Badge
                        variant={
                          item.relation === "required"
                            ? "default"
                            : item.relation === "suggested"
                            ? "secondary"
                            : "outline"
                        }
                        className="text-[10px] px-1.5 py-0 h-4 shrink-0"
                      >
                        {item.relation === "required"
                          ? "必选"
                          : item.relation === "suggested"
                          ? "推荐"
                          : item.relation === "conflicting"
                          ? "互斥"
                          : "可选"}
                      </Badge>
                      {isNewApp && (
                        <Badge className="h-4 shrink-0 bg-emerald-500/15 px-1.5 py-0 text-[10px] font-medium text-emerald-600 dark:text-emerald-400">
                          新增
                        </Badge>
                      )}
                      {isDeployed && (
                        <Badge
                          variant="outline"
                          className="h-4 shrink-0 px-1.5 py-0 text-[10px] font-normal text-muted-foreground"
                        >
                          已部署
                        </Badge>
                      )}
                    </div>
                    {app?.description && (
                      <p className="text-xs text-muted-foreground line-clamp-1 mt-0.5">
                        {app.description}
                      </p>
                    )}
                    {item.group && (
                      <p className="text-[11px] text-muted-foreground mt-0.5">
                        分组：{item.group}
                      </p>
                    )}
                  </div>

                  <input
                    type="checkbox"
                    checked={selected}
                    disabled={disabled}
                    readOnly
                    className="h-4 w-4 rounded border-input shrink-0"
                  />
                </CardContent>
              </Card>
            );
          })}
        </div>
      </div>
    </div>
  );

  const renderSharedStep = () => {
    const sharedProperties = (
      orchestration.shared_config_schema as {
        properties?: Record<string, { default?: unknown }>;
      }
    )?.properties;
    const hasSchema = Object.keys(sharedProperties || {}).length > 0;
    // 仅单个共享目录的组合（如漫画）用实例名作为默认子路径，
    // 多目录组合（如影视的 media/downloads）仍由用户分别填写
    const volumeDefaults = sharedProperties?.volumes?.default;
    const defaultDirectorySubPath =
      Array.isArray(volumeDefaults) && volumeDefaults.length === 1
        ? slugify(instanceName)
        : undefined;

    return (
      <div className="space-y-4">
        <div className="text-sm text-muted-foreground">
          以下配置会共享给所有选中的应用，可在各应用配置中覆盖
        </div>
        {hasSchema ? (
          <SchemaForm
            schema={orchestration.shared_config_schema as never}
            data={sharedConfig}
            onChange={setSharedConfig}
            defaultDirectorySubPath={defaultDirectorySubPath}
            relativeToAppDataDir={false}
          />
        ) : (
          <div className="rounded-xl border border-dashed p-8 text-center">
            <div className="text-sm text-muted-foreground">当前组合没有公共配置项</div>
          </div>
        )}
      </div>
    );
  };

  const renderAppsStep = () => (
    <div className="space-y-4 max-h-[50vh] overflow-y-auto pr-1">
      {appsLoading ? (
        <div className="text-sm text-muted-foreground py-4">加载应用配置中...</div>
      ) : (
        selectedAppList.map((appName) => {
          const app = appDetailsMap[appName];
          if (!app) {
            return (
              <div key={appName} className="text-sm text-muted-foreground py-4">
                未找到应用 {appName}
              </div>
            );
          }

          return (
            <div key={appName} className="rounded-xl border bg-card p-4 shadow-sm">
              <h4 className="text-sm font-semibold mb-3">{app.display_name}</h4>
              <SchemaForm
                schema={app.config_schema as never}
                data={appConfigs[appName] || {}}
                onChange={(data) =>
                  setAppConfigs((prev) => ({ ...prev, [appName]: data }))
                }
                image={app.image || undefined}
              />
            </div>
          );
        })
      )}
    </div>
  );

  const renderConfirmStep = () => (
    <div className="space-y-4 pr-1">
      <div className="rounded-xl border border-border bg-card p-4 shadow-sm space-y-4">
        <div className="grid grid-cols-2 gap-4">
          <div className="space-y-1">
            <div className="text-xs text-muted-foreground">实例名称</div>
            <div className="text-sm font-medium truncate">{instanceName}</div>
          </div>
          <div className="space-y-1">
            <div className="text-xs text-muted-foreground">
              {upgradeGroup ? "升级应用数" : "部署应用数"}
            </div>
            <div className="text-sm font-medium">{selectedApps.size} 个</div>
          </div>
        </div>

        {upgradeGroup && removedApps.length > 0 && (
          <div className="rounded-lg border border-destructive/25 bg-destructive/5 px-3 py-2 text-xs leading-relaxed text-destructive">
            升级时将删除已从新方案移除的应用：
            {removedApps.map((app) => app.display_name).join("、")}
          </div>
        )}

        <div className="space-y-2">
          <div className="text-xs text-muted-foreground">应用列表</div>
          <div className="flex flex-wrap items-start gap-4">
            {Array.from(selectedApps).map((appName) => {
              const app = appDetailsMap[appName];
              return (
                <div
                  key={appName}
                  className="flex flex-col items-center gap-1.5 w-16"
                >
                  {app ? (
                    <AppIcon
                      app={app}
                      className="h-12 w-12 rounded-[22%] bg-white border border-border/60 shadow-sm"
                    />
                  ) : (
                    <div className="h-12 w-12 rounded-[22%] bg-gradient-to-br from-primary/15 to-primary/5 flex items-center justify-center text-xl">
                      🚀
                    </div>
                  )}
                  <span className="text-xs font-medium text-center line-clamp-2 leading-tight">
                    {app?.display_name || appName}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      </div>

      <div className="space-y-2">
        <h4 className="text-sm font-semibold">docker-compose.yml 预览</h4>
        {previewsLoading ? (
          <div className="text-sm text-muted-foreground py-4">加载预览中…</div>
        ) : (
          selectedAppList.map((appName) => {
            const app = appDetailsMap[appName];
            const preview = previews[appName];
            const expanded = expandedPreviews.has(appName);
            return (
              <div
                key={appName}
                className="rounded-lg border border-border overflow-hidden"
              >
                <button
                  type="button"
                  onClick={() => togglePreview(appName)}
                  className="flex w-full items-center justify-between px-4 py-2.5 text-left text-sm font-medium hover:bg-muted/50 transition-colors"
                >
                  <span>{app?.display_name || appName}</span>
                  {expanded ? (
                    <ChevronUp className="h-4 w-4 text-muted-foreground" />
                  ) : (
                    <ChevronDown className="h-4 w-4 text-muted-foreground" />
                  )}
                </button>
                {expanded && (
                  <div className="border-t">
                    {preview?.error ? (
                      <div className="p-4 text-xs text-destructive">
                        预览生成失败：{preview.error}
                      </div>
                    ) : (
                      <CodeBlock
                        code={preview?.yaml || ""}
                        emptyText="暂无预览内容"
                        showCopy={false}
                        language="yaml"
                      />
                    )}
                  </div>
                )}
              </div>
            );
          })
        )}
      </div>
    </div>
  );

  const modeLabel = upgradeGroup ? "升级" : redeployGroup ? "修复" : "部署";

  const stepTitles: Record<Step, string> = {
    select: "选择应用",
    shared: "共享配置",
    apps: "应用配置",
    confirm: upgradeGroup ? "确认升级" : "确认部署",
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-4xl max-h-[90vh] flex flex-col overflow-hidden">
        <DialogHeader className="flex-shrink-0">
          <DialogTitle>
            {modeLabel} {orchestration.display_name}
          </DialogTitle>
          <DialogDescription>
            {upgradeGroup
              ? "将实例组升级到当前方案版本，未勾选的可选应用将不会被升级"
              : orchestration.description || "选择应用并填写配置后一键部署"}
          </DialogDescription>
        </DialogHeader>

        <div className="flex items-center gap-2 py-2 flex-shrink-0">
          {(["select", "shared", "apps", "confirm"] as Step[]).map(
            (s, index) => (
              <div key={s} className="flex items-center gap-2">
                <div
                  className={`flex items-center justify-center h-6 w-6 rounded-full text-xs font-medium ${
                    step === s
                      ? "bg-primary text-primary-foreground"
                      : index < ["select", "shared", "apps", "confirm"].indexOf(step)
                      ? "bg-primary/10 text-primary"
                      : "bg-muted text-muted-foreground"
                  }`}
                >
                  {index < ["select", "shared", "apps", "confirm"].indexOf(step) ? (
                    <Check className="h-3.5 w-3.5" />
                  ) : (
                    index + 1
                  )}
                </div>
                <span
                  className={`text-xs ${
                    step === s ? "text-primary font-medium" : "text-muted-foreground"
                  }`}
                >
                  {stepTitles[s]}
                </span>
                {index < 3 && (
                  <ChevronRight className="h-3.5 w-3.5 text-muted-foreground" />
                )}
              </div>
            )
          )}
        </div>

        <div className="overflow-y-auto max-h-[calc(90vh-12rem)] py-2 scrollbar-thin -mr-4 pr-2">
          {step === "select" && renderSelectStep()}
          {step === "shared" && renderSharedStep()}
          {step === "apps" && renderAppsStep()}
          {step === "confirm" && renderConfirmStep()}
        </div>

        <div className="flex justify-between gap-2 mt-4 flex-shrink-0">
          <Button
            variant="outline"
            onClick={() => onOpenChange(false)}
            disabled={submitMutation.isPending}
          >
            取消
          </Button>
          <div className="flex gap-2">
            {step !== "select" && (
              <Button variant="outline" onClick={handleBack}>
                <ChevronLeft className="h-4 w-4 mr-1" />
                上一步
              </Button>
            )}
            {step !== "confirm" ? (
              <Button onClick={handleNext}>
                下一步
                <ChevronRight className="h-4 w-4 ml-1" />
              </Button>
            ) : (
              <Button
                onClick={handleDeploy}
                disabled={submitMutation.isPending}
              >
                {submitMutation.isPending
                  ? upgradeGroup
                    ? "升级中..."
                    : "部署中..."
                  : upgradeGroup
                    ? "确认升级"
                    : redeployGroup
                      ? "确认修复"
                      : "确认部署"}
              </Button>
            )}
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
