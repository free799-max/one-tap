/**
 * 应用集成页面
 *
 * 网络集成（Lucky）：自动配置反向代理、域名转发与 DDNS。
 * 认证集成（Vaultwarden）：同步已部署应用的账号密码。
 *
 * 网络集成在前，因为 Vaultwarden 需要先有 HTTPS 访问入口。
 */

import { useSearchParams } from "react-router-dom";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { AuthIntegrationTab } from "./AuthIntegrationTab";
import { NetworkIntegrationTab } from "./NetworkIntegrationTab";
import { KeyRound, Route } from "lucide-react";

const integrationTabs = new Set(["auth", "network"]);

const TAB_CLASS =
  "h-12 flex-none gap-2 rounded-none px-0 text-sm text-muted-foreground transition-colors after:-bottom-px after:h-0.5 after:bg-primary hover:text-foreground data-active:bg-transparent data-active:font-semibold data-active:text-primary";

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
      <Tabs value={activeTab} onValueChange={handleTabChange} className="w-full">
        <TabsList
          variant="line"
          className="scrollbar-hidden h-auto w-full justify-start gap-7 overflow-x-auto rounded-none border-b border-border bg-transparent p-0"
        >
          <TabsTrigger value="network" className={TAB_CLASS}>
            <Route className="h-4 w-4" aria-hidden="true" />
            网络集成
          </TabsTrigger>
          <TabsTrigger value="auth" className={TAB_CLASS}>
            <KeyRound className="h-4 w-4" aria-hidden="true" />
            认证集成
          </TabsTrigger>
        </TabsList>

        <TabsContent value="network" className="mt-4">
          <NetworkIntegrationTab />
        </TabsContent>

        <TabsContent value="auth" className="mt-4">
          <AuthIntegrationTab />
        </TabsContent>
      </Tabs>
    </div>
  );
}
