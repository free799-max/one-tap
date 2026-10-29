/**
 * Popover 浮动面板组件
 *
 * 基于 @base-ui/react/popover 封装，用于触发元素旁展开浮动内容。
 * 样式与现有 shadcn/ui 风格保持一致。
 */

import { Popover as PopoverPrimitive } from "@base-ui/react/popover";
import { cn } from "@/lib/utils";

/** Popover 根组件 */
export function Popover({ children, ...props }: PopoverPrimitive.Root.Props) {
  return <PopoverPrimitive.Root {...props}>{children}</PopoverPrimitive.Root>;
}

/** Popover 触发器 */
export function PopoverTrigger({
  className,
  children,
  ...props
}: PopoverPrimitive.Trigger.Props) {
  return (
    <PopoverPrimitive.Trigger
      className={cn(
        "inline-flex items-center justify-center outline-none",
        className
      )}
      {...props}
    >
      {children}
    </PopoverPrimitive.Trigger>
  );
}

/** Popover 弹出内容（已包含 Portal + Positioner + 样式） */
export function PopoverContent({
  className,
  children,
  ...props
}: PopoverPrimitive.Popup.Props) {
  return (
    <PopoverPrimitive.Portal>
      <PopoverPrimitive.Positioner sideOffset={6} align="end">
        <PopoverPrimitive.Popup
          className={cn(
            "z-50 w-96 max-w-[calc(100vw-2rem)] rounded-xl border bg-popover p-3 text-popover-foreground shadow-lg outline-none origin-top-right transition-[transform,opacity] duration-150 data-[starting-style]:scale-95 data-[starting-style]:opacity-0 data-[ending-style]:scale-95 data-[ending-style]:opacity-0",
            className
          )}
          {...props}
        >
          {children}
        </PopoverPrimitive.Popup>
      </PopoverPrimitive.Positioner>
    </PopoverPrimitive.Portal>
  );
}

/** Popover 箭头 */
export function PopoverArrow({ className, ...props }: PopoverPrimitive.Arrow.Props) {
  return (
    <PopoverPrimitive.Arrow
      className={cn("fill-popover stroke-border", className)}
      {...props}
    />
  );
}

/** Popover 关闭按钮 */
export function PopoverClose({ className, ...props }: PopoverPrimitive.Close.Props) {
  return (
    <PopoverPrimitive.Close
      className={cn(
        "inline-flex items-center justify-center rounded-md text-sm font-medium outline-none transition-colors hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring",
        className
      )}
      {...props}
    />
  );
}

/** Popover 标题 */
export function PopoverTitle({ className, ...props }: PopoverPrimitive.Title.Props) {
  return (
    <PopoverPrimitive.Title
      className={cn("text-sm font-medium", className)}
      {...props}
    />
  );
}

/** Popover 描述 */
export function PopoverDescription({
  className,
  ...props
}: PopoverPrimitive.Description.Props) {
  return (
    <PopoverPrimitive.Description
      className={cn("text-sm text-muted-foreground", className)}
      {...props}
    />
  );
}
