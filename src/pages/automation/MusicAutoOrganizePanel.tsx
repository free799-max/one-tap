/**
 * Music Tag Web 自动整理面板
 *
 * 配置保存在实例组 app_configs["music-tag-web"].auto_organize 中，
 * 由后端定时调用 Music Tag Web 的刮削/整理接口；本面板只负责编辑配置、
 * 展示进度和手动触发一轮执行。
 */

import { useEffect, useRef, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Progress } from "@/components/ui/progress";
import { cn } from "@/lib/utils";
import {
  useAppAutoOrganize,
  useRunAppAutoOrganize,
  useSaveAppAutoOrganize,
  type MusicAutoDirItem,
  type MusicAutoOrganizeConfig,
  type MusicAutoOrganizePanel as PanelData,
} from "@/hooks/useOrchestrations";
import {
  AlertTriangle,
  CheckCircle2,
  Clock,
  FolderTree,
  Loader2,
  Play,
  RefreshCw,
  Save,
  Wand2,
} from "lucide-react";

const SOURCE_OPTIONS: { value: string; label: string }[] = [
  { value: "netease", label: "网易云" },
  { value: "qmusic", label: "QQ 音乐" },
  { value: "kugou", label: "酷狗" },
  { value: "kuwo", label: "酷我" },
  { value: "migu", label: "咪咕" },
  { value: "bilibili", label: "B 站" },
  { value: "qianqian", label: "千千" },
];

const MODIFY_OPTIONS: { value: string; label: string }[] = [
  { value: "title", label: "标题" },
  { value: "artist", label: "艺术家" },
  { value: "album", label: "专辑" },
  { value: "album_img", label: "封面" },
  { value: "year", label: "年份" },
  { value: "lyrics", label: "歌词" },
];

const DIR_FIELD_OPTIONS: { value: string; label: string }[] = [
  { value: "artist", label: "艺术家" },
  { value: "albumartist", label: "专辑艺术家" },
  { value: "album", label: "专辑" },
  { value: "year", label: "年份" },
  { value: "genre", label: "流派" },
];

function ToggleButton({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <Button
      type="button"
      size="sm"
      variant={active ? "default" : "outline"}
      className="h-7 px-2.5 text-xs"
      onClick={onClick}
    >
      {children}
    </Button>
  );
}

function formatTime(value?: string | null) {
  if (!value) return "尚未运行";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN");
}

const STATUS_META: Record<
  string,
  { label: string; className: string }
> = {
  ok: {
    label: "成功",
    className: "text-emerald-600 dark:text-emerald-400",
  },
  partial: {
    label: "部分失败",
    className: "text-amber-600 dark:text-amber-400",
  },
  error: {
    label: "失败",
    className: "text-destructive",
  },
};

function RunSummary({ data }: { data: PanelData }) {
  const last = data.last_run;
  const status = STATUS_META[last?.status ?? ""];

  return (
    <div className="space-y-2 rounded-lg border bg-muted/20 p-3 text-sm">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <Clock className="h-3.5 w-3.5 text-muted-foreground" />
        <span className="text-xs text-muted-foreground">最近运行</span>
        <span className="text-xs">{formatTime(last?.finished_at)}</span>
        {status && (
          <span className={cn("text-xs font-medium", status.className)}>
            {status.label}
          </span>
        )}
        {last?.trigger === "auto" && (
          <Badge variant="secondary" className="h-5 text-[11px]">
            定时
          </Badge>
        )}
      </div>
      {last?.message ? (
        <p className="text-xs text-muted-foreground">{last.message}</p>
      ) : (
        <p className="text-xs text-muted-foreground">
          启用后后端会按间隔自动扫描下载目录。
        </p>
      )}
      {last?.navidrome && (
        <p
          className={cn(
            "text-xs",
            last.navidrome.ok ? "text-emerald-600" : "text-amber-600"
          )}
        >
          Navidrome 扫描：{last.navidrome.message}
        </p>
      )}
      <div className="flex flex-wrap gap-3 text-[11px] text-muted-foreground">
        <span>已处理 {data.stats?.done ?? 0}</span>
        <span>待处理 {data.stats?.pending ?? 0}</span>
        <span>失败待重试 {data.stats?.failed ?? 0}</span>
      </div>
    </div>
  );
}

