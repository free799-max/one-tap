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

export function AppLayout() {
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);

  return (
    <div className="flex h-screen bg-background">
      {/* 左侧侧边栏 */}
      <Sidebar collapsed={sidebarCollapsed} />
      {/* 右侧区域 */}
      <div className="flex-1 flex flex-col overflow-hidden">
        {/* 顶部栏组件 */}
        <TopBar
          sidebarCollapsed={sidebarCollapsed}
          onToggleSidebar={() => setSidebarCollapsed((v) => !v)}
        />
        {/* 主内容区域 */}
        <main className="flex-1 overflow-auto p-4">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
