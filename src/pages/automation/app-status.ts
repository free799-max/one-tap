/**
 * 应用运行状态的中文展示元信息
 *
 * 供实例组应用列表与配置面板共用，保证状态展示一致。
 */

export const APP_STATUS_META: Record<
  string,
  { label: string; dot: string; text: string }
> = {
  running: {
    label: "运行中",
    dot: "bg-emerald-500",
    text: "text-emerald-600",
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
  deploying: {
    label: "部署中",
    dot: "bg-primary",
    text: "text-primary",
  },
};

/** 应用状态胶囊的背景与文字配色 */
export const APP_STATUS_PILL: Record<string, string> = {
  running: "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400",
  stopped: "bg-muted text-muted-foreground",
  error: "bg-destructive/10 text-destructive",
  deploying: "bg-primary/10 text-primary",
};
