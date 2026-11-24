/**
 * 外部应用导入提醒弹窗
 *
 * 登录后扫描尚未接管的外部 Compose 项目与独立容器，采用左右分栏：
 * - 左侧选择对象，右侧展示镜像、端口与挂载状态；
 * - 未挂载时只针对当前对象给出挂载配置，避免重复堆叠警告；
 * - 已导入的对象不再出现，全部处理完后弹窗自动关闭。
 */

import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Box,
  Boxes,
  CheckCircle2,
  Copy,
  Download,
  Info,
  Loader2,
  PackagePlus,
  RefreshCw,
} from "lucide-react";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { useToast } from "@/components/ui/toast";
import { useAuth } from "@/hooks/useAuth";
import { cn } from "@/lib/utils";
import {
  importComposeObjects,
  listComposeImportCandidates,
  type ComposeImportCandidates,
  type ComposeImportContainer,
  type ComposeImportProject,
} from "@/api/compose";

type Selection =
  | { kind: "project"; id: number }
  | { kind: "container"; id: string };

function pickSelection(
  data: ComposeImportCandidates,
  previous: Selection | null
): Selection | null {
  if (previous?.kind === "project") {
    if (data.projects.some((item) => item.project_id === previous.id)) {
      return previous;
    }
  }
  if (previous?.kind === "container") {
    if (data.containers.some((item) => item.container_id === previous.id)) {
      return previous;
    }
  }
  if (data.projects.length > 0) {
    return { kind: "project", id: data.projects[0].project_id };
  }
  if (data.containers.length > 0) {
    return { kind: "container", id: data.containers[0].container_id };
  }
  return null;
}

function mountSnippet(project: ComposeImportProject) {
  const dirs = project.missing_dirs.length
    ? project.missing_dirs
    : project.required_dirs;
  return ["volumes:", ...dirs.map((dir) => `  - ${dir}:${dir}`)].join("\n");
}

/** 运行状态：颜色之外始终带文字 */
function StatusPill({ running }: { running: boolean }) {
  return (
    <span
      className={cn(
        "inline-flex shrink-0 items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] font-medium",
        running
          ? "bg-emerald-500/10 text-emerald-700 dark:text-emerald-400"
          : "bg-muted text-muted-foreground"
      )}
    >
      <span
        className={cn(
          "size-1.5 rounded-full",
          running ? "bg-emerald-500" : "bg-muted-foreground/50"
        )}
      />
      {running ? "运行中" : "已停止"}
    </span>
  );
}

function MetaChip({
  children,
  title,
}: {
  children: ReactNode;
  title?: string;
}) {
  return (
    <span
      className="max-w-full truncate rounded-md bg-muted px-1.5 py-0.5 font-mono text-[11px] text-foreground/80"
      title={title}
    >
      {children}
    </span>
  );
}

function RailItem({
  active,
  icon,
  name,
  subtitle,
  warning,
  onSelect,
}: {
  active: boolean;
  icon: ReactNode;
  name: string;
  subtitle: ReactNode;
  warning?: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      aria-current={active ? "true" : undefined}
      onClick={onSelect}
      className={cn(
        "flex w-full items-center gap-2.5 rounded-lg py-2 pr-3 pl-2.5 text-left outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring",
        active
          ? "bg-primary/10 ring-1 ring-primary/20"
          : "hover:bg-muted/60"
      )}
    >
      <span
        className={cn(
          "flex size-7 shrink-0 items-center justify-center rounded-md ring-1",
          active
            ? "bg-primary/15 text-primary ring-primary/20"
            : "bg-background text-muted-foreground ring-border/60"
        )}
      >
        {icon}
      </span>
      <span className="min-w-0 flex-1">
        <span
          className={cn(
            "block truncate text-[13px] font-medium",
            active ? "text-primary" : "text-foreground"
          )}
        >
          {name}
        </span>
        <span className="mt-0.5 block truncate text-[11px]">{subtitle}</span>
      </span>
      {warning && (
        <AlertTriangle className="size-3.5 shrink-0 text-amber-500" />
      )}
    </button>
  );
}

