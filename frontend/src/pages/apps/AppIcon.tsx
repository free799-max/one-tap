/**
 * 应用图标组件
 *
 * 当 `icon` 为 http(s) 外链或以 `/` 开头的本地路径（如后端缓存接口
 * `/api/apps/<name>/icon`）时直接使用，否则回退到前端静态资源
 * `/icons/apps/<name>.svg`，加载失败时显示火箭占位。
 */

import { useState } from "react";
import { Rocket } from "lucide-react";
import { cn } from "@/lib/utils";
import type { App } from "@/hooks/useApps";

interface AppIconProps {
  app: Pick<App, "name" | "icon">;
  className?: string;
  /** 不画白色底框，只显示图标本身（用于方案流程等不需要卡片底色的场景） */
  frameless?: boolean;
}

export function AppIcon({ app, className, frameless = false }: AppIconProps) {
  const [failed, setFailed] = useState(false);

  const src =
    app.icon?.startsWith("http") || app.icon?.startsWith("/")
      ? app.icon
      : `/icons/apps/${app.name}.svg`;

  if (failed) {
    return (
      <div
        className={cn(
          "flex items-center justify-center rounded-[22%] bg-gradient-to-br from-primary/15 to-primary/5 text-primary",
          className
        )}
      >
        <Rocket className="h-1/2 w-1/2" />
      </div>
    );
  }

  return (
    <div
      className={cn(
        "relative flex items-center justify-center overflow-hidden rounded-[22%]",
        !frameless && "bg-white",
        className
      )}
    >
      <img
        src={src}
        alt={app.name}
        className="h-full w-full object-contain"
        loading="lazy"
        decoding="async"
        onError={() => setFailed(true)}
      />
    </div>
  );
}
