/**
 * 系统设置页面
 *
 * 使用 Tabs 组织基础、服务端口、存储和 Hosts 优选配置。
 */

import { useSearchParams } from "react-router-dom";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { BasicSettingsTab } from "./BasicSettingsTab";
import { ServicePortsTab } from "./ServicePortsTab";
import { StorageSettingsTab } from "./StorageSettingsTab";
import { HostsOptimizerTab } from "./HostsOptimizerTab";
import { SlidersHorizontal, HardDrive, Globe2, Plug } from "lucide-react";

const settingsTabs = new Set(["basic", "ports", "storage", "hosts"]);

export function SettingsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedTab = searchParams.get("tab") ?? "basic";
  const activeTab = settingsTabs.has(requestedTab) ? requestedTab : "basic";

  const handleTabChange = (value: string) => {
    const nextParams = new URLSearchParams(searchParams);
    if (value === "basic") nextParams.delete("tab");
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
          <TabsTrigger
            value="basic"
            className="h-12 flex-none gap-2 rounded-none px-0 text-sm text-muted-foreground transition-colors after:-bottom-px after:h-0.5 after:bg-primary hover:text-foreground data-active:bg-transparent data-active:font-semibold data-active:text-primary"
          >
            <SlidersHorizontal className="h-4 w-4" aria-hidden="true" />
            基础配置
          </TabsTrigger>
          <TabsTrigger
            value="ports"
            className="h-12 flex-none gap-2 rounded-none px-0 text-sm text-muted-foreground transition-colors after:-bottom-px after:h-0.5 after:bg-primary hover:text-foreground data-active:bg-transparent data-active:font-semibold data-active:text-primary"
          >
            <Plug className="h-4 w-4" aria-hidden="true" />
            服务端口
          </TabsTrigger>
          <TabsTrigger
            value="storage"
            className="h-12 flex-none gap-2 rounded-none px-0 text-sm text-muted-foreground transition-colors after:-bottom-px after:h-0.5 after:bg-primary hover:text-foreground data-active:bg-transparent data-active:font-semibold data-active:text-primary"
          >
            <HardDrive className="h-4 w-4" aria-hidden="true" />
            存储配置
          </TabsTrigger>
          <TabsTrigger
            value="hosts"
            className="h-12 flex-none gap-2 rounded-none px-0 text-sm text-muted-foreground transition-colors after:-bottom-px after:h-0.5 after:bg-primary hover:text-foreground data-active:bg-transparent data-active:font-semibold data-active:text-primary"
          >
            <Globe2 className="h-4 w-4" aria-hidden="true" />
            Hosts 优选
          </TabsTrigger>
        </TabsList>

        <TabsContent value="basic" className="mt-4">
          <BasicSettingsTab />
        </TabsContent>

        <TabsContent value="ports" className="mt-4">
          <ServicePortsTab />
        </TabsContent>

        <TabsContent value="storage" className="mt-4">
          <StorageSettingsTab />
        </TabsContent>

        <TabsContent value="hosts" className="mt-4">
          <HostsOptimizerTab />
        </TabsContent>
      </Tabs>
    </div>
  );
}
