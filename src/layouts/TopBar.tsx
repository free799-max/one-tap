/**
 * 顶部栏组件 - 极简风格
 *
 * 包含帮助、通知、用户菜单
 */

import { useState, useRef, useEffect } from "react";
import {
  Bell,
  HelpCircle,
  User,
  LogOut,
  PanelLeft,
  PanelLeftClose,
  Loader2,
} from "lucide-react";
import { useAuth } from "@/hooks/useAuth";
import { useDeployTasks } from "@/hooks/useDeployTasks";

interface TopBarProps {
  sidebarCollapsed: boolean;
  onToggleSidebar: () => void;
}

export function TopBar({ sidebarCollapsed, onToggleSidebar }: TopBarProps) {
  const { user, logout } = useAuth();
  const { activeTaskIds, isPanelOpen, openPanel } = useDeployTasks();
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  /**
   * 点击外部关闭用户菜单
   */
  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setMenuOpen(false);
      }
    }
    document.addEventListener("mousedown", handleClickOutside);
    return () => document.removeEventListener("mousedown", handleClickOutside);
  }, []);

  return (
    <header className="h-11 flex items-center justify-between px-4 bg-white border-b border-border/60 shrink-0">
      {/* 左侧：侧边栏收起按钮 */}
      <button
        onClick={onToggleSidebar}
        className="h-8 w-8 rounded-md flex items-center justify-center text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
        title={sidebarCollapsed ? "展开导航" : "收起导航"}
      >
        {sidebarCollapsed ? (
          <PanelLeft className="h-4 w-4" />
        ) : (
          <PanelLeftClose className="h-4 w-4" />
        )}
      </button>

      {/* 右侧：帮助 + 通知 + 部署任务入口 + 用户菜单 */}
      <div className="flex items-center gap-1">
        <button className="h-8 w-8 rounded-md flex items-center justify-center text-muted-foreground hover:text-foreground hover:bg-muted transition-colors">
          <HelpCircle className="h-4 w-4" />
        </button>
        <button className="h-8 w-8 rounded-md flex items-center justify-center text-muted-foreground hover:text-foreground hover:bg-muted transition-colors relative">
          <Bell className="h-4 w-4" />
          <span className="absolute top-1 right-1 h-2 w-2 rounded-full bg-red-500 ring-2 ring-white" />
        </button>

        {activeTaskIds.length > 0 && (
          <button
            onClick={openPanel}
            title="查看任务进度"
            className={`h-8 pl-2 pr-2 rounded-md flex items-center gap-1.5 text-xs font-medium transition-colors relative ${
              isPanelOpen
                ? "bg-primary/10 text-primary"
                : "text-muted-foreground hover:text-foreground hover:bg-muted"
            }`}
          >
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            <span>任务中</span>
            <span className="h-4 min-w-4 px-1 rounded-full bg-primary text-primary-foreground text-[10px] flex items-center justify-center">
              {activeTaskIds.length}
            </span>
          </button>
        )}

        {/* 用户菜单 */}
        <div className="relative" ref={menuRef}>
          <button
            onClick={() => setMenuOpen(!menuOpen)}
            className="h-8 pl-2 pr-2.5 rounded-md flex items-center gap-2 text-muted-foreground hover:text-foreground hover:bg-muted transition-colors"
            title={user?.username ?? "用户"}
          >
            <div className="h-6 w-6 rounded-full bg-primary/10 flex items-center justify-center">
              <User className="h-3.5 w-3.5 text-primary" />
            </div>
            <span className="text-xs font-medium max-w-[80px] truncate">
              {user?.username ?? "用户"}
            </span>
          </button>

          {/* 下拉菜单 */}
          {menuOpen && (
            <div className="absolute right-0 mt-2 w-44 rounded-xl bg-white shadow-lg border border-border/60 py-1 z-50">
              <div className="px-3 py-2 border-b border-border/40">
                <p className="text-xs font-medium text-foreground truncate">
                  {user?.username}
                </p>
                <p className="text-[10px] text-muted-foreground">
                  {user?.role === "admin" ? "管理员" : "普通用户"}
                </p>
              </div>
              <button
                onClick={() => {
                  setMenuOpen(false);
                  logout();
                }}
                className="w-full flex items-center gap-2 px-3 py-2 text-xs text-destructive hover:bg-destructive/5 transition-colors"
              >
                <LogOut className="h-4 w-4" />
                退出登录
              </button>
            </div>
          )}
        </div>
      </div>
    </header>
  );
}
