/**
 * 系统设置页面
 *
 * 使用 Tabs 组织基础、服务端口和存储配置。
 */

import { useSearchParams } from "react-router-dom";
import { Tabs, TabsList, TabsTrigger, TabsContent } from "@/components/ui/tabs";
import { BasicSettingsTab } from "./BasicSettingsTab";
import { ServicePortsTab } from "./ServicePortsTab";
import { StorageSettingsTab } from "./StorageSettingsTab";
import { SlidersHorizontal, HardDrive, Plug } from "lucide-react";

const settingsTabs = new Set(["basic", "ports", "storage"]);

const TAB_TRIGGER_CLASS =
  "h-12 flex-none gap-2 rounded-none px-0 text-sm text-muted-foreground transition-colors after:hidden hover:text-foreground data-active:bg-transparent data-active:font-semibold data-active:text-primary";

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
      <Tabs value={activeTab} onValueChange={handleTabChange} className="-mt-4 w-full">
        <TabsList
          variant="line"
          className="h-12! w-full justify-start gap-7 rounded-none border-b border-border bg-transparent p-0"
        >
          <TabsTrigger
            value="basic"
            className={TAB_TRIGGER_CLASS}
          >
            <SlidersHorizontal className="h-4 w-4" aria-hidden="true" />
            基础配置
          </TabsTrigger>
          <TabsTrigger
            value="ports"
            className={TAB_TRIGGER_CLASS}
          >
            <Plug className="h-4 w-4" aria-hidden="true" />
            服务端口
          </TabsTrigger>
          <TabsTrigger
            value="storage"
            className={TAB_TRIGGER_CLASS}
          >
            <HardDrive className="h-4 w-4" aria-hidden="true" />
            存储配置
          </TabsTrigger>
        </TabsList>

        <TabsContent value="basic" className="mt-2">
          <BasicSettingsTab />
        </TabsContent>

        <TabsContent value="ports" className="mt-2">
          <ServicePortsTab />
        </TabsContent>

        <TabsContent value="storage" className="mt-2">
          <StorageSettingsTab />
        </TabsContent>
      </Tabs>
    </div>
  );
}
