import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";
import { useToast } from "@/components/ui/toast";
import type { ApiError } from "@/api/types";
import {
  getMcpCatalogDetail,
  getMcpConfig,
  getMcpConnect,
  getMcpKey,
  listMcpAudit,
  listMcpCatalog,
  resetMcpKey,
  testMcpUpstreamTools,
  updateMcpConfig,
} from "@/api/mcp";

export type {
  McpAuditRecord,
  McpCatalog,
  McpCatalogDetail,
  McpCatalogEntry,
  McpCatalogSource,
  McpConfig,
  McpConnectInfo,
  McpKey,
  McpTool,
  McpToolList,
  McpUpstream,
} from "@/api/mcp";

const MCP_KEY = ["settings", "mcp"] as const;

async function invalidateMcpConfiguration(queryClient: QueryClient) {
  await Promise.all([
    queryClient.invalidateQueries({ queryKey: [...MCP_KEY, "catalog"] }),
    queryClient.invalidateQueries({ queryKey: [...MCP_KEY, "config"] }),
    queryClient.invalidateQueries({ queryKey: [...MCP_KEY, "upstreams"] }),
  ]);
}

export function useMcpCatalog() {
  return useQuery({
    queryKey: [...MCP_KEY, "catalog"],
    queryFn: listMcpCatalog,
    retry: false,
    staleTime: 15_000,
    refetchOnWindowFocus: false,
  });
}

export function useMcpCatalogDetail(
  entryId: string | null,
  enabled = true
) {
  return useQuery({
    queryKey: [...MCP_KEY, "catalog", "detail", entryId],
    queryFn: () => {
      if (!entryId) throw new Error("未选择目录条目");
      return getMcpCatalogDetail(entryId);
    },
    enabled: enabled && entryId !== null,
    retry: false,
  });
}

export function useMcpConfig() {
  return useQuery({
    queryKey: [...MCP_KEY, "config"],
    queryFn: getMcpConfig,
  });
}

export function useUpdateMcpConfig() {
  const queryClient = useQueryClient();
  const toast = useToast();

  return useMutation({
    mutationFn: (data: { enabled: boolean }) => updateMcpConfig(data),
    onSuccess: async () => {
      await invalidateMcpConfiguration(queryClient);
      toast.success("MCP 聚合设置已保存");
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "保存 MCP 聚合设置失败");
    },
  });
}

export function useMcpUpstreamTools(
  instanceId: number | null,
  enabled: boolean
) {
  return useQuery({
    queryKey: [...MCP_KEY, "upstreams", instanceId, "tools"],
    queryFn: () => {
      if (instanceId === null) throw new Error("未选择上游实例");
      return testMcpUpstreamTools(instanceId);
    },
    enabled: enabled && instanceId !== null,
    retry: false,
    refetchOnWindowFocus: false,
  });
}

export function useMcpKey() {
  return useQuery({
    queryKey: [...MCP_KEY, "key"],
    queryFn: getMcpKey,
    retry: false,
    refetchOnWindowFocus: false,
  });
}

export function useMcpConnect() {
  return useQuery({
    queryKey: [...MCP_KEY, "connect"],
    queryFn: getMcpConnect,
    retry: false,
    staleTime: 60_000,
    refetchOnWindowFocus: false,
  });
}

export function useResetMcpKey() {
  const queryClient = useQueryClient();
  const toast = useToast();

  return useMutation({
    mutationFn: resetMcpKey,
    onSuccess: (data) => {
      queryClient.setQueryData([...MCP_KEY, "key"], data);
      queryClient.invalidateQueries({ queryKey: [...MCP_KEY, "connect"] });
      toast.success("密钥已重置");
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "重置密钥失败");
    },
  });
}

export function useMcpAudit(limit = 100) {
  return useQuery({
    queryKey: [...MCP_KEY, "audit", limit],
    queryFn: () => listMcpAudit(limit),
  });
}
