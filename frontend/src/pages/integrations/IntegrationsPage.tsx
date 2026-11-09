/**
 * 应用集成页面
 *
 * 访问入口（Lucky）：自动配置反向代理、域名转发与 DDNS。
 * 网络优化（AdGuard）：按应用注入 DNS，把域名解析到指定或优选 IP。
 * 认证集成（Vaultwarden）：同步已部署应用的账号密码。
 *
 * 网络相关能力在前，因为 Vaultwarden 需要先有 HTTPS 访问入口。
 */

import { useSearchParams } from "react-router-dom";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { AuthIntegrationTab } from "./AuthIntegrationTab";
import { NetworkIntegrationTab } from "./NetworkIntegrationTab";
import { HostsOptimizerTab } from "./HostsOptimizerTab";
import { KeyRound, Route, Zap } from "lucide-react";

const integrationTabs = new Set(["auth", "network", "hosts"]);

const TAB_CLASS =
  "h-12 flex-none gap-2 rounded-none px-0 text-sm text-muted-foreground transition-colors after:hidden hover:text-foreground data-active:bg-transparent data-active:font-semibold data-active:text-primary";

export function IntegrationsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedTab = searchParams.get("tab") ?? "network";
  const activeTab = integrationTabs.has(requestedTab) ? requestedTab : "network";

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
          <TabsTrigger value="hosts" className={TAB_CLASS}>
            <Zap className="h-4 w-4" aria-hidden="true" />
            网络优化
          </TabsTrigger>
          <TabsTrigger value="auth" className={TAB_CLASS}>
            <KeyRound className="h-4 w-4" aria-hidden="true" />
            认证集成
          </TabsTrigger>
        </TabsList>

        <TabsContent value="network" className="mt-2">
          <NetworkIntegrationTab />
        </TabsContent>

        <TabsContent value="hosts" className="mt-2">
          <HostsOptimizerTab />
        </TabsContent>

        <TabsContent value="auth" className="mt-2">
          <AuthIntegrationTab />
        </TabsContent>
      </Tabs>
    </div>
  );
}
