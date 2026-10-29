/**
 * 自动化分类详情页面
 *
 * 未部署时：以「应用组合模板 + 流程展示」居中展示分类下的自动化流程；
 * 已部署时：以「左侧应用列表 + 右侧详情面板」管理实例组内的应用。
 */

import { Fragment, useEffect, useRef, useState } from "react";
import { useParams } from "react-router-dom";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Button } from "@/components/ui/button";
import { AppIcon } from "../apps/AppIcon";
import { AutomationDeployWizard } from "./AutomationDeployWizard";
import { AutomationImportDialog } from "./AutomationImportDialog";
import { AutomationRestoreDialog } from "./AutomationRestoreDialog";
import { AutomationUpdateEventsDialog } from "./AutomationUpdateEventsDialog";
import { AutomationUpgradeDialog } from "./AutomationUpgradeDialog";
import { OrchestrationInstanceManager } from "./OrchestrationInstanceManager";
import { OrchestrationVersionBadge } from "./OrchestrationVersionBadge";
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
import { cn } from "@/lib/utils";
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
    <div className="flex flex-col items-center gap-2 w-20">
      {app ? (
        <AppIcon
          app={app}
          className="h-14 w-14 rounded-[22%] object-cover shadow-md"
        />
      ) : (
        <div className="h-14 w-14 rounded-[22%] bg-gradient-to-br from-primary/15 to-primary/5 flex items-center justify-center text-2xl shadow-md">
          🚀
        </div>
      )}
      <span className="text-xs font-medium text-foreground/80 text-center leading-tight line-clamp-2">
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
              <ArrowRight className="h-4 w-4 shrink-0 self-start mt-5 text-muted-foreground/40" />
            )}
            <div className="flex flex-col items-center gap-2.5">
              <div className="flex items-start gap-2.5">
                {stage.items.map((item, itemIndex) => (
                  <Fragment key={item.app_name}>
                    {itemIndex > 0 && (
                      <span className="self-start mt-5 text-[11px] font-medium text-muted-foreground/60">
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

function OrchestrationShowcase({
  label,
  category,
  orchestrations,
  appMap,
}: {
  label: string;
  category: string;
  orchestrations: AppOrchestration[];
  appMap: Record<string, App>;
}) {
  const config = getAutomationCategoryMeta(category);
  const Icon = config.icon;
  const multiple = orchestrations.length > 1;
  const isEmpty = orchestrations.length === 0;

  return (
    <div className="relative flex-1 flex flex-col px-6 pt-20 pb-8 overflow-y-auto">
      <div className="absolute inset-0 pointer-events-none">
        <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[360px] h-[360px] rounded-full bg-primary/[0.04] blur-3xl" />
      </div>

      <div className="relative z-10 mx-auto mt-16 flex flex-col items-center text-center max-w-3xl w-full">
        <div className="h-20 w-20 rounded-2xl bg-gradient-to-br from-primary/15 to-primary/5 flex items-center justify-center mb-5 shadow-sm ring-1 ring-primary/10">
          <Icon className="h-10 w-10 text-primary" />
        </div>

        <h3 className="text-lg font-semibold text-foreground">{label}自动化</h3>
        <p className="text-sm text-muted-foreground mt-2 leading-relaxed max-w-lg">
          {isEmpty ? `暂无 ${label} 自动化组合模板` : config.description}
        </p>

        {!isEmpty && (
          <div className="mt-8 w-full flex flex-col items-center gap-10">
            {orchestrations.map((orchestration) => (
              <div
                key={orchestration.name}
                className="group w-full flex flex-col items-center gap-5"
              >
                {multiple && (
                  <div className="space-y-1">
                    <h4 className="text-sm font-semibold text-foreground">
                      {orchestration.display_name}
                    </h4>
                    {orchestration.description && (
                      <p className="text-xs text-muted-foreground max-w-md">
                        {orchestration.description}
                      </p>
                    )}
                  </div>
                )}

                <StageFlow
                  composition={orchestration.app_composition}
                  appMap={appMap}
                />
              </div>
            ))}
          </div>
        )}
      </div>
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
  const switchTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const meta = getAutomationCategoryMeta(category);
  const label = meta.label;

  useEffect(() => {
    setSelectedInstanceId((prev) => {
      const stillExists = instances.some((i) => i.id === prev);
      if (stillExists) return prev;
      return instances.length > 0 ? instances[0].id : null;
    });
  }, [instances]);

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
      (o) => o.name === selectedDetail?.orchestration_name
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

  // 修复：加载目标实例组详情，并找到对应的编排模板
  const { data: redeployDetail } = useOrchestrationInstanceDetail(redeployId);
  const redeployOrchestration = redeployDetail
    ? (orchestrations.find((o) => o.name === redeployDetail.orchestration_name) ??
      null)
    : null;

  // 升级：同样按实例组加载详情与对应模板
  const { data: upgradeDetail } = useOrchestrationInstanceDetail(upgradeId);
  const upgradeOrchestration = upgradeDetail
    ? (orchestrations.find((o) => o.name === upgradeDetail.orchestration_name) ??
      null)
    : null;

  const hasInstances = instances.length > 0;
  const apps = selectedDetail?.apps ?? [];

  return (
    <div className="h-full flex flex-col space-y-4">
      {hasInstances && orchestrations.length > 0 ? (
        <div className="flex min-h-0 flex-1 flex-col overflow-hidden rounded-xl border border-border bg-card shadow-sm">
          {/* 页面标题与操作 */}
          <div className="flex flex-wrap items-center gap-3 border-b border-border/70 px-4 py-4 sm:px-5">
            <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-primary/15 to-primary/5 ring-1 ring-primary/10">
              <meta.icon className="h-5 w-5 text-primary" />
            </div>
            <div className="min-w-0 flex-1">
              <h1 className="text-xl font-semibold tracking-tight text-foreground">
                {`${label}自动化`}
              </h1>
              <p className="mt-0.5 text-sm text-muted-foreground">
                {meta.description}
              </p>
            </div>
            <div className="flex shrink-0 flex-wrap items-center gap-1.5">
              {orchestrations.map((orchestration) => (
                <OrchestrationVersionBadge
                  key={orchestration.name}
                  orchestration={orchestration}
                  showName={orchestrations.length > 1}
                />
              ))}
            </div>
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
              <Button
                size="sm"
                variant="outline"
                onClick={() => setRestoreOrchestration(orchestrations[0])}
                className="gap-1.5"
              >
                <RotateCcw className="h-3.5 w-3.5" />
                恢复
              </Button>
              <Button
                size="sm"
                variant="outline"
                onClick={() => setImportOrchestration(orchestrations[0])}
                className="gap-1.5"
              >
                <Import className="h-3.5 w-3.5" />
                导入
              </Button>
              <Button
                size="sm"
                onClick={() => setDeployOrchestration(orchestrations[0])}
                className="gap-1.5"
              >
                <Rocket className="h-3.5 w-3.5" />
                部署
              </Button>
            </div>
          </div>

          {/* 主从布局：左侧实例组树，右侧应用详情 */}
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
                  instances={instances}
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
                      onSuccess: (response) =>
                        startTask(response.task_id),
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
                      v{selectedDetail.orchestration_version || "未知"}
                      <span className="mx-1 text-muted-foreground/60">→</span>
                      v
                      {selectedDetail.template_version ||
                        currentOrchestration?.version ||
                        "最新"}
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
        </div>
      ) : (
        <Card className="rounded-xl flex-1 flex flex-col">
          <CardHeader className="flex flex-row items-start justify-between gap-3 py-1 px-4 pb-0">
            <div className="flex flex-wrap items-center gap-2">
              <CardTitle className="text-base font-medium">
                {label}自动化
              </CardTitle>
              {orchestrations.map((orchestration) => (
                <OrchestrationVersionBadge
                  key={orchestration.name}
                  orchestration={orchestration}
                  showName={orchestrations.length > 1}
                />
              ))}
            </div>
            {orchestrations.length > 0 && (
              <div className="flex flex-wrap items-center justify-end gap-2">
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => setUpdateEventsOpen(true)}
                  className="gap-1.5 rounded-lg h-9"
                >
                  <History className="h-4 w-4" />
                  更新记录
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => setRestoreOrchestration(orchestrations[0])}
                  className="gap-1.5 rounded-lg h-9"
                >
                  <RotateCcw className="h-4 w-4" />
                  恢复
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => setImportOrchestration(orchestrations[0])}
                  className="gap-1.5 rounded-lg h-9"
                >
                  <Import className="h-4 w-4" />
                  导入
                </Button>
                <Button
                  size="sm"
                  variant="outline"
                  onClick={() => setDeployOrchestration(orchestrations[0])}
                  className="gap-1.5 rounded-lg h-9"
                >
                  <Rocket className="h-4 w-4" />
                  部署
                </Button>
              </div>
            )}
          </CardHeader>

          <CardContent className="flex-1 flex flex-col px-4 pb-4 pt-0 overflow-y-auto">
            {isLoading ? (
              <div className="text-sm text-muted-foreground py-12 text-center">
                加载中…
              </div>
            ) : (
              <OrchestrationShowcase
                label={label}
                category={category || "media"}
                orchestrations={orchestrations}
                appMap={appMap}
              />
            )}
          </CardContent>
        </Card>
      )}

      <AutomationDeployWizard
        orchestration={deployOrchestration}
        open={!!deployOrchestration}
        onOpenChange={(open) => !open && setDeployOrchestration(null)}
      />

      <AutomationDeployWizard
        orchestration={redeployOrchestration}
        open={!!redeployOrchestration}
        onOpenChange={(open) => !open && setRedeployId(null)}
        redeployGroup={redeployDetail ?? null}
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
