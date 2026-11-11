/**
 * MoviePilot 集成面板
 *
 * 展示下载器/媒体服务器自动装配结果与重新装配入口。
 */

import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  useWireOrchestrationInstance,
  type AppIntegrations,
  type IntegrationRecord,
  type OrchestrationInstanceDetail,
} from "@/hooks/useOrchestrations";
import { Loader2, RefreshCw, Unplug } from "lucide-react";

function formatWiredAt(value?: string) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return date.toLocaleString("zh-CN");
}

function IntegrationRecordRow({ record }: { record: IntegrationRecord }) {
  const ok = record.status === "ok";
  return (
    <div className="rounded-lg border border-border px-3 py-2.5">
      <div className="flex items-center gap-2">
        <span className="text-sm font-medium">{record.name}</span>
        {record.type && (
          <span className="text-xs text-muted-foreground">{record.type}</span>
        )}
        <Badge
          variant={ok ? "secondary" : "destructive"}
          className={
            ok
              ? "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
              : undefined
          }
        >
          {ok ? "成功" : "失败"}
        </Badge>
      </div>
      {record.url && (
        <p className="mt-0.5 font-mono text-xs text-muted-foreground break-all">
          {record.url}
        </p>
      )}
      {record.message && (
        <p className="mt-0.5 text-xs text-muted-foreground break-words">
          {record.message}
        </p>
      )}
    </div>
  );
}

export function MoviePilotIntegrationPanel({
  instanceId,
  detail,
}: {
  instanceId: number;
  detail: OrchestrationInstanceDetail;
}) {
  const wireMutation = useWireOrchestrationInstance();

  const rawEntry = (detail.app_configs?.["moviepilot"] ?? {}) as Record<
    string,
    unknown
  >;
  const integrations = rawEntry.integrations as AppIntegrations | undefined;
  const downloaders = integrations?.downloaders ?? [];
  const mediaservers = integrations?.mediaservers ?? [];
  const hasIntegrations =
    !!integrations && (downloaders.length > 0 || mediaservers.length > 0);

  return (
    <div className="space-y-5">
      <div className="space-y-3">
        <div className="flex items-center justify-between gap-2">
          <div className="flex items-center gap-2 text-sm font-medium">
            自动装配
            {integrations?.wired_at && (
              <span className="text-xs font-normal text-muted-foreground">
                装配于 {formatWiredAt(integrations.wired_at)}
              </span>
            )}
          </div>
          <Button
            size="sm"
            variant="outline"
            className="h-8 shrink-0 gap-1.5 rounded-lg"
            onClick={() => wireMutation.mutate(instanceId)}
            disabled={wireMutation.isPending}
          >
            {wireMutation.isPending ? (
              <Loader2 className="h-3.5 w-3.5 animate-spin" />
            ) : (
              <RefreshCw className="h-3.5 w-3.5" />
            )}
            重新装配
          </Button>
        </div>

        {!hasIntegrations ? (
          <div className="flex flex-col items-center gap-2 rounded-lg border border-dashed border-border py-8 text-muted-foreground">
            <Unplug className="h-6 w-6" />
            <span className="text-xs">尚未自动装配</span>
          </div>
        ) : (
          <div className="space-y-3">
            {downloaders.length > 0 && (
              <div className="space-y-2">
                <div className="text-xs font-medium text-muted-foreground">
                  下载器
                </div>
                {downloaders.map((record) => (
                  <IntegrationRecordRow
                    key={`dl-${record.name}`}
                    record={record}
                  />
                ))}
              </div>
            )}
            {mediaservers.length > 0 && (
              <div className="space-y-2">
                <div className="text-xs font-medium text-muted-foreground">
                  媒体服务器
                </div>
                {mediaservers.map((record) => (
                  <IntegrationRecordRow
                    key={`ms-${record.name}`}
                    record={record}
                  />
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