function MobileChip({
  active,
  icon,
  name,
  tone,
  onSelect,
}: {
  active: boolean;
  icon: ReactNode;
  name: string;
  tone: "warn" | "ok" | "muted";
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      title={name}
      aria-current={active ? "true" : undefined}
      onClick={onSelect}
      className={cn(
        "flex shrink-0 items-center gap-1.5 rounded-full border px-2.5 py-1.5 text-xs outline-none transition-colors focus-visible:ring-2 focus-visible:ring-ring",
        active
          ? "border-primary/25 bg-primary/10 text-primary"
          : "border-border/70 bg-background hover:bg-muted/60"
      )}
    >
      <span
        className={cn(
          "size-1.5 shrink-0 rounded-full",
          tone === "warn"
            ? "bg-amber-500"
            : tone === "ok"
              ? "bg-emerald-500"
              : "bg-muted-foreground/40"
        )}
      />
      <span className="shrink-0 text-muted-foreground">{icon}</span>
      <span className="max-w-36 truncate">{name}</span>
    </button>
  );
}

function ProjectDetail({
  project,
  importing,
  busy,
  onImport,
}: {
  project: ComposeImportProject;
  importing: boolean;
  busy: boolean;
  onImport: () => void;
}) {
  const toast = useToast();
  const missing = project.missing_dirs.length > 0;

  const copyMount = async () => {
    try {
      await navigator.clipboard.writeText(mountSnippet(project));
      toast.success("挂载配置已复制", { duration: 2000 });
    } catch {
      toast.error("复制失败，请手动复制", { duration: 3000 });
    }
  };

  return (
    <div className="flex h-full flex-col">
      <div className="flex items-start justify-between gap-4">
        <div className="flex min-w-0 items-start gap-3">
          <div className="flex size-11 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary">
            <Boxes className="size-5" />
          </div>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="truncate text-base font-semibold">
                {project.project_name}
              </h3>
              <StatusPill running={project.running_count > 0} />
            </div>
            <p className="mt-0.5 text-xs text-muted-foreground">
              Compose 项目 · 由外部创建
            </p>
          </div>
        </div>
        {project.ready ? (
          <Button size="sm" onClick={onImport} disabled={busy}>
            {importing ? <Loader2 className="animate-spin" /> : <Download />}
            导入
          </Button>
        ) : (
          <span className="inline-flex shrink-0 items-center gap-1.5 rounded-full border border-amber-500/30 bg-amber-500/10 px-2.5 py-1 text-[11px] font-medium text-amber-700 dark:text-amber-300">
            <AlertTriangle className="size-3.5" />
            待挂载
          </span>
        )}
      </div>

      <dl className="mt-5 space-y-2.5 text-xs">
        <div className="flex gap-3">
          <dt className="w-16 shrink-0 pt-0.5 text-muted-foreground">镜像</dt>
          <dd className="flex min-w-0 flex-1 flex-wrap gap-1">
            {project.images.length > 0 ? (
              project.images.map((image) => (
                <MetaChip key={image} title={image}>
                  {image}
                </MetaChip>
              ))
            ) : (
              <span className="text-muted-foreground">未检测到镜像</span>
            )}
          </dd>
        </div>
        <div className="flex gap-3">
          <dt className="w-16 shrink-0 pt-0.5 text-muted-foreground">
            端口映射
          </dt>
          <dd className="flex min-w-0 flex-1 flex-wrap gap-1">
            {project.ports.length > 0 ? (
              project.ports.map((port) => (
                <MetaChip key={port} title={port}>
                  {port}
                </MetaChip>
              ))
            ) : (
              <span className="text-muted-foreground">无</span>
            )}
          </dd>
        </div>
        <div className="flex gap-3">
          <dt className="w-16 shrink-0 text-muted-foreground">容器</dt>
          <dd className="text-foreground/80">
            {project.container_count === 0
              ? "无关联容器"
              : project.running_count === project.container_count
                ? `${project.container_count} 个，全部运行中`
                : `${project.container_count} 个，${project.running_count} 个运行中`}
          </dd>
        </div>
      </dl>

      <div className="mt-6 flex-1">
        <h4 className="text-[11px] font-medium tracking-wider text-muted-foreground">
          挂载检查
        </h4>

        {project.ready ? (
          <div className="mt-2 rounded-xl border border-emerald-500/25 bg-emerald-500/[0.06] p-3.5">
            <div className="flex items-start gap-2.5">
              <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-emerald-600 dark:text-emerald-400" />
              <div className="min-w-0">
                <p className="text-[13px] font-medium text-emerald-700 dark:text-emerald-300">
                  项目目录已挂载
                </p>
                <p className="mt-0.5 text-xs text-muted-foreground">
                  Compose 配置可正常读取，导入后即可统一启停和查看日志。
                </p>
                <p className="mt-1.5 break-all font-mono text-[11px] leading-relaxed text-muted-foreground">
                  {project.required_dirs.join("、")}
                </p>
              </div>
            </div>
          </div>
        ) : (
          <div className="mt-2 rounded-xl border border-amber-500/30 bg-amber-500/[0.06] p-3.5">
            <div className="flex items-start gap-2.5">
              <AlertTriangle className="mt-0.5 size-4 shrink-0 text-amber-600 dark:text-amber-400" />
              <div className="min-w-0 flex-1">
                <p className="text-[13px] font-medium text-amber-700 dark:text-amber-300">
                  {missing ? "项目目录未挂载" : project.reason}
                </p>
                <p className="mt-0.5 text-xs leading-relaxed text-muted-foreground">
                  把以下目录挂载到 OneTap 容器（建议宿主机与容器内路径一致），
                  或在「系统设置 → 存储目录」添加映射，然后重新检测。
                </p>

                <div className="mt-2.5 overflow-hidden rounded-lg bg-background/80 ring-1 ring-border/60">
                  <div className="flex items-center justify-between border-b border-border/50 px-3 py-1">
                    <span className="text-[10px] font-medium tracking-wider text-muted-foreground">
                      挂载配置
                    </span>
                    <Button
                      variant="ghost"
                      size="xs"
                      onClick={copyMount}
                      title="复制挂载配置"
                    >
                      <Copy />
                      复制
                    </Button>
                  </div>
                  <pre className="p-3 font-mono text-[11px] leading-relaxed break-all whitespace-pre-wrap">
{mountSnippet(project)}
                  </pre>
                </div>
              </div>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function ContainerDetail({
  container,
  importing,
  busy,
  onImport,
}: {
  container: ComposeImportContainer;
  importing: boolean;
  busy: boolean;
  onImport: () => void;
}) {
  const running = container.status === "running";
  return (
    <div className="flex h-full flex-col">
      <div className="flex items-start justify-between gap-4">
        <div className="flex min-w-0 items-start gap-3">
          <div className="flex size-11 shrink-0 items-center justify-center rounded-xl bg-primary/10 text-primary">
            <Box className="size-5" />
          </div>
          <div className="min-w-0">
            <div className="flex flex-wrap items-center gap-2">
              <h3 className="truncate text-base font-semibold">
                {container.container_name}
              </h3>
              <StatusPill running={running} />
            </div>
            <p className="mt-0.5 text-xs text-muted-foreground">
              独立容器 · 由外部创建
            </p>
          </div>
        </div>
        <Button size="sm" onClick={onImport} disabled={busy}>
          {importing ? <Loader2 className="animate-spin" /> : <Download />}
          导入
        </Button>
      </div>

      <dl className="mt-5 space-y-2.5 text-xs">
        <div className="flex gap-3">
          <dt className="w-16 shrink-0 pt-0.5 text-muted-foreground">镜像</dt>
          <dd className="flex min-w-0 flex-1 flex-wrap gap-1">
            {container.image ? (
              <MetaChip title={container.image}>{container.image}</MetaChip>
            ) : (
              <span className="text-muted-foreground">未检测到镜像</span>
            )}
          </dd>
        </div>
        <div className="flex gap-3">
          <dt className="w-16 shrink-0 pt-0.5 text-muted-foreground">
            端口映射
          </dt>
          <dd className="flex min-w-0 flex-1 flex-wrap gap-1">
            {container.ports ? (
              <MetaChip title={container.ports}>{container.ports}</MetaChip>
            ) : (
              <span className="text-muted-foreground">无</span>
            )}
          </dd>
        </div>
      </dl>

      <div className="mt-6 flex-1">
        <h4 className="text-[11px] font-medium tracking-wider text-muted-foreground">
          导入说明
        </h4>
        <div className="mt-2 rounded-xl border border-border/70 bg-muted/30 p-3.5 text-xs leading-relaxed text-muted-foreground">
          独立容器不需要挂载项目目录。导入后可在服务概览中启停、查看日志，
          不会重启或修改当前容器。
        </div>
      </div>
    </div>
  );
}

export function ExternalImportDialog({
  open: openProp,
  onOpenChange,
}: {
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
} = {}) {
  const { user } = useAuth();
  const toast = useToast();
  const queryClient = useQueryClient();
  const [uncontrolledOpen, setUncontrolledOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [candidates, setCandidates] = useState<ComposeImportCandidates | null>(
    null
  );
  const [selection, setSelection] = useState<Selection | null>(null);
  const [importingKey, setImportingKey] = useState<string | null>(null);
  const checkedRef = useRef(false);
  const openedRef = useRef(false);

  const open = openProp ?? uncontrolledOpen;
  const setOpen = useCallback(
    (next: boolean) => {
      if (openProp === undefined) setUncontrolledOpen(next);
      onOpenChange?.(next);
    },
    [openProp, onOpenChange]
  );

  const load = useCallback(async (silent = false) => {
    if (!silent) setLoading(true);
    try {
      const data = await listComposeImportCandidates();
      setCandidates(data);
      setSelection((previous) => pickSelection(data, previous));
      return data;
    } catch {
      // 扫描失败不打扰用户，进入编排页仍可手动处理
      return undefined;
    } finally {
      if (!silent) setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (user?.role !== "admin" || checkedRef.current) return;
    checkedRef.current = true;
    void load(true).then((data) => {
      if (!data) return;
      if (data.projects.length > 0 || data.containers.length > 0) {
        // 登录时已扫描过一次，打开弹窗不必再扫
        openedRef.current = true;
        setOpen(true);
      }
    });
  }, [user?.role, load, setOpen]);

  /** 手动打开时重新扫描一次，避免展示登录时的旧数据 */
  useEffect(() => {
    if (!open) {
      openedRef.current = false;
      return;
    }
    if (openedRef.current) return;
    openedRef.current = true;
    void load(true);
  }, [open, load]);

  const handleImport = async (
    key: string,
    payload: { project_ids: number[]; container_ids: string[] }
  ) => {
    setImportingKey(key);
    try {
      const result = await importComposeObjects(payload);
      if (result.errors.length > 0) {
        toast.error(
          result.errors
            .map((item) => `${item.name}: ${item.message}`)
            .join("；"),
          { duration: 6000 }
        );
      }
      const imported =
        result.imported_projects.length + result.imported_containers.length;
      if (imported > 0) {
        toast.success(`已导入 ${imported} 个应用`, { duration: 3000 });
        queryClient.invalidateQueries({ queryKey: ["compose", "projects"] });
        queryClient.invalidateQueries({ queryKey: ["dashboard"] });
        queryClient.invalidateQueries({ queryKey: ["apps"] });
      }
      const remaining = await load(true);
      if (
        remaining &&
        remaining.projects.length === 0 &&
        remaining.containers.length === 0
      ) {
        setOpen(false);
      }
    } catch {
      toast.error("导入失败，请稍后重试", { duration: 4000 });
    } finally {
      setImportingKey(null);
    }
  };

  if (!open) return null;

  const projects = candidates?.projects ?? [];
  const containers = candidates?.containers ?? [];
  const selectedProject =
    selection?.kind === "project"
      ? projects.find((item) => item.project_id === selection.id)
      : undefined;
  const selectedContainer =
    selection?.kind === "container"
      ? containers.find((item) => item.container_id === selection.id)
      : undefined;
  const readyProjects = projects.filter((project) => project.ready);
  const importableCount = readyProjects.length + containers.length;
  const isEmpty =
    candidates !== null && projects.length === 0 && containers.length === 0;

  return (
    <Dialog open={open} onOpenChange={setOpen}>
      <DialogContent className="h-[min(84vh,580px)] gap-0 overflow-hidden p-0 sm:max-w-4xl [&_[data-slot=dialog-close]]:top-[12px] [&_[data-slot=dialog-close]]:right-4">
        <DialogHeader className="shrink-0 gap-0 border-b px-5 py-2.5 pr-12">
          <div className="flex items-center gap-2.5">
            <div className="flex size-8 shrink-0 items-center justify-center rounded-lg bg-primary/10 text-primary">
              <PackagePlus className="size-4" />
            </div>
            <DialogTitle className="text-base">导入已有应用</DialogTitle>
          </div>
          <DialogDescription className="sr-only">
            将外部容器接入 OneTap 统一管理，导入不会重启或修改现有容器
          </DialogDescription>
        </DialogHeader>

        {isEmpty ? (
          <div className="flex min-h-0 flex-1 flex-col items-center justify-center gap-1.5 px-6 text-center">
            <div className="flex size-10 items-center justify-center rounded-xl bg-muted text-muted-foreground">
              <PackagePlus className="size-5" />
            </div>
            <p className="mt-1 text-sm font-medium">未发现未接管的外部应用</p>
            <p className="text-xs text-muted-foreground">
              容器和 Compose 项目都已纳入 OneTap 管理
            </p>
          </div>
        ) : (
          <div className="flex min-h-0 flex-1 flex-col sm:flex-row">
          {/* 窄屏：横向对象切换 */}
          <div className="flex shrink-0 gap-2 overflow-x-auto border-b px-3 py-2.5 sm:hidden">
            {projects.map((project) => (
              <MobileChip
                key={`project-${project.project_id}`}
                active={
                  selection?.kind === "project" &&
                  selection.id === project.project_id
                }
                icon={<Boxes className="size-3" />}
                name={project.project_name}
                tone={project.ready ? "ok" : "warn"}
                onSelect={() =>
                  setSelection({ kind: "project", id: project.project_id })
                }
              />
            ))}
            {containers.map((container) => (
              <MobileChip
                key={`container-${container.container_id}`}
                active={
                  selection?.kind === "container" &&
                  selection.id === container.container_id
                }
                icon={<Box className="size-3" />}
                name={container.container_name}
                tone={container.status === "running" ? "ok" : "muted"}
                onSelect={() =>
                  setSelection({
                    kind: "container",
                    id: container.container_id,
                  })
                }
              />
            ))}
          </div>

          {/* 宽屏：左侧对象列表 */}
          <div className="hidden shrink-0 overflow-y-auto border-r bg-muted/20 p-2 sm:block sm:w-60">
            {projects.length > 0 && (
              <div className="pb-1">
                <div className="flex items-center justify-between px-2.5 pt-1.5 pb-1">
                  <span className="text-[11px] font-medium text-muted-foreground">
                    Compose 项目
                  </span>
                  <span className="text-[11px] tabular-nums text-muted-foreground/70">
                    {projects.length}
                  </span>
                </div>
                <div className="space-y-0.5">
                  {projects.map((project) => (
                    <RailItem
                      key={project.project_id}
                      active={
                        selection?.kind === "project" &&
                        selection.id === project.project_id
                      }
                      icon={<Boxes className="size-3.5" />}
                      name={project.project_name}
                      subtitle={
                        <>
                          <span className="text-muted-foreground">
                            {project.container_count} 个容器 ·{" "}
                          </span>
                          <span
                            className={
                              project.ready
                                ? "text-emerald-600 dark:text-emerald-400"
                                : "text-amber-600 dark:text-amber-400"
                            }
                          >
                            {project.ready ? "已挂载" : "待挂载"}
                          </span>
                        </>
                      }
                      warning={!project.ready}
                      onSelect={() =>
                        setSelection({
                          kind: "project",
                          id: project.project_id,
                        })
                      }
                    />
                  ))}
                </div>
              </div>
            )}

            {containers.length > 0 && (
              <div className="pb-1">
                <div className="flex items-center justify-between px-2.5 pt-3 pb-1">
                  <span className="text-[11px] font-medium text-muted-foreground">
                    独立容器
                  </span>
                  <span className="text-[11px] tabular-nums text-muted-foreground/70">
                    {containers.length}
                  </span>
                </div>
                <div className="space-y-0.5">
                  {containers.map((container) => (
                    <RailItem
                      key={container.container_id}
                      active={
                        selection?.kind === "container" &&
                        selection.id === container.container_id
                      }
                      icon={<Box className="size-3.5" />}
                      name={container.container_name}
                      subtitle={
                        <span className="text-muted-foreground">
                          {container.image || "独立容器"}
                        </span>
                      }
                      onSelect={() =>
                        setSelection({
                          kind: "container",
                          id: container.container_id,
                        })
                      }
                    />
                  ))}
                </div>
              </div>
            )}
          </div>

          {/* 右侧详情 */}
          <div className="min-h-0 flex-1 overflow-y-auto p-5">
            {selectedProject && (
              <ProjectDetail
                project={selectedProject}
                importing={
                  importingKey === `project-${selectedProject.project_id}`
                }
                busy={importingKey !== null}
                onImport={() =>
                  handleImport(`project-${selectedProject.project_id}`, {
                    project_ids: [selectedProject.project_id],
                    container_ids: [],
                  })
                }
              />
            )}
            {selectedContainer && (
              <ContainerDetail
                container={selectedContainer}
                importing={
                  importingKey === `container-${selectedContainer.container_id}`
                }
                busy={importingKey !== null}
                onImport={() =>
                  handleImport(
                    `container-${selectedContainer.container_id}`,
                    {
                      project_ids: [],
                      container_ids: [selectedContainer.container_id],
                    }
                  )
                }
              />
            )}
            {!selectedProject && !selectedContainer && (
              <div className="flex h-full items-center justify-center text-sm text-muted-foreground">
                选择一个项目查看详情
              </div>
            )}
          </div>
          </div>
        )}

        <div className="flex shrink-0 items-center justify-between border-t bg-muted/30 px-5 py-3">
          <p className="hidden items-center gap-1.5 text-[11px] text-muted-foreground sm:flex">
            <Info className="size-3.5 shrink-0" />
            将外部容器接入 OneTap 统一管理，导入不会重启或修改现有容器
          </p>
          <div className="ml-auto flex flex-wrap items-center justify-end gap-2">
            <Button
              variant="outline"
              size="sm"
              onClick={() => void load()}
              disabled={loading || importingKey !== null}
            >
              <RefreshCw className={loading ? "animate-spin" : undefined} />
              重新检测
            </Button>
            <Button
              size="sm"
              onClick={() =>
                handleImport("all", {
                  project_ids: readyProjects.map((item) => item.project_id),
                  container_ids: containers.map(
                    (item) => item.container_id
                  ),
                })
              }
              disabled={importingKey !== null || importableCount === 0}
              title={
                importableCount === 0
                  ? "暂无可直接导入的对象，请先挂载项目目录"
                  : undefined
              }
            >
              {importingKey === "all" ? (
                <Loader2 className="animate-spin" />
              ) : (
                <Download />
              )}
              一键导入
            </Button>
          </div>
        </div>
      </DialogContent>
    </Dialog>
  );
}
