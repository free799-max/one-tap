/**
 * 应用整体布局组件
 *
 * 布局结构：
 * ┌──────────────────────────────────┐
 * │  Sidebar  │      TopBar          │
 * │  (左侧)   │─────────────────────│
 * │           │                      │
 * │           │   主内容区 (Outlet)   │
 * │           │                      │
 * └──────────────────────────────────┘
 */

import { useState } from "react";
import { Outlet } from "react-router-dom";
import { Sidebar } from "./Sidebar";
import { TopBar } from "./TopBar";
import { ExternalImportDialog } from "@/components/ExternalImportDialog";

export function AppLayout() {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [importOpen, setImportOpen] = useState(false);

  return (
    <div className="flex h-full overflow-hidden bg-background">
      {/* 左侧侧边栏 */}
      <Sidebar collapsed={sidebarCollapsed} />
      {/* 右侧区域 */}
      <div className="flex min-w-0 flex-1 flex-col overflow-hidden">
        {/* 顶部栏组件 */}
        <TopBar
          sidebarCollapsed={sidebarCollapsed}
          onToggleSidebar={() => setSidebarCollapsed((v) => !v)}
          onImport={() => setImportOpen(true)}
        />
        {/* 主内容区域 */}
        <main className="min-h-0 flex-1 overflow-auto px-4 pt-4 pb-0">
          <Outlet />
        </main>
      </div>
      {/* 登录后提醒 + 顶部栏手动打开的外部应用导入弹窗 */}
      <ExternalImportDialog open={importOpen} onOpenChange={setImportOpen} />
    </div>
  );
}
