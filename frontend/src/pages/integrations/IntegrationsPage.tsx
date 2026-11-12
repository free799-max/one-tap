/**
 * 应用集成页面
 *
 * 访问入口（Lucky）：自动配置反向代理、域名转发与 DDNS。
 * 网络优化（AdGuard）：按应用注入 DNS，把域名解析到指定或优选 IP。
 * 网络加速（VLESS Reality）：指定域名走自建节点，其余请求直连。
 * 认证集成（Vaultwarden）：同步已部署应用的账号密码。
 *
 * 网络相关能力在前，因为 Vaultwarden 需要先有 HTTPS 访问入口。
 */

import { useSearchParams } from "react-router-dom";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { AuthIntegrationTab } from "./AuthIntegrationTab";
import { NetworkIntegrationTab } from "./NetworkIntegrationTab";
import { HostsOptimizerTab } from "./HostsOptimizerTab";
import { AccelerationTab } from "./AccelerationTab";
import { KeyRound, Network, Route, Zap } from "lucide-react";
import { useLicenseStatus } from "@/hooks/useLicense";

// 专属加速开通后展示网络加速，未开通时展示免费的 DNS 网络优化，二者互斥。
const FREE_TABS = new Set(["auth", "network", "hosts"]);
const LICENSED_TABS = new Set(["auth", "network", "accel"]);

const TAB_CLASS =
  "h-12 flex-none gap-2 rounded-none px-0 text-sm text-muted-foreground transition-colors after:hidden hover:text-foreground data-active:bg-transparent data-active:font-semibold data-active:text-primary";

export function IntegrationsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const { data: license } = useLicenseStatus();
  const visibleTabs = license?.valid ? LICENSED_TABS : FREE_TABS;
  const requestedTab = searchParams.get("tab") ?? "network";
  const activeTab = visibleTabs.has(requestedTab) ? requestedTab : "network";

  const handleTabChange = (value: string) => {
    const nextParams = new URLSearchParams(searchParams);
    if (value === "network") nextParams.delete("tab");
    else nextParams.set("tab", value);
    setSearchParams(nextParams);
  };

  return (
    <div className="w-full">
      <Tabs value={activeTab} onValueChange={handleTabChange} className="-mt-4 w-full">
        <TabsList
          variant="line"
          className="h-12! w-full justify-start gap-7 rounded-none border-b border-border bg-transparent p-0"
        >
          <TabsTrigger value="network" className={TAB_CLASS}>
            <Route className="h-4 w-4" aria-hidden="true" />
            访问入口
          </TabsTrigger>
          {visibleTabs.has("hosts") && (
            <TabsTrigger value="hosts" className={TAB_CLASS}>
              <Zap className="h-4 w-4" aria-hidden="true" />
              网络优化
            </TabsTrigger>
          )}
          {visibleTabs.has("accel") && (
            <TabsTrigger value="accel" className={TAB_CLASS}>
              <Network className="h-4 w-4" aria-hidden="true" />
              网络加速
            </TabsTrigger>
          )}
          <TabsTrigger value="auth" className={TAB_CLASS}>
            <KeyRound className="h-4 w-4" aria-hidden="true" />
            认证集成
          </TabsTrigger>
        </TabsList>

        <TabsContent value="network" className="mt-2">
          <NetworkIntegrationTab />
        </TabsContent>

        {visibleTabs.has("hosts") && (
          <TabsContent value="hosts" className="mt-2">
            <HostsOptimizerTab />
          </TabsContent>
        )}

        {visibleTabs.has("accel") && (
          <TabsContent value="accel" className="mt-2">
            <AccelerationTab />
          </TabsContent>
        )}

        <TabsContent value="auth" className="mt-2">
          <AuthIntegrationTab />
        </TabsContent>
      </Tabs>
    </div>
  );
}
