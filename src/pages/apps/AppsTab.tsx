/**
 * 应用商店标签页
 *
 * 应用商店风格：工具栏（分类标签 + 搜索）、卡片网格、分页。
 */

import { useState, useMemo } from "react";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { AppCard } from "./AppCard";
import { AppDeployDialog } from "./AppDeployDialog";
import { AppDetailDialog } from "./AppDetailDialog";
import { useToast } from "@/components/ui/toast";
import { useDeployTasks } from "@/hooks/useDeployTasks";
import {
  useApps,
  useApp,
  useCategories,
  useDeployApp,
  type App,
  type AppDetail,
} from "@/hooks/useApps";
import { Search, ChevronLeft, ChevronRight } from "lucide-react";
import { categoryLabel, categoryRank } from "@/lib/categories";

const PAGE_SIZE_OPTIONS = [20, 40, 60, 100];

export function AppsTab() {
  const { data: apps = [], isLoading } = useApps();
  const { data: categoryOptions = [] } = useCategories();
  const deployMutation = useDeployApp();
  const toast = useToast();
  const { startTask } = useDeployTasks();

  const [search, setSearch] = useState("");
  const [selectedCategory, setSelectedCategory] = useState<string>("all");
  const [pageSize, setPageSize] = useState(60);
  const [page, setPage] = useState(1);
  const [deployApp, setDeployApp] = useState<App | null>(null);
  const [detailApp, setDetailApp] = useState<App | null>(null);
  const { data: detailAppFull } = useApp(detailApp?.name ?? "");
  const [jumpPage, setJumpPage] = useState("");

  const categories = useMemo(
    () =>
      Array.from(new Set(apps.map((a) => a.category))).sort(
        (a, b) => categoryRank(categoryOptions, a) - categoryRank(categoryOptions, b)
      ),
    [apps, categoryOptions]
  );

  const filtered = useMemo(() => {
    return apps.filter((a) => {
      const matchSearch =
        !search.trim() ||
        a.display_name.toLowerCase().includes(search.toLowerCase()) ||
        (a.description ?? "").toLowerCase().includes(search.toLowerCase()) ||
        a.tags.some((tag) => tag.toLowerCase().includes(search.toLowerCase()));
      const matchCategory =
        selectedCategory === "all" || a.category === selectedCategory;
      return matchSearch && matchCategory;
    });
  }, [apps, search, selectedCategory]);

  const total = filtered.length;
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const safePage = Math.min(page, totalPages);
  const paged = filtered.slice((safePage - 1) * pageSize, safePage * pageSize);

  const handleSearchChange = (value: string) => {
    setSearch(value);
    setPage(1);
  };

  const handleCategoryChange = (category: string) => {
    setSelectedCategory(category);
    setPage(1);
  };

  const handlePageSizeChange = (size: number) => {
    setPageSize(size);
    setPage(1);
  };

  const handleJump = () => {
    const n = parseInt(jumpPage, 10);
    if (!Number.isNaN(n)) {
      const target = Math.min(Math.max(1, n), totalPages);
      setPage(target);
    }
    setJumpPage("");
  };

  const handleDeploy = (data: {
    instance_name: string;
    config: Record<string, unknown>;
  }) => {
    if (!deployApp) return;
    deployMutation.mutate(
      { name: deployApp.name, data },
      {
        onSuccess: (response) => {
          setDeployApp(null);
          startTask(response.task_id);
          toast.success("部署任务已启动");
        },
      }
    );
  };

  const pageNumbers = useMemo(() => {
    const pages: (number | string)[] = [];
    const maxVisible = 5;
    if (totalPages <= maxVisible + 2) {
      for (let i = 1; i <= totalPages; i++) pages.push(i);
    } else {
      pages.push(1);
      let start = Math.max(2, safePage - 1);
      let end = Math.min(totalPages - 1, safePage + 1);
      if (safePage <= 3) {
        end = Math.min(totalPages - 1, maxVisible);
      }
      if (safePage >= totalPages - 2) {
        start = Math.max(2, totalPages - maxVisible + 1);
      }
      if (start > 2) pages.push("...");
      for (let i = start; i <= end; i++) pages.push(i);
      if (end < totalPages - 1) pages.push("...");
      pages.push(totalPages);
    }
    return pages;
  }, [safePage, totalPages]);

  return (
    <div className="space-y-4">
      <Card className="rounded-xl py-2">
        <CardContent className="px-4 pb-4 pt-2 space-y-4">
          {/* 工具栏 */}
          <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
            <div className="flex items-center gap-2 overflow-x-auto scrollbar-hidden pb-1 order-2 sm:order-1">
              <button
                onClick={() => handleCategoryChange("all")}
                className={`shrink-0 px-3.5 py-1.5 rounded-full text-sm font-medium transition-colors ${
                  selectedCategory === "all"
                    ? "bg-primary text-primary-foreground shadow-sm"
                    : "bg-muted/70 text-muted-foreground hover:bg-muted"
                }`}
              >
                全部
              </button>
              {categories.map((cat) => (
                <button
                  key={cat}
                  onClick={() => handleCategoryChange(cat)}
                  className={`shrink-0 px-3.5 py-1.5 rounded-full text-sm font-medium transition-colors ${
                    selectedCategory === cat
                      ? "bg-primary text-primary-foreground shadow-sm"
                      : "bg-muted/70 text-muted-foreground hover:bg-muted"
                  }`}
                >
                  {categoryLabel(categoryOptions, cat)}
                </button>
              ))}
            </div>

            <div className="flex items-center gap-2 order-1 sm:order-2">
              <div className="relative w-52">
                <Search className="absolute left-3 top-1/2 -translate-y-1/2 h-4 w-4 text-muted-foreground" />
                <Input
                  placeholder="搜索"
                  value={search}
                  onChange={(e) => handleSearchChange(e.target.value)}
                  className="pl-9 rounded-lg border border-black/10 bg-white"
                />
              </div>
            </div>
          </div>

          {/* 卡片网格 */}
          {isLoading ? (
            <div className="text-sm text-muted-foreground py-12 text-center">
              加载中...
            </div>
          ) : paged.length === 0 ? (
            <div className="text-sm text-muted-foreground py-12 text-center">
              暂无匹配的应用
            </div>
          ) : (
            <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
              {paged.map((app) => (
                <AppCard
                  key={app.name}
                  app={app}
                  onDeploy={setDeployApp}
                  onDetail={setDetailApp}
                />
              ))}
            </div>
          )}

          {/* 分页 */}
          {total > 0 && (
            <div className="flex flex-col sm:flex-row items-center justify-end gap-3 pt-2">
              <span className="text-sm text-muted-foreground">
                共 {total} 条
              </span>

              <div className="flex items-center gap-2">
                <select
                  value={pageSize}
                  onChange={(e) => handlePageSizeChange(Number(e.target.value))}
                  className="h-8 rounded-lg border border-border bg-white px-2 text-sm text-foreground outline-none focus:ring-2 focus:ring-primary/20"
                >
                  {PAGE_SIZE_OPTIONS.map((size) => (
                    <option key={size} value={size}>
                      {size}条/页
                    </option>
                  ))}
                </select>

                <div className="flex items-center gap-1">
                  <button
                    onClick={() => setPage((p) => Math.max(1, p - 1))}
                    disabled={safePage === 1}
                    className="h-8 w-8 flex items-center justify-center rounded-lg border border-border bg-white text-foreground hover:bg-muted disabled:opacity-40 disabled:cursor-not-allowed"
                  >
                    <ChevronLeft className="h-4 w-4" />
                  </button>

                  {pageNumbers.map((item, idx) =>
                    item === "..." ? (
                      <span
                        key={`ellipsis-${idx}`}
                        className="h-8 w-8 flex items-center justify-center text-sm text-muted-foreground"
                      >
                        ...
                      </span>
                    ) : (
                      <button
                        key={item}
                        onClick={() => setPage(item as number)}
                        className={`h-8 min-w-8 px-2 rounded-lg text-sm font-medium transition-colors ${
                          safePage === item
                            ? "bg-primary text-primary-foreground"
                            : "border border-border bg-white text-foreground hover:bg-muted"
                        }`}
                      >
                        {item}
                      </button>
                    )
                  )}

                  <button
                    onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                    disabled={safePage === totalPages}
                    className="h-8 w-8 flex items-center justify-center rounded-lg border border-border bg-white text-foreground hover:bg-muted disabled:opacity-40 disabled:cursor-not-allowed"
                  >
                    <ChevronRight className="h-4 w-4" />
                  </button>
                </div>

                <div className="flex items-center gap-1 text-sm text-muted-foreground">
                  <span>前往</span>
                  <input
                    type="number"
                    min={1}
                    max={totalPages}
                    value={jumpPage}
                    onChange={(e) => setJumpPage(e.target.value)}
                    onKeyDown={(e) => e.key === "Enter" && handleJump()}
                    className="h-8 w-14 rounded-lg border border-border bg-white px-1 text-center text-sm text-foreground outline-none focus:ring-2 focus:ring-primary/20"
                  />
                  <span>页</span>
                </div>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      <AppDeployDialog
        key={deployApp?.name || "empty"}
        app={deployApp}
        open={!!deployApp}
        onOpenChange={(open) => !open && setDeployApp(null)}
        onDeploy={handleDeploy}
        isDeploying={deployMutation.isPending}
      />

      <AppDetailDialog
        key={detailApp?.name || "empty-detail"}
        app={(detailAppFull as AppDetail | undefined) ?? detailApp}
        open={!!detailApp}
        onOpenChange={(open) => !open && setDetailApp(null)}
        onDeploy={setDeployApp}
      />
    </div>
  );
}
