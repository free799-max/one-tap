/**
 * 顶栏通知铃铛：未读角标 + 最近通知下拉。
 */

import { useEffect, useRef, useState } from "react";
import {
  Bell,
  BookOpen,
  CheckCheck,
  Film,
  Image as ImageIcon,
  Loader2,
  Music,
} from "lucide-react";
import type { LucideIcon } from "lucide-react";
import {
  useMarkAllNotificationsRead,
  useMarkNotificationRead,
  useUnreadNotifications,
} from "@/hooks/useNotifications";
import { formatRelativeTime } from "@/lib/utils";
import { cn } from "@/lib/utils";

const DOMAIN_META: Record<
  string,
  { label: string; icon: LucideIcon; tone: string }
> = {
  video: { label: "影视", icon: Film, tone: "bg-sky-50 text-sky-500" },
  comic: { label: "漫画", icon: ImageIcon, tone: "bg-violet-50 text-violet-500" },
  novel: { label: "小说", icon: BookOpen, tone: "bg-amber-50 text-amber-500" },
  music: { label: "音乐", icon: Music, tone: "bg-emerald-50 text-emerald-500" },
};

/** 卡片只展示纯文本，并去掉与标题重复的开头。 */
function previewText(title: string, markdown: string) {
  let text = (markdown || "")
    .replace(/!\[[^\]]*\]\([^)]+\)/g, "")
    .replace(/\[([^\]]+)\]\([^)]+\)/g, "$1")
    .replace(/[*_`#>]/g, "")
    .replace(/\s*\n+\s*/g, " ")
    .trim();
  const headline = (title || "").replace(/[*_`#>]/g, "").trim();
  if (headline && text.startsWith(headline)) {
    text = text.slice(headline.length).trim();
  }
  // 老通知的正文里还会再写一遍资源名，标题已经展示过
  const name = /^《([^》]+)》/.exec(headline)?.[1];
  if (name && text.startsWith(name)) {
    text = text
      .slice(name.length)
      .replace(/^[（(][^）)]*[）)]/, "")
      .trim();
  }
  return text;
}

export function NotificationBell() {
  const [open, setOpen] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const query = useUnreadNotifications();
  const markRead = useMarkNotificationRead();
  const markAll = useMarkAllNotificationsRead();
  const unread = query.data?.unread ?? 0;
  const items = query.data?.items ?? [];

  useEffect(() => {
    function handleClickOutside(event: MouseEvent) {
      if (
        containerRef.current &&
        !containerRef.current.contains(event.target as Node)
      ) {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  const handleOpen = (item: (typeof items)[number]) => {
    if (!item.read_at) markRead.mutate(item.id);
    if (item.link) window.open(item.link, "_blank", "noreferrer");
    setOpen(false);
  };

  const handleToggle = () => {
    const next = !open;
    setOpen(next);
    // 点开通知面板即视为已读，未读角标同步清零
    if (next && unread > 0 && !markAll.isPending) {
      markAll.mutate();
    }
  };

  return (
    <div className="relative" ref={containerRef}>
      <button
        onClick={handleToggle}
        aria-label="通知"
        className={cn(
          "h-8 w-8 rounded-md flex items-center justify-center text-muted-foreground transition-colors relative",
          open ? "bg-primary/10 text-primary" : "hover:text-foreground hover:bg-muted"
        )}
      >
        <Bell className="h-4 w-4" />
        {unread > 0 && (
          <span className="absolute -right-0.5 -top-0.5 h-4 min-w-4 px-1 rounded-full bg-destructive text-destructive-foreground text-[10px] leading-4 text-center">
            {unread > 99 ? "99+" : unread}
          </span>
        )}
      </button>

      {open && (
        <div className="absolute right-0 mt-2 w-[26rem] max-w-[calc(100vw-2rem)] rounded-xl bg-white shadow-lg border border-border/60 z-50 overflow-hidden">
          <div className="flex items-center justify-between px-4 py-2.5 border-b border-border/40">
            <span className="flex items-baseline gap-1.5">
              <span className="text-[13px] font-medium">通知</span>
              {unread > 0 && (
                <span className="text-[10px] text-muted-foreground">
                  {unread} 条未读
                </span>
              )}
            </span>
            <button
              onClick={() => markAll.mutate()}
              disabled={unread === 0 || markAll.isPending}
              className="flex items-center gap-1 text-[11px] text-muted-foreground hover:text-foreground disabled:opacity-50"
            >
              <CheckCheck className="h-3.5 w-3.5" />
              全部已读
            </button>
          </div>

          <div className="max-h-96 overflow-y-auto">
            {query.isLoading ? (
              <div className="flex items-center gap-2 px-4 py-6 text-xs text-muted-foreground">
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                加载中…
              </div>
            ) : items.length === 0 ? (
              <div className="px-4 py-10 text-center text-xs text-muted-foreground">
                暂无通知
              </div>
            ) : (
              items.map((item) => {
                const meta = DOMAIN_META[item.domain ?? ""] ?? DOMAIN_META.video;
                const Icon = meta.icon;
                const preview = previewText(item.title, item.markdown);
                return (
                  <button
                    key={item.id}
                    onClick={() => handleOpen(item)}
                    className="w-full flex gap-3 px-4 py-3 text-left hover:bg-muted/60 transition-colors border-b border-border/30 last:border-b-0"
                  >
                    {item.image_proxy_path ? (
                      <img
                        src={item.image_proxy_path}
                        alt=""
                        className="h-14 w-10 shrink-0 rounded-md object-cover bg-muted"
                        loading="lazy"
                      />
                    ) : (
                      <div
                        className={cn(
                          "flex h-14 w-10 shrink-0 items-center justify-center rounded-md",
                          meta.tone
                        )}
                        aria-label={meta.label}
                      >
                        <Icon className="h-4 w-4" />
                      </div>
                    )}
                    <div className="min-w-0 flex-1">
                      <div className="flex items-start gap-2">
                        <span className="flex min-w-0 flex-1 items-center gap-1.5">
                          {!item.read_at && (
                            <span className="h-1.5 w-1.5 shrink-0 rounded-full bg-primary" />
                          )}
                          <span className="min-w-0 truncate text-[13px] font-medium leading-5">
                            {item.title}
                          </span>
                        </span>
                        <span className="shrink-0 pt-0.5 text-[10px] leading-4 text-muted-foreground/80">
                          {formatRelativeTime(item.created_at)}
                        </span>
                      </div>
                      {preview && (
                        <p className="mt-0.5 text-[11px] leading-4 text-muted-foreground line-clamp-2">
                          {preview}
                        </p>
                      )}
                    </div>
                  </button>
                );
              })
            )}
          </div>

          <div className="px-4 py-2 border-t border-border/40 text-center">
            <a
              href="/settings?tab=notifications"
              className="text-[11px] text-primary hover:underline"
              onClick={() => setOpen(false)}
            >
              通知与渠道设置
            </a>
          </div>
        </div>
      )}
    </div>
  );
}
