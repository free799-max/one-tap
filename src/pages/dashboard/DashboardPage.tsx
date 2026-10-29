/**
 * 服务概览页面
 *
 * 采用 sun-panel 式的应用墙布局：顶部展示宿主机实时状态，
 * 已部署应用按分类自动分组，访问地址由后端根据部署端口自动生成。
 * 只展示实际存在容器/项目的应用，失败或已清理的部署记录不会出现。
 */

import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import {
  BookImage,
  BookOpen,
  Clapperboard,
  Clock,
  Container,
  Cpu,
  Download,
  Film,
  Gamepad2,
  Globe,
  HardDrive,
  Info,
  Images,
  LayoutGrid,
  MemoryStick,
  Music,
  Network,
  Newspaper,
  Package,
  RefreshCw,
  Search,
  Server,
  Store,
  Wrench,
  type LucideIcon,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { AppIcon } from "@/pages/apps/AppIcon";
import { AppDetailDialog } from "./AppDetailDialog";
import { useDockerHostInfo } from "@/hooks/useDocker";
import { categoryLabel } from "@/lib/categories";
import { useCategories } from "@/hooks/useApps";
import { cn, formatBytes, formatUptime } from "@/lib/utils";
import { useOverview, type OverviewApp } from "@/hooks/useDashboard";

const CATEGORY_ICONS: Record<string, LucideIcon> = {
  movie: Film,
  media: Clapperboard,
  comic: BookImage,
  book: BookOpen,
  music: Music,
  download: Download,
  gallery: Images,
  game: Gamepad2,
  news: Newspaper,
  tool: Wrench,
  network: Network,
  web: Globe,
  docker: Container,
  other: Package,
};

const STATUS_META: Record<string, { label: string; dot: string; text: string }> = {
  running: {
    label: "运行中",
    dot: "bg-emerald-500",
    text: "text-emerald-600",
  },
  deploying: {
    label: "部署中",
    dot: "bg-amber-500 animate-pulse",
    text: "text-amber-600",
  },
  stopped: {
    label: "已停止",
    dot: "bg-muted-foreground/40",
    text: "text-muted-foreground",
  },
  error: {
    label: "异常",
    dot: "bg-destructive",
    text: "text-destructive",
  },
};

function StatusMetric({
  icon: Icon,
  label,
  value,
  hint,
}: {
  icon: LucideIcon;
  label: string;
  value: string;
  hint?: string;
}) {
  return (
    <div className="flex items-center gap-2">
      <Icon className="h-4 w-4 shrink-0 text-muted-foreground" />
      <div className="min-w-0">
        <p className="text-[10px] leading-none text-muted-foreground">{label}</p>
        <p className="mt-0.5 truncate text-xs font-medium leading-tight text-foreground">
          {value}
          {hint && (
            <span className="ml-1 font-normal text-muted-foreground">{hint}</span>
          )}
        </p>
      </div>
    </div>
  );
}

function HostStatusBar({
  hostname,
  cpuPercent,
  cpuCores,
  memoryUsed,
  memoryTotal,
  diskUsed,
  diskTotal,
  uptimeSeconds,
  search,
  onSearchChange,
  onRefresh,
  refreshing,
}: {
  hostname?: string;
  cpuPercent?: number;
  cpuCores?: number;
  memoryUsed?: number;
  memoryTotal?: number;
  diskUsed?: number;
  diskTotal?: number;
  uptimeSeconds?: number;
  search: string;
  onSearchChange: (value: string) => void;
  onRefresh: () => void;
  refreshing: boolean;
}) {
  const [now, setNow] = useState(() => new Date());

  useEffect(() => {
    const timer = window.setInterval(() => setNow(new Date()), 30_000);
    return () => window.clearInterval(timer);
  }, []);

  const clock = `${now.getFullYear()}/${now.getMonth() + 1}/${now.getDate()} ${String(
    now.getHours()
  ).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`;

  return (
    <div className="rounded-xl border border-border/70 bg-card px-4 py-3 shadow-[var(--shadow-card)]">
      <div className="flex flex-wrap items-center gap-x-5 gap-y-3">
        <div className="flex items-center gap-2.5">
          <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-primary/10">
            <Server className="h-4 w-4 text-primary" />
          </div>
          <div className="min-w-0">
            <p className="truncate text-sm font-semibold leading-tight text-foreground">
              {hostname || "OneTap"}
            </p>
            <p className="text-[10px] leading-tight text-muted-foreground">
              服务概览
            </p>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-x-5 gap-y-2">
          <StatusMetric
            icon={Cpu}
            label="CPU"
            value={cpuPercent == null ? "—" : `${cpuPercent}%`}
            hint={cpuCores ? `${cpuCores} 核` : undefined}
          />
          <StatusMetric
            icon={MemoryStick}
            label="内存"
            value={memoryUsed == null ? "—" : formatBytes(memoryUsed)}
            hint={memoryTotal ? `/ ${formatBytes(memoryTotal)}` : undefined}
          />
          <StatusMetric
            icon={HardDrive}
            label="磁盘"
            value={diskUsed == null ? "—" : formatBytes(diskUsed)}
            hint={diskTotal ? `/ ${formatBytes(diskTotal)}` : undefined}
          />
          <StatusMetric
            icon={Clock}
            label="运行时间"
            value={uptimeSeconds == null ? "—" : formatUptime(uptimeSeconds)}
          />
        </div>

        <div className="ml-auto flex items-center gap-2">
          <div className="relative w-44 sm:w-56">
            <Search className="absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
            <Input
              placeholder="搜索服务"
              value={search}
              onChange={(event) => onSearchChange(event.target.value)}
              className="h-8 rounded-lg border border-black/10 bg-white pl-9 text-xs"
            />
          </div>
          <span className="hidden whitespace-nowrap text-xs tabular-nums text-muted-foreground lg:inline">
            {clock}
          </span>
          <Button
            variant="outline"
            size="sm"
            render={<Link to="/appstore" />}
          >
            <Store />
            应用市场
          </Button>
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={onRefresh}
            disabled={refreshing}
            title="刷新"
          >
            <RefreshCw className={cn(refreshing && "animate-spin")} />
          </Button>
        </div>
      </div>
    </div>
  );
}

function ServiceCard({
  app,
  onShowDetail,
}: {
  app: OverviewApp;
  onShowDetail: (app: OverviewApp) => void;
}) {
  const status = STATUS_META[app.status] ?? {
    label: app.status,
    dot: "bg-muted-foreground/40",
    text: "text-muted-foreground",
  };
  const subtitle = app.description || app.instance_name;

  return (
    <div
      className={cn(
        "group relative flex items-center gap-3 rounded-xl border border-border/70 bg-card px-3.5 py-3 shadow-[var(--shadow-card)] transition-all",
        app.url &&
          "hover:-translate-y-0.5 hover:border-primary/30 hover:shadow-[var(--shadow-elevated)]"
      )}
      title={
        app.url
          ? `${app.display_name} · ${app.instance_name} · ${app.url}`
          : `${app.display_name} · ${app.instance_name}（尚未获取到访问地址）`
      }
    >
      {app.url && (
        <a
          href={app.url}
          target="_blank"
          rel="noreferrer noopener"
          aria-label={`打开 ${app.display_name}`}
          title={`${app.display_name} · ${app.instance_name} · ${app.url}`}
          className="absolute inset-0 z-0 rounded-xl focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
        />
      )}

      <AppIcon
        app={{ name: app.app_name, icon: app.icon }}
        className="pointer-events-none relative z-10 h-11 w-11 shrink-0 border border-border/60 text-lg"
      />
      <div className="pointer-events-none relative z-10 min-w-0 flex-1 pr-6">
        <div className="flex items-center gap-1.5">
          <span
            className={cn("h-1.5 w-1.5 shrink-0 rounded-full", status.dot)}
            title={status.label}
            aria-label={status.label}
          />
          <span className="truncate text-sm font-medium text-foreground">
            {app.display_name}
          </span>
        </div>
        <div className="mt-0.5 flex items-center gap-1.5">
          <p className="min-w-0 flex-1 truncate text-xs text-muted-foreground">
            {subtitle}
          </p>
          <span className={cn("shrink-0 text-[10px]", status.text)}>
            {status.label}
          </span>
          {!app.url && (
            <span className="shrink-0 text-[10px] text-muted-foreground/70">
              无地址
            </span>
          )}
        </div>
      </div>

      <button
        type="button"
        onClick={(event) => {
          event.preventDefault();
          event.stopPropagation();
          onShowDetail(app);
        }}
        title="查看容器详情"
        aria-label={`查看 ${app.display_name} 的容器详情`}
        className="pointer-events-none absolute top-1.5 right-1.5 z-20 flex h-6 w-6 items-center justify-center rounded-md text-muted-foreground/70 opacity-0 transition-all duration-150 hover:bg-muted hover:text-foreground group-hover:pointer-events-auto group-hover:opacity-100 focus-visible:pointer-events-auto focus-visible:opacity-100 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
      >
        <Info className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}

function CategorySection({
  category,
  label,
  apps,
  onShowDetail,
}: {
  category: string;
  label: string;
  apps: OverviewApp[];
  onShowDetail: (app: OverviewApp) => void;
}) {
  const Icon = CATEGORY_ICONS[category] ?? Package;
  const running = apps.filter((app) => app.status === "running").length;
  return (
    <section className="space-y-2.5">
      <div className="flex items-center gap-2">
        <Icon className="h-4 w-4 text-primary" />
        <h2 className="text-sm font-semibold text-foreground">
          {label}
        </h2>
        <span className="text-xs text-muted-foreground">
          {apps.length} 个应用
          {running > 0 && (
            <span className="ml-1.5 text-emerald-600">{running} 运行中</span>
          )}
        </span>
        <div className="h-px flex-1 bg-border/70" />
      </div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 2xl:grid-cols-6">
        {apps.map((app) => (
          <ServiceCard
            key={app.id}
            app={app}
            onShowDetail={onShowDetail}
          />
        ))}
      </div>
    </section>
  );
}

function LoadingSkeleton() {
  return (
    <div className="space-y-6">
      {[0, 1].map((section) => (
        <div key={section} className="space-y-3">
          <div className="h-4 w-24 animate-pulse rounded bg-muted" />
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 md:grid-cols-3 lg:grid-cols-4 xl:grid-cols-5 2xl:grid-cols-6">
            {Array.from({ length: 6 }).map((_, index) => (
              <div
                key={index}
                className="flex animate-pulse items-center gap-3 rounded-xl border border-border/70 bg-card px-3.5 py-3"
              >
                <div className="h-11 w-11 shrink-0 rounded-xl bg-muted" />
                <div className="flex-1 space-y-2">
                  <div className="h-3 w-2/3 rounded bg-muted" />
                  <div className="h-2.5 w-full rounded bg-muted" />
                </div>
              </div>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}

export function DashboardPage() {
  const { data, isLoading, isError, isFetching, refetch } = useOverview();
  const { data: hostInfo } = useDockerHostInfo();
  const { data: categoryOptions = [] } = useCategories();
  const [search, setSearch] = useState("");
  const [detailAppId, setDetailAppId] = useState<number | null>(null);

  const categories = useMemo(() => {
    const keyword = search.trim().toLowerCase();
    if (!keyword) return data?.categories ?? [];
    return (data?.categories ?? [])
      .map((category) => ({
        ...category,
        apps: category.apps.filter((app) =>
          [
            app.display_name,
            app.app_name,
            app.instance_name,
            app.description ?? "",
            categoryLabel(categoryOptions, category.category),
          ].some((value) => value.toLowerCase().includes(keyword))
        ),
      }))
      .filter((category) => category.apps.length > 0);
  }, [data, search, categoryOptions]);

  const visibleTotal = categories.reduce(
    (total, category) => total + category.apps.length,
    0
  );
  const isFiltering = search.trim().length > 0;
  const resources = hostInfo?.resources;

  return (
    <div className="space-y-5">
      <HostStatusBar
        hostname={hostInfo?.hostname}
        cpuPercent={resources?.cpu_usage_percent}
        cpuCores={resources?.cpu_cores}
        memoryUsed={resources?.memory_used}
        memoryTotal={resources?.memory_total}
        diskUsed={resources?.disk_used}
        diskTotal={resources?.disk_total}
        uptimeSeconds={hostInfo?.uptime_seconds}
        search={search}
        onSearchChange={setSearch}
        onRefresh={() => refetch()}
        refreshing={isFetching}
      />

      {isLoading ? (
        <LoadingSkeleton />
      ) : isError ? (
        <div className="rounded-2xl border border-dashed border-border bg-card/60 px-6 py-16 text-center">
          <p className="text-sm text-muted-foreground">服务概览加载失败</p>
          <Button variant="outline" size="sm" className="mt-4" onClick={() => refetch()}>
            重试
          </Button>
        </div>
      ) : visibleTotal === 0 ? (
        <div className="rounded-2xl border border-dashed border-border bg-card/60 px-6 py-16 text-center">
          <div className="mx-auto flex h-12 w-12 items-center justify-center rounded-2xl bg-primary/10">
            <LayoutGrid className="h-6 w-6 text-primary" />
          </div>
          <h2 className="mt-4 text-base font-semibold text-foreground">
            {isFiltering ? "没有匹配的服务" : "还没有已部署的应用"}
          </h2>
          <p className="mx-auto mt-1 max-w-md text-sm text-muted-foreground">
            {isFiltering
              ? "换个关键词试试，或清空搜索查看全部服务。"
              : "从应用市场安装应用后，这里会自动按分类生成跳转入口。"}
          </p>
          {isFiltering ? (
            <Button
              variant="outline"
              size="sm"
              className="mt-4"
              onClick={() => setSearch("")}
            >
              清空搜索
            </Button>
          ) : (
            <Button
              size="sm"
              className="mt-4"
              render={<Link to="/appstore" />}
            >
              <Store />
              去应用市场
            </Button>
          )}
        </div>
      ) : (
        <div className="rounded-xl border border-border/70 bg-card p-4 shadow-[var(--shadow-card)] sm:p-5">
          <div className="space-y-6">
            {categories.map((category) => (
              <CategorySection
                key={category.category}
                category={category.category}
                label={categoryLabel(categoryOptions, category.category)}
                apps={category.apps}
                onShowDetail={(app) => setDetailAppId(app.id)}
              />
            ))}
          </div>
        </div>
      )}

      <AppDetailDialog
        instanceId={detailAppId}
        open={detailAppId != null}
        onOpenChange={(open) => !open && setDetailAppId(null)}
      />
    </div>
  );
}
