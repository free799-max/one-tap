import { useEffect, useMemo, useRef, useState } from "react";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { useImageTags } from "@/hooks/useDocker";
import { cn, formatBytes, formatRelativeTime } from "@/lib/utils";
import {
  Check,
  ChevronDown,
  Clock,
  HardDrive,
  Loader2,
  RefreshCw,
} from "lucide-react";

export interface ContainerImageConfig {
  repository: string;
  tag: string;
}

function splitImageReference(image: string): {
  repository: string;
  tag: string | null;
} {
  const value = image.trim();
  const lastSlash = value.lastIndexOf("/");
  const lastColon = value.lastIndexOf(":");
  if (lastColon > lastSlash) {
    return {
      repository: value.slice(0, lastColon),
      tag: value.slice(lastColon + 1),
    };
  }
  return { repository: value, tag: null };
}

interface ContainerImageFieldProps {
  containerName: string;
  defaultImage: string;
  value?: ContainerImageConfig;
  onChange: (value: ContainerImageConfig) => void;
}

export function ContainerImageField({
  containerName,
  defaultImage,
  value,
  onChange,
}: ContainerImageFieldProps) {
  const parsedDefault = splitImageReference(defaultImage);
  const current = value || {
    repository: parsedDefault.repository,
    tag: parsedDefault.tag || "latest",
  };
  const [open, setOpen] = useState(false);
  const [tagQuery, setTagQuery] = useState("");
  const [refreshToken, setRefreshToken] = useState(0);
  const containerRef = useRef<HTMLDivElement>(null);

  const {
    data: imageTags = [],
    isFetching,
    error,
  } = useImageTags(current.repository, refreshToken);

  // 点击面板外部或按 Esc 收起
  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: PointerEvent) => {
      if (!containerRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setOpen(false);
    };
    document.addEventListener("pointerdown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("pointerdown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  // latest 置顶，其余按推送时间倒序
  const sortedTags = useMemo(
    () =>
      [...imageTags].sort((a, b) => {
        const aLatest = a.name === "latest" ? 1 : 0;
        const bLatest = b.name === "latest" ? 1 : 0;
        if (aLatest !== bLatest) return bLatest - aLatest;
        return (
          new Date(b.last_updated || 0).getTime() -
          new Date(a.last_updated || 0).getTime()
        );
      }),
    [imageTags]
  );

  const visibleTags = useMemo(() => {
    const q = tagQuery.trim().toLowerCase();
    if (!q) return sortedTags;
    return sortedTags.filter((tag) => tag.name.toLowerCase().includes(q));
  }, [sortedTags, tagQuery]);

  const selectedMeta = sortedTags.find((tag) => tag.name === current.tag);
  const manualTag = current.tag && !selectedMeta ? current.tag : "";
  const tagError = error as { displayMessage?: string; message?: string } | null;

  const selectTag = (tag: string) => {
    onChange({ repository: current.repository, tag });
    setOpen(false);
    setTagQuery("");
  };

  const retry = () => setRefreshToken((token) => token + 1);

  const toggleOpen = () => setOpen((prev) => !prev);

  const repositoryId = `image-repository-${containerName}`;
  const tagId = `image-tag-${containerName}`;

  return (
    <div className="mb-3 grid grid-cols-1 gap-3 sm:grid-cols-[minmax(0,1fr)_260px]">
      <div className="space-y-1">
        <Label htmlFor={repositoryId} className="text-xs font-medium">
          镜像地址
          <span className="ml-0.5 text-destructive" aria-hidden="true">*</span>
        </Label>
        <Input
          id={repositoryId}
          value={current.repository}
          readOnly
          aria-readonly="true"
          title="镜像地址由管理端方案或应用默认值决定，不可修改"
          autoComplete="off"
          className="h-8 cursor-default rounded-md border-input bg-muted/40 px-2 text-sm text-muted-foreground shadow-none focus-visible:ring-0"
        />
      </div>

      <div className="space-y-1">
        <Label htmlFor={tagId} className="text-xs font-medium">
          镜像标签
          <span className="ml-0.5 text-destructive" aria-hidden="true">*</span>
        </Label>
        <div ref={containerRef} className="relative">
          <div
            className={cn(
              "flex h-8 items-center rounded-md border border-input bg-white transition-colors focus-within:ring-1 focus-within:ring-ring",
              open && "border-primary/50 ring-1 ring-primary/20"
            )}
          >
            <Input
              id={tagId}
              value={current.tag}
              onChange={(event) => {
                const tag = event.target.value;
                onChange({ repository: current.repository, tag });
                setTagQuery(tag);
                setOpen(true);
              }}
              onFocus={() => setOpen(true)}
              placeholder="latest 或自定义标签"
              autoComplete="off"
              className="h-8 flex-1 rounded-md border-0 bg-transparent px-2 text-sm shadow-none focus-visible:ring-0"
            />
            {isFetching && (
              <Loader2 className="h-3 w-3 shrink-0 animate-spin text-muted-foreground" />
            )}
            <button
              type="button"
              onClick={toggleOpen}
              aria-label="浏览镜像标签"
              aria-expanded={open}
              className="px-2 text-muted-foreground transition-colors hover:text-foreground"
            >
              <ChevronDown
                className={cn(
                  "h-3.5 w-3.5 shrink-0 transition-transform",
                  open && "rotate-180"
                )}
              />
            </button>
          </div>

          {open && (
            <div
              role="listbox"
              aria-labelledby={tagId}
              className="absolute right-0 top-full z-30 mt-1 w-full rounded-xl border bg-popover p-2 text-popover-foreground shadow-lg"
            >
              {isFetching && sortedTags.length === 0 ? (
                <div className="flex items-center justify-center gap-2 py-8 text-xs text-muted-foreground">
                  <Loader2 className="h-4 w-4 animate-spin" />
                  正在加载标签…
                </div>
              ) : sortedTags.length === 0 ? (
                <div className="py-6 text-center">
                  <p className="text-xs text-muted-foreground">
                    {tagError
                      ? tagError.displayMessage || tagError.message
                      : "未获取到标签，可能是网络异常或 Docker Hub 限流"}
                  </p>
                  <p className="mt-1 text-xs text-muted-foreground">
                    可直接在输入框中填写标签
                  </p>
                  <button
                    type="button"
                    onClick={retry}
                    disabled={isFetching}
                    className="mt-2 inline-flex items-center gap-1 rounded-md border px-2 py-1 text-xs text-foreground transition-colors hover:bg-muted disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {isFetching ? (
                      <Loader2 className="h-3 w-3 animate-spin" />
                    ) : (
                      <RefreshCw className="h-3 w-3" />
                    )}
                    {isFetching ? "获取中…" : "重新获取"}
                  </button>
                </div>
              ) : (
                <div className="-mr-2 max-h-72 space-y-0.5 overflow-y-auto pr-2 [scrollbar-width:thin] [&::-webkit-scrollbar]:w-1 [&::-webkit-scrollbar-track]:bg-transparent [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-muted-foreground/30 [&::-webkit-scrollbar-thumb:hover]:bg-muted-foreground/50">
                  {manualTag && (
                    <button
                      type="button"
                      role="option"
                      aria-selected
                      onClick={() => selectTag(manualTag)}
                      className="flex w-full items-center gap-2.5 rounded-lg border border-primary/40 bg-primary/5 px-2.5 py-2 text-left"
                    >
                      <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full border border-primary bg-primary text-primary-foreground">
                        <Check className="h-2.5 w-2.5" />
                      </span>
                      <span className="min-w-0 flex-1 truncate text-sm font-medium text-primary">
                        {manualTag}
                      </span>
                      <span className="shrink-0 text-[11px] text-muted-foreground">
                        自定义
                      </span>
                    </button>
                  )}

                  {visibleTags.map((tag) => {
                    const selected = tag.name === current.tag;
                    const hasMeta = tag.size > 0 || !!tag.last_updated;
                    return (
                      <button
                        type="button"
                        role="option"
                        aria-selected={selected}
                        key={tag.name}
                        onClick={() => selectTag(tag.name)}
                        className={cn(
                          "flex w-full items-center gap-2.5 rounded-lg border px-2.5 py-2 text-left transition-colors",
                          selected
                            ? "border-primary/40 bg-primary/5"
                            : "border-transparent hover:bg-muted/60"
                        )}
                      >
                        <span
                          className={cn(
                            "flex h-4 w-4 shrink-0 items-center justify-center rounded-full border",
                            selected
                              ? "border-primary bg-primary text-primary-foreground"
                              : "border-muted-foreground/30"
                          )}
                        >
                          {selected && <Check className="h-2.5 w-2.5" />}
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className="flex items-center gap-1.5">
                            <span
                              className={cn(
                                "truncate text-sm",
                                selected
                                  ? "font-medium text-primary"
                                  : "text-foreground"
                              )}
                            >
                              {tag.name}
                            </span>
                            {tag.name === "latest" && (
                              <span className="shrink-0 rounded border border-primary/20 bg-primary/10 px-1 text-[10px] leading-4 text-primary">
                                最新
                              </span>
                            )}
                          </span>
                          {hasMeta && (
                            <span className="mt-0.5 flex items-center gap-2.5 text-[11px] text-muted-foreground">
                              {tag.size > 0 && (
                                <span className="flex items-center gap-1">
                                  <HardDrive className="h-3 w-3" />
                                  {formatBytes(tag.size)}
                                </span>
                              )}
                              {tag.last_updated && (
                                <span className="flex items-center gap-1">
                                  <Clock className="h-3 w-3" />
                                  {formatRelativeTime(tag.last_updated)}
                                </span>
                              )}
                            </span>
                          )}
                        </span>
                      </button>
                    );
                  })}

                  {visibleTags.length === 0 && (
                    <p className="py-6 text-center text-xs text-muted-foreground">
                      未找到匹配的标签，可直接使用输入的标签
                    </p>
                  )}
                </div>
              )}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
