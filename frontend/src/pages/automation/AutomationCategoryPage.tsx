/**
 * 自动化分类详情页面
 *
 * 同一分类下可以有多套方案（如「影视自动化（本地）」与「影视自动化（网盘）」）。
 * 未部署时并列展示方案卡片；已部署时按方案切换，左侧实例树、右侧应用详情。
 */

import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { AppIcon } from "../apps/AppIcon";
import { AutomationDeployWizard } from "./AutomationDeployWizard";
import { AutomationImportDialog } from "./AutomationImportDialog";
import { AutomationRestoreDialog } from "./AutomationRestoreDialog";
import { AutomationUpdateEventsDialog } from "./AutomationUpdateEventsDialog";
import { AutomationUpgradeDialog } from "./AutomationUpgradeDialog";
import { AutomationPlanSection } from "./AutomationPlanSection";
import { OrchestrationVersionBadge } from "./OrchestrationVersionBadge";
import { OrchestrationInstanceManager } from "./OrchestrationInstanceManager";
import { AppConfigPanel } from "./AppConfigPanel";
import { RELATION_LABELS } from "./composition-meta";
import {
  useOrchestrations,
  useOrchestrationInstances,
  useOrchestrationInstanceDetail,
  useUpdateOrchestrationInstance,
  useDeleteOrchestrationInstance,
  type AppCompositionItem,
  type AppOrchestration,
  type OrchestrationInstanceApp,
} from "@/hooks/useOrchestrations";
import { useApps, type App } from "@/hooks/useApps";
import { useDeployTasks } from "@/hooks/useDeployTasks";
import { cn, formatVersion } from "@/lib/utils";
import { getAutomationCategoryMeta } from "@/lib/automation-categories";
import {
  ArrowRight,
  ArrowUpCircle,
  Boxes,
  History,
  Import,
  Loader2,
  Rocket,
  RotateCcw,
} from "lucide-react";

/** 流程阶段：同一 group 的应用会被合并为同一阶段（如多个播放器） */
interface Stage {
  group: string | null;
  items: AppCompositionItem[];
}

/**
 * 将应用组合按 group 合并为流程阶段：
 * 连续且 group 相同的应用归为同一阶段（如多个播放器同属"播放"环节），
 * 其余应用各自独立成阶段，按数据顺序构成自动化流程。
 */
function buildStages(composition: AppCompositionItem[]): Stage[] {
  const stages: Stage[] = [];
  for (const item of composition) {
    const last = stages[stages.length - 1];
    if (item.group && last && last.group === item.group) {
      last.items.push(item);
    } else {
      stages.push({ group: item.group ?? null, items: [item] });
    }
  }
  return stages;
}

/**
 * 判断同组应用是否互斥（二选一）：
 * 仅当组内每个应用都通过 conflict_with 与其它同组应用互斥时才成立。
 * group 本身只是分组标签，不代表互斥；可多选的分组（如播放器）不应标"二选一"。
 */
function isMutuallyExclusive(items: AppCompositionItem[]): boolean {
  if (items.length < 2) return false;
  return items.every((a) =>
    items.every(
      (b) => a === b || (a.conflict_with?.includes(b.app_name) ?? false)
    )
  );
}

function AppNode({
  item,
  appMap,
}: {
  item: AppCompositionItem;
  appMap: Record<string, App>;
}) {
  const app = appMap[item.app_name];
  return (
    <div className="flex w-20 flex-col items-center gap-2">
      {app ? (
        <AppIcon
          app={app}
          className="h-14 w-14 rounded-[22%] object-cover shadow-md"
        />
      ) : (
        <div className="flex h-14 w-14 items-center justify-center rounded-[22%] bg-gradient-to-br from-primary/15 to-primary/5 text-2xl shadow-md">
          🚀
        </div>
      )}
      <span className="line-clamp-2 text-center text-xs font-medium leading-tight text-foreground/80">
        {app?.display_name || item.app_name}
      </span>
    </div>
  );
}

