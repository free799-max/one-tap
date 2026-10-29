/**
 * Docker 页面通用图标操作按钮
 *
 * 带 Tooltip 的图标按钮，用于容器列表、Compose 卡片等场景。
 */

import { useState, useRef, useEffect } from "react";
import { createPortal } from "react-dom";
import { Button } from "@/components/ui/button";

interface ActionButtonProps {
  icon: React.ComponentType<{ className?: string }>;
  title: string;
  onClick: (e: React.MouseEvent<HTMLButtonElement>) => void;
  disabled?: boolean;
  className?: string;
}

export function ActionButton({
  icon: Icon,
  title,
  onClick,
  disabled,
  className,
}: ActionButtonProps) {
  const [show, setShow] = useState(false);
  const buttonRef = useRef<HTMLButtonElement>(null);
  const [tooltipPos, setTooltipPos] = useState({ left: 0, top: 0 });

  useEffect(() => {
    if (show && buttonRef.current) {
      const rect = buttonRef.current.getBoundingClientRect();
      setTooltipPos({
        left: rect.left + rect.width / 2,
        top: rect.bottom + 8,
      });
    }
  }, [show]);

  return (
    <>
      <Button
        ref={buttonRef}
        size="icon-xs"
        variant="ghost"
        className={
          className ||
          "h-5 w-5 p-0 text-muted-foreground hover:text-primary disabled:opacity-40"
        }
        onClick={onClick}
        onMouseEnter={() => setShow(true)}
        onMouseLeave={() => setShow(false)}
        disabled={disabled}
      >
        <Icon className="size-4" />
      </Button>
      {show &&
        createPortal(
          <div
            style={{
              position: "fixed",
              left: tooltipPos.left,
              top: tooltipPos.top,
              transform: "translateX(-50%)",
            }}
            className="px-2.5 py-1 bg-background text-muted-foreground text-xs font-medium rounded-full border shadow-md whitespace-nowrap z-[100] pointer-events-none"
          >
            {title}
          </div>,
          document.body
        )}
    </>
  );
}
