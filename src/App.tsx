/**
 * 应用根组件，配置路由表和鉴权守卫 ProtectedRoute
 */

import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { AuthProvider } from "./contexts/AuthContext";
import { DeployTaskProvider } from "./contexts/DeployTaskContext";
import { ToastProvider } from "./components/ui/toast";
import { DeployProgressDialog } from "./components/DeployProgressDialog";
import { useAuth } from "./hooks/useAuth";
import { AppLayout } from "./layouts/AppLayout";
import { LoginPage } from "./pages/auth/LoginPage";
import { DashboardPage } from "./pages/dashboard/DashboardPage";
import { AppsPage } from "./pages/apps/AppsPage";
import { AutomationCategoriesPage } from "./pages/automation/AutomationCategoriesPage";
import { AutomationCategoryPage } from "./pages/automation/AutomationCategoryPage";
import { DockerPage } from "./pages/docker/containers/DockerPage";
import { DockerHostPage } from "./pages/docker/host/DockerHostPage";
import { DockerImagesPage } from "./pages/docker/images/DockerImagesPage";
import { DockerStacksPage } from "./pages/docker/stacks/DockerStacksPage";
import { SettingsPage } from "./pages/settings/SettingsPage";
import { LicenseGuard } from "./components/LicenseGuard";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      refetchOnWindowFocus: false,
      refetchOnReconnect: true,
    },
  },
});

/**
 * 鉴权路由守卫
 *
 * 检查用户是否已登录（通过 localStorage token 判断）。
 * 未登录时重定向到 /login，已登录时渲染子内容。
 */
function ProtectedRoute({ children }: { children: React.ReactNode }) {
  const { isAuthenticated, isLoading } = useAuth();

  // 认证状态初始化中，显示空白占位避免闪烁
  if (isLoading) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-muted/30">
        <div className="animate-spin h-8 w-8 border-2 border-primary border-t-transparent rounded-full" />
      </div>
    );
  }

  if (!isAuthenticated) {
    return <Navigate to="/login" replace />;
  }

  return <>{children}</>;
}

export default function App() {
  return (
    <QueryClientProvider client={queryClient}>
      <AuthProvider>
        <ToastProvider>
          <DeployTaskProvider>
            <BrowserRouter>
              <Routes>
                <Route path="/login" element={<LoginPage />} />
                <Route
                  element={
                    <ProtectedRoute>
                      <AppLayout />
                    </ProtectedRoute>
                  }
                >
                  {/* 核心入口 */}
                  <Route path="/" element={<DashboardPage />} />
                  <Route path="/appstore" element={<AppsPage />} />

                  {/* 自动化管理 */}
                  <Route
                    path="/automation"
                    element={
                      <LicenseGuard>
                        <AutomationCategoriesPage />
                      </LicenseGuard>
                    }
                  />
                  <Route
                    path="/automation/:category"
                    element={
                      <LicenseGuard>
                        <AutomationCategoryPage />
                      </LicenseGuard>
                    }
                  />

                  {/* Docker 管理 */}
                  <Route
                    path="/docker/stacks"
                    element={<DockerStacksPage />}
                  />
                  <Route path="/docker/containers" element={<DockerPage />} />
                  <Route path="/docker/images" element={<DockerImagesPage />} />
                  <Route path="/docker/host" element={<DockerHostPage />} />

                  {/* 系统设置 */}
                  <Route path="/settings" element={<SettingsPage />} />

                  {/* 旧路由重定向 */}
                  <Route
                    path="/docker"
                    element={<Navigate to="/docker/containers" replace />}
                  />
                </Route>
              </Routes>
            </BrowserRouter>
            <DeployProgressDialog />
          </DeployTaskProvider>
        </ToastProvider>
      </AuthProvider>
    </QueryClientProvider>
  );
}