function StageFlow({
  composition,
  appMap,
}: {
  composition: AppCompositionItem[];
  appMap: Record<string, App>;
}) {
  const stages = buildStages(composition);

  return (
    <div className="flex flex-wrap items-start justify-center gap-x-3 gap-y-6 sm:gap-x-4">
      {stages.map((stage, index) => {
        const isRequired = stage.items.every(
          (item) => item.relation === "required"
        );
        const mutex = isMutuallyExclusive(stage.items);
        const caption = stage.group
          ? `${stage.group} · ${mutex ? "二选一" : isRequired ? "必选" : "可选"}`
          : RELATION_LABELS[stage.items[0]?.relation] ?? "可选";

        return (
          <Fragment key={`${stage.group ?? "single"}-${index}`}>
            {index > 0 && (
              <ArrowRight className="mt-5 h-4 w-4 shrink-0 self-start text-muted-foreground/40" />
            )}
            <div className="flex flex-col items-center gap-2.5">
              <div className="flex items-start gap-2.5">
                {stage.items.map((item, itemIndex) => (
                  <Fragment key={item.app_name}>
                    {itemIndex > 0 && (
                      <span className="mt-5 self-start text-[11px] font-medium text-muted-foreground/60">
                        或
                      </span>
                    )}
                    <AppNode item={item} appMap={appMap} />
                  </Fragment>
                ))}
              </div>
              <span
                className={`text-[11px] leading-none ${
                  isRequired ? "text-primary/70" : "text-muted-foreground"
                }`}
              >
                {caption}
              </span>
            </div>
          </Fragment>
        );
      })}
    </div>
  );
}