export function MusicAutoOrganizePanel({
  instanceId,
  appName,
}: {
  instanceId: number;
  appName: string;
}) {
  const { data, isLoading, refetch, isFetching } = useAppAutoOrganize(
    instanceId,
    appName
  );
  const saveMutation = useSaveAppAutoOrganize();
  const runMutation = useRunAppAutoOrganize();
  const [draft, setDraft] = useState<MusicAutoOrganizeConfig | null>(null);
  const initialized = useRef(false);

  useEffect(() => {
    if (!data?.config || initialized.current) return;
    setDraft(data.config);
    initialized.current = true;
  }, [data?.config]);

  if (isLoading || !draft) {
    return (
      <div className="flex items-center gap-2 py-8 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" />
        正在读取自动整理配置…
      </div>
    );
  }

  if (!data?.supported) {
    return (
      <div className="rounded-lg border border-dashed p-4 text-sm text-muted-foreground">
        该应用暂不支持自动整理，目前仅 Music Tag Web 可用。
      </div>
    );
  }

  const update = (patch: Partial<MusicAutoOrganizeConfig>) =>
    setDraft((prev) => (prev ? { ...prev, ...patch } : prev));

  const toggleListValue = (
    key: "source_list" | "modify_list",
    value: string
  ) => {
    const current = draft[key];
    update({
      [key]: current.includes(value)
        ? current.filter((item) => item !== value)
        : [...current, value],
    });
  };

  const updateDir = (index: number, patch: Partial<MusicAutoDirItem>) => {
    update({
      dir_list: draft.dir_list.map((item, itemIndex) =>
        itemIndex === index ? { ...item, ...patch } : item
      ),
    });
  };

  const addDir = () =>
    update({
      dir_list: [...draft.dir_list, { value: "album", value2: "" }],
    });

  const removeDir = (index: number) =>
    update({
      dir_list: draft.dir_list.filter((_, itemIndex) => itemIndex !== index),
    });

  const handleSave = () =>
    saveMutation.mutate(
      { instanceId, appName, config: draft },
      { onSuccess: (panel) => setDraft(panel.config) }
    );

  // 手动执行前先保存当前表单，避免运行的仍是旧目录/旧开关
  const handleRun = () =>
    saveMutation.mutate(
      { instanceId, appName, config: draft },
      {
        onSuccess: () => runMutation.mutate({ instanceId, appName }),
      }
    );

  const total = data.progress?.total ?? 0;
  const processed = data.progress?.processed ?? 0;
  const progressPercent = total > 0 ? Math.round((processed / total) * 100) : 0;

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <Wand2 className="h-4 w-4 text-primary" />
            <h3 className="text-sm font-medium">自动整理</h3>
            <Badge
              variant={draft.enabled ? "default" : "secondary"}
              className="h-5 text-[11px]"
            >
              {draft.enabled ? "已启用" : "未启用"}
            </Badge>
          </div>
          <p className="text-xs text-muted-foreground">
            后端按设定间隔扫描下载目录，调用 Music Tag Web 完成刮削，再按目录模板整理进曲库。
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            size="sm"
            variant="outline"
            className="gap-1.5"
            onClick={() => refetch()}
            disabled={isFetching}
          >
            <RefreshCw
              className={cn("h-3.5 w-3.5", isFetching && "animate-spin")}
            />
            刷新
          </Button>
          <Button
            size="sm"
            variant="outline"
            className="gap-1.5"
            onClick={handleRun}
            disabled={
              saveMutation.isPending || runMutation.isPending || data.running
            }
          >
            {data.running || runMutation.isPending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Play className="h-3.5 w-3.5" />
            )}
            立即整理
          </Button>
          <Button
            size="sm"
            className="gap-1.5"
            onClick={handleSave}
            disabled={saveMutation.isPending}
          >
            {saveMutation.isPending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <Save className="h-3.5 w-3.5" />
            )}
            保存配置
          </Button>
        </div>
      </div>

      {(data.running || data.progress?.message) && (
        <div className="space-y-2 rounded-lg border bg-card p-3">
          <div className="flex items-center justify-between gap-2 text-xs">
            <span className="flex min-w-0 items-center gap-1.5 font-medium">
              {data.running && (
                <Loader2 className="h-3.5 w-3.5 shrink-0 animate-spin text-primary" />
              )}
              <span className="truncate">
                {data.progress?.message || "等待执行"}
              </span>
            </span>
            {total > 0 && (
              <span className="shrink-0 text-muted-foreground">
                {processed}/{total}
              </span>
            )}
          </div>
          <Progress value={progressPercent} className="h-1.5" />
        </div>
      )}

      <RunSummary data={data} />

      <div className="grid gap-4 lg:grid-cols-2">
        <div className="space-y-3 rounded-lg border p-3">
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium">运行方式</span>
            <ToggleButton
              active={draft.enabled}
              onClick={() => update({ enabled: !draft.enabled })}
            >
              {draft.enabled ? "已启用" : "启用自动整理"}
            </ToggleButton>
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="music-interval">检查间隔（分钟）</Label>
              <Input
                id="music-interval"
                type="number"
                min={1}
                max={1440}
                value={draft.interval_minutes}
                onChange={(e) =>
                  update({ interval_minutes: Number(e.target.value) || 1 })
                }
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="music-max-files">每轮最多处理</Label>
              <Input
                id="music-max-files"
                type="number"
                min={1}
                max={200}
                value={draft.max_files_per_run}
                onChange={(e) =>
                  update({ max_files_per_run: Number(e.target.value) || 1 })
                }
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="music-min-age">文件稳定等待（秒）</Label>
              <Input
                id="music-min-age"
                type="number"
                min={0}
                max={86400}
                value={draft.min_age_seconds}
                onChange={(e) =>
                  update({ min_age_seconds: Number(e.target.value) || 0 })
                }
              />
            </div>
            <div className="space-y-1.5">
              <Label>执行内容</Label>
              <div className="flex flex-wrap gap-1.5">
                <ToggleButton
                  active={draft.scrape_enabled}
                  onClick={() =>
                    update({ scrape_enabled: !draft.scrape_enabled })
                  }
                >
                  自动刮削
                </ToggleButton>
                <ToggleButton
                  active={draft.tidy_enabled}
                  onClick={() => update({ tidy_enabled: !draft.tidy_enabled })}
                >
                  自动整理
                </ToggleButton>
              </div>
            </div>
          </div>
          <div className="flex items-center justify-between border-t pt-3">
            <div>
              <p className="text-xs font-medium">整理后触发 Navidrome 扫描</p>
              <p className="text-[11px] text-muted-foreground">
                实例组内存在已配置的 Navidrome 时生效
              </p>
            </div>
            <ToggleButton
              active={draft.navidrome_scan}
              onClick={() => update({ navidrome_scan: !draft.navidrome_scan })}
            >
              {draft.navidrome_scan ? "已开启" : "已关闭"}
            </ToggleButton>
          </div>
        </div>

        <div className="space-y-3 rounded-lg border p-3">
          <div className="flex items-center gap-1.5">
            <FolderTree className="h-4 w-4 text-muted-foreground" />
            <span className="text-sm font-medium">目录（容器内路径）</span>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="music-watch-dir">监控目录</Label>
            <Input
              id="music-watch-dir"
              className="font-mono text-xs"
              value={draft.watch_dir}
              onChange={(e) => update({ watch_dir: e.target.value })}
            />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="music-library-dir">整理后的曲库目录</Label>
            <Input
              id="music-library-dir"
              className="font-mono text-xs"
              value={draft.library_dir}
              onChange={(e) => update({ library_dir: e.target.value })}
            />
          </div>
          {data.detected && (
            <div className="flex flex-wrap items-center gap-2 text-[11px] text-muted-foreground">
              <span>从应用挂载检测到：</span>
              <button
                type="button"
                className="rounded bg-muted px-1.5 py-0.5 font-mono hover:bg-muted/70"
                onClick={() =>
                  update({
                    watch_dir: data.detected!.watch_dir,
                    library_dir: data.detected!.library_dir,
                  })
                }
              >
                {data.detected.watch_dir} → {data.detected.library_dir}
              </button>
            </div>
          )}
          <p className="text-[11px] text-muted-foreground">
            目录必须与 Music Tag Web 容器内的实际挂载一致；整理目录不能位于监控目录内部。
          </p>
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <div className="space-y-3 rounded-lg border p-3">
          <div>
            <span className="text-sm font-medium">刮削内容</span>
            <p className="text-[11px] text-muted-foreground">
              音乐源按顺序尝试；写入字段会与标题、艺术家、专辑等基础字段合并。
            </p>
          </div>
          <div className="space-y-1.5">
            <Label>音乐源</Label>
            <div className="flex flex-wrap gap-1.5">
              {SOURCE_OPTIONS.map((option) => (
                <ToggleButton
                  key={option.value}
                  active={draft.source_list.includes(option.value)}
                  onClick={() => toggleListValue("source_list", option.value)}
                >
                  {option.label}
                </ToggleButton>
              ))}
            </div>
          </div>
          <div className="space-y-1.5">
            <Label>写入字段</Label>
            <div className="flex flex-wrap gap-1.5">
              {MODIFY_OPTIONS.map((option) => (
                <ToggleButton
                  key={option.value}
                  active={draft.modify_list.includes(option.value)}
                  onClick={() => toggleListValue("modify_list", option.value)}
                >
                  {option.label}
                </ToggleButton>
              ))}
            </div>
          </div>
        </div>

        <div className="space-y-3 rounded-lg border p-3">
          <div className="flex items-center justify-between">
            <div>
              <span className="text-sm font-medium">整理目录层级</span>
              <p className="text-[11px] text-muted-foreground">
                例如「艺术家/专辑」会整理为 曲库/艺术家/专辑/歌曲。
              </p>
            </div>
            <Button
              size="sm"
              variant="outline"
              className="h-7 text-xs"
              onClick={addDir}
              disabled={draft.dir_list.length >= 4}
            >
              添加层级
            </Button>
          </div>
          <div className="space-y-2">
            {draft.dir_list.map((item, index) => (
              <div key={index} className="flex items-center gap-2">
                <select
                  className="h-9 w-32 rounded-md border border-input bg-background px-2 text-xs"
                  value={item.value}
                  onChange={(e) =>
                    updateDir(index, { value: e.target.value })
                  }
                >
                  {DIR_FIELD_OPTIONS.map((option) => (
                    <option key={option.value} value={option.value}>
                      {option.label}
                    </option>
                  ))}
                </select>
                <Input
                  className="h-9 flex-1 text-xs"
                  placeholder="自定义子目录名（可选）"
                  value={item.value2 ?? ""}
                  onChange={(e) =>
                    updateDir(index, { value2: e.target.value })
                  }
                />
                <Button
                  size="sm"
                  variant="ghost"
                  className="h-8 shrink-0 px-2 text-xs text-muted-foreground"
                  onClick={() => removeDir(index)}
                  disabled={draft.dir_list.length <= 1}
                >
                  删除
                </Button>
              </div>
            ))}
          </div>
        </div>
      </div>

      <div className="rounded-lg border bg-muted/20 p-3 text-[11px] text-muted-foreground">
        <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
          <span className="flex items-center gap-1">
            <CheckCircle2 className="h-3 w-3" />
            配置保存在自动化页的应用配置中，不修改应用镜像
          </span>
          <span className="flex items-center gap-1">
            <AlertTriangle className="h-3 w-3" />
            失败文件最多重试 5 次，间隔逐步延长
          </span>
        </div>
      </div>
    </div>
  );
}