export function AutomationCategoryPage() {
  const { category } = useParams<{ category: string }>();
  const { data: orchestrations = [], isLoading } = useOrchestrations(category);
  const { data: instances = [] } = useOrchestrationInstances(category);
  const { data: allApps = [] } = useApps();
  const appMap = Object.fromEntries(allApps.map((app) => [app.name, app]));
  const [deployOrchestration, setDeployOrchestration] =
    useState<AppOrchestration | null>(null);
  const [importOrchestration, setImportOrchestration] =
    useState<AppOrchestration | null>(null);
  const [restoreOrchestration, setRestoreOrchestration] =
    useState<AppOrchestration | null>(null);
  const [redeployId, setRedeployId] = useState<number | null>(null);
  const [upgradeId, setUpgradeId] = useState<number | null>(null);
  const [updateEventsOpen, setUpdateEventsOpen] = useState(false);
  const [selectedInstanceId, setSelectedInstanceId] = useState<number | null>(
    null
  );
  const [selectedApp, setSelectedApp] =
    useState<OrchestrationInstanceApp | null>(null);
  const [displayInstanceId, setDisplayInstanceId] = useState<number | null>(
    null
  );
  const [isSwitching, setIsSwitching] = useState(false);
  const [activeRecipeName, setActiveRecipeName] = useState<string | null>(null);
  const switchTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const meta = getAutomationCategoryMeta(category);
  const label = meta.label;

  const instanceCountByRecipe = Object.fromEntries(
    orchestrations.map((orchestration) => [
      orchestration.name,
      instances.filter(
        (instance) => instance.orchestration_name === orchestration.name
      ).length,
    ])
  );

  // 默认选中：优先沿用当前方案；否则取第一个有实例的方案，再退回第一个方案
  useEffect(() => {
    if (orchestrations.length === 0) {
      setActiveRecipeName(null);
      return;
    }
    setActiveRecipeName((prev) => {
      if (prev && orchestrations.some((item) => item.name === prev)) {
        return prev;
      }
      const firstWithInstances = instances.find((instance) =>
        orchestrations.some(
          (item) => item.name === instance.orchestration_name
        )
      );
      return firstWithInstances?.orchestration_name ?? orchestrations[0].name;
    });
  }, [orchestrations, instances]);

  const activeOrchestration =
    orchestrations.find((item) => item.name === activeRecipeName) ??
    orchestrations[0] ??
    null;
  const activeInstances = activeOrchestration
    ? instances.filter(
        (instance) => instance.orchestration_name === activeOrchestration.name
      )
    : [];
  const hasInstances = activeInstances.length > 0;

  // 切换方案后，选中该方案下的第一个实例
  useEffect(() => {
    setSelectedInstanceId((prev) => {
      if (prev && activeInstances.some((instance) => instance.id === prev)) {
        return prev;
      }
      return activeInstances[0]?.id ?? null;
    });
  }, [activeOrchestration?.name, instances]);

  useEffect(() => {
    setDisplayInstanceId(selectedInstanceId);
  }, [selectedInstanceId]);

  useEffect(() => {
    return () => {
      if (switchTimerRef.current) {
        clearTimeout(switchTimerRef.current);
      }
    };
  }, []);

  const { data: selectedDetail } = useOrchestrationInstanceDetail(
    displayInstanceId
  );
  const currentOrchestration =
    orchestrations.find(
      (item) => item.name === selectedDetail?.orchestration_name
    ) ?? null;
  const updateMutation = useUpdateOrchestrationInstance();
  const deleteMutation = useDeleteOrchestrationInstance();
  const { startTask } = useDeployTasks();

  // 详情加载后默认选中第一个应用；实例切换或应用被移除时自动修正选中项
  useEffect(() => {
    const apps = selectedDetail?.apps ?? [];
    setSelectedApp((prev) => {
      if (apps.length === 0) return null;
      if (prev && apps.some((app) => app.id === prev.id)) return prev;
      return apps[0];
    });
  }, [selectedDetail]);

  const switchInstance = (id: number) => {
    if (id === selectedInstanceId || isSwitching) return;
    setIsSwitching(true);
    if (switchTimerRef.current) {
      clearTimeout(switchTimerRef.current);
    }
    switchTimerRef.current = setTimeout(() => {
      setSelectedInstanceId(id);
      setIsSwitching(false);
    }, 120);
  };

  const switchRecipe = (name: string) => {
    if (name === activeRecipeName) return;
    setActiveRecipeName(name);
    const first = instances.find(
      (instance) => instance.orchestration_name === name
    );
    setSelectedInstanceId(first?.id ?? null);
  };

  // 修复/升级：按实例组加载详情与对应模板
  const { data: redeployDetail } = useOrchestrationInstanceDetail(redeployId);
  const redeployOrchestration = redeployDetail
    ? (orchestrations.find(
        (item) => item.name === redeployDetail.orchestration_name
      ) ?? null)
    : null;

  const { data: upgradeDetail } = useOrchestrationInstanceDetail(upgradeId);
  const upgradeOrchestration = upgradeDetail
    ? (orchestrations.find(
        (item) => item.name === upgradeDetail.orchestration_name
      ) ?? null)
    : null;

  const apps = selectedDetail?.apps ?? [];

  // 同分类其他方案里已在运行的应用：部署时提示可复用，避免重复安装播放器/挂载
  const siblingApps = useMemo(
    () =>
      new Set(
        instances
          .filter(
            (instance) =>
              instance.orchestration_name !== activeOrchestration?.name
          )
          .flatMap((instance) =>
            instance.apps
              .filter((app) => app.status === "running")
              .map((app) => app.app_name)
          )
      ),
    [instances, activeOrchestration?.name]
  );

  const renderPlanFlow = (orchestration: AppOrchestration) => (
    <StageFlow composition={orchestration.app_composition} appMap={appMap} />
  );

  // 未部署时的展示：单个方案在页面中间展示，多个方案纵向排列。
  const renderPlanList = () => {
    const single = orchestrations.length === 1;
    return (
      <div className="flex flex-1 flex-col overflow-y-auto px-4 py-8 sm:px-6 sm:py-10">
        <div
          className={cn(
            "mx-auto w-full max-w-3xl",
            single
              ? "pt-16"
              : "flex flex-col divide-y divide-border/60"
          )}
        >
          {orchestrations.map((orchestration) => (
            <AutomationPlanSection
              key={orchestration.name}
              variant={single ? "single" : "stacked"}
              orchestration={orchestration}
              instanceCount={instanceCountByRecipe[orchestration.name] ?? 0}
              onDeploy={setDeployOrchestration}
              onImport={setImportOrchestration}
              flow={renderPlanFlow(orchestration)}
              className={single ? "" : "py-9 first:pt-0 last:pb-0"}
            />
          ))}
        </div>
      </div>
    );
  };

  // 已部署其他方案、当前方案还没有实例时，同样按流程展示该方案。
  const renderActivePlanEmpty = () =>
    activeOrchestration && (
      <div className="flex flex-1 flex-col overflow-y-auto px-4 py-8 sm:px-6 sm:py-10">
        <div className="mx-auto w-full max-w-3xl">
          <AutomationPlanSection
            orchestration={activeOrchestration}
            instanceCount={0}
            onDeploy={setDeployOrchestration}
            onImport={setImportOrchestration}
            flow={renderPlanFlow(activeOrchestration)}
          />
        </div>
      </div>
    );

  return (
    <div className="flex h-full flex-col space-y-4">
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-border bg-card shadow-sm">
        <div className="flex flex-wrap items-center gap-3 border-b border-border/70 px-4 py-4 sm:px-5">
          <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-primary/15 to-primary/5 ring-1 ring-primary/10">
            <meta.icon className="h-5 w-5 text-primary" />
          </div>
          <div className="min-w-0 flex-1">
            <div className="flex flex-wrap items-center gap-2">
              <h1 className="text-xl font-semibold tracking-tight text-foreground">
                {`${label}自动化`}
              </h1>
              {orchestrations.length === 1 && activeOrchestration && (
                <OrchestrationVersionBadge
                  orchestration={activeOrchestration}
                  className="h-5 text-[11px]"
                />
              )}
            </div>
            <p className="mt-0.5 text-sm text-muted-foreground">
              {meta.description}
            </p>
          </div>

          {instances.length > 0 && orchestrations.length > 1 && (
            <div className="flex shrink-0 flex-wrap items-center gap-1.5">
              {orchestrations.map((orchestration) => {
                const active = orchestration.name === activeOrchestration?.name;
                const count = instanceCountByRecipe[orchestration.name] ?? 0;
                return (
                  <button
                    key={orchestration.name}
                    type="button"
                    onClick={() => switchRecipe(orchestration.name)}
                    className={cn(
                      "flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-xs transition-colors",
                      active
                        ? "border-primary/40 bg-primary/10 font-medium text-primary"
                        : "border-border bg-background text-muted-foreground hover:bg-muted/50"
                    )}
                  >
                    {orchestration.display_name}
                    <span className="tabular-nums opacity-70">
                      {formatVersion(orchestration.version)}
                    </span>
                    {count > 0 && (
                      <Badge
                        variant="secondary"
                        className="h-4 px-1.5 py-0 text-[10px] font-normal"
                      >
                        {count}
                      </Badge>
                    )}
                  </button>
                );
              })}
            </div>
          )}

          <div className="flex shrink-0 items-center gap-2">
            <Button
              size="sm"
              variant="outline"
              onClick={() => setUpdateEventsOpen(true)}
              className="gap-1.5"
            >
              <History className="h-3.5 w-3.5" />
              更新记录
            </Button>
            {hasInstances && activeOrchestration && (
              <>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => setRestoreOrchestration(activeOrchestration)}
                  className="gap-1.5"
                >
                  <RotateCcw className="h-3.5 w-3.5" />
                  恢复
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => setImportOrchestration(activeOrchestration)}
                  className="gap-1.5"
                >
                  <Import className="h-3.5 w-3.5" />
                  导入
                </Button>
                <Button
                  size="sm"
                  onClick={() => setDeployOrchestration(activeOrchestration)}
                  className="gap-1.5"
                >
                  <Rocket className="h-3.5 w-3.5" />
                  部署
                </Button>
              </>
            )}
          </div>
        </div>

        {isLoading ? (
          <div className="flex flex-1 items-center justify-center">
            <span className="flex items-center gap-2 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              加载中…
            </span>
          </div>
        ) : orchestrations.length === 0 ? (
          <div className="flex flex-1 flex-col items-center justify-center gap-2 px-6 text-center">
            <Boxes className="h-6 w-6 text-muted-foreground/50" />
            <p className="text-sm text-muted-foreground">
              暂无 {label} 自动化组合模板
            </p>
          </div>
        ) : instances.length === 0 ? (
          renderPlanList()
        ) : !hasInstances ? (
          renderActivePlanEmpty()
        ) : (
          <div
            key={displayInstanceId ?? "empty"}
            className={cn(
              "flex min-h-0 flex-1 flex-col transition-opacity duration-150 ease-out lg:flex-row",
              isSwitching ? "opacity-0" : "opacity-100"
            )}
          >
            <aside className="shrink-0 border-b border-border/70 bg-muted/20 lg:flex lg:w-[288px] lg:flex-col lg:border-b-0 lg:border-r">
              <div className="min-h-0 flex-1 overflow-y-auto px-2 py-3">
                <OrchestrationInstanceManager
                  instances={activeInstances}
                  selectedId={selectedInstanceId}
                  selectedAppId={selectedApp?.id ?? null}
                  appMap={appMap}
                  composition={currentOrchestration?.app_composition}
                  onSelect={switchInstance}
                  onSelectApp={(app) =>
                    setSelectedApp(
                      selectedDetail?.apps.find((item) => item.id === app.id) ??
                        app
                    )
                  }
                  onRename={(id, name) =>
                    updateMutation.mutate({
                      id,
                      data: { instance_name: name },
                    })
                  }
                  onDelete={(id) =>
                    deleteMutation.mutate(id, {
                      onSuccess: (response) => startTask(response.task_id),
                    })
                  }
                  onRedeploy={(id) => setRedeployId(id)}
                  onUpgrade={(id) => setUpgradeId(id)}
                  isPending={
                    updateMutation.isPending || deleteMutation.isPending
                  }
                />
              </div>
            </aside>

            <section className="min-w-0 flex-1 overflow-y-auto">
              {selectedDetail?.upgrade_available && (
                <div className="m-4 mb-0 flex flex-wrap items-center gap-3 rounded-xl border border-amber-500/30 bg-amber-500/5 px-3.5 py-2.5">
                  <ArrowUpCircle className="h-4 w-4 shrink-0 text-amber-600 dark:text-amber-400" />
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium text-foreground">
                      方案有新版本
                    </p>
                    <p className="text-xs text-muted-foreground">
                      {formatVersion(selectedDetail.orchestration_version)}
                      <span className="mx-1 text-muted-foreground/60">→</span>
                      {selectedDetail.template_version ||
                      currentOrchestration?.version
                        ? formatVersion(
                            selectedDetail.template_version ||
                              currentOrchestration?.version
                          )
                        : "最新"}
                    </p>
                    {selectedDetail.template_changelog?.trim() && (
                      <p className="mt-1 line-clamp-2 whitespace-pre-wrap text-xs leading-relaxed text-muted-foreground/80">
                        {selectedDetail.template_changelog}
                      </p>
                    )}
                  </div>
                  <Button
                    size="sm"
                    className="shrink-0 gap-1.5"
                    onClick={() => setUpgradeId(selectedDetail.id)}
                  >
                    <ArrowUpCircle className="h-3.5 w-3.5" />
                    升级方案
                  </Button>
                </div>
              )}
              {!selectedDetail || (apps.length > 0 && !selectedApp) ? (
                <div className="flex min-h-[320px] items-center justify-center gap-2 text-sm text-muted-foreground">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  正在加载应用…
                </div>
              ) : selectedApp ? (
                <div className="p-4 sm:p-5">
                  <AppConfigPanel
                    app={selectedApp}
                    detail={selectedDetail}
                    orchestration={currentOrchestration}
                    description={appMap[selectedApp.app_name]?.description}
                    onSave={(payload) => {
                      if (!selectedInstanceId) return;
                      updateMutation.mutate({
                        id: selectedInstanceId,
                        data: payload,
                      });
                    }}
                    isPending={updateMutation.isPending}
                  />
                </div>
              ) : (
                <div className="flex min-h-[320px] flex-col items-center justify-center gap-2 px-6 text-center">
                  <Boxes className="h-6 w-6 text-muted-foreground/50" />
                  <p className="text-sm text-muted-foreground">
                    该实例组暂无应用
                  </p>
                  <p className="text-xs text-muted-foreground/70">
                    可在左侧导入现有容器，或重新部署该组合。
                  </p>
                </div>
              )}
            </section>
          </div>
        )}
      </div>

      <AutomationDeployWizard
        orchestration={deployOrchestration}
        open={!!deployOrchestration}
        onOpenChange={(open) => !open && setDeployOrchestration(null)}
        siblingApps={siblingApps}
      />

      <AutomationDeployWizard
        orchestration={redeployOrchestration}
        open={!!redeployOrchestration}
        onOpenChange={(open) => !open && setRedeployId(null)}
        redeployGroup={redeployDetail ?? null}
        siblingApps={siblingApps}
      />

      <AutomationUpgradeDialog
        orchestration={upgradeOrchestration}
        group={upgradeDetail ?? null}
        open={!!upgradeDetail}
        onOpenChange={(open) => !open && setUpgradeId(null)}
      />

      <AutomationUpdateEventsDialog
        open={updateEventsOpen}
        onOpenChange={setUpdateEventsOpen}
        orchestrations={orchestrations}
      />

      <AutomationImportDialog
        orchestration={importOrchestration}
        open={!!importOrchestration}
        onOpenChange={(open) => !open && setImportOrchestration(null)}
      />

      <AutomationRestoreDialog
        orchestration={restoreOrchestration}
        open={!!restoreOrchestration}
        onOpenChange={(open) => !open && setRestoreOrchestration(null)}
      />
    </div>
  );
}
