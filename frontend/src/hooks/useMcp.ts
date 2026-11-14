import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";
import { useToast } from "@/components/ui/toast";
import type { ApiError } from "@/api/types";
import {
  createMcpToken,
  getMcpCatalogDetail,
  getMcpConfig,
  listMcpAudit,
  listMcpCatalog,
  listMcpLocalTools,
  listMcpTokens,
  listMcpUpstreams,
  revokeMcpToken,
  testMcpUpstreamTools,
  updateMcpApiOperations,
  updateMcpConfig,
  updateMcpLocalTools,
  updateMcpUpstream,
  type McpTokenCreate,
  type McpUpstreamUpdate,
} from "@/api/mcp";

export type {
  McpAuditRecord,
  McpCatalog,
  McpCatalogDetail,
  McpCatalogEntry,
  McpCatalogSource,
  McpConfig,
  McpCreatedToken,
  McpLocalTool,
  McpToken,
  McpTokenCreate,
  McpTool,
  McpToolList,
  McpUpstream,
  McpUpstreamUpdate,
} from "@/api/mcp";

const MCP_KEY = ["settings", "mcp"] as const;

async function invalidateMcpConfiguration(queryClient: QueryClient) {
  await Promise.all([
    queryClient.invalidateQueries({ queryKey: [...MCP_KEY, "catalog"] }),
    queryClient.invalidateQueries({ queryKey: [...MCP_KEY, "config"] }),
    queryClient.invalidateQueries({ queryKey: [...MCP_KEY, "local-tools"] }),
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

export function useMcpUpstreams() {
  return useQuery({
    queryKey: [...MCP_KEY, "upstreams"],
    queryFn: listMcpUpstreams,
  });
}

export function useUpdateMcpUpstream() {
  const queryClient = useQueryClient();
  const toast = useToast();

  return useMutation({
    mutationFn: ({
      instanceId,
      data,
    }: {
      instanceId: number;
      data: McpUpstreamUpdate;
    }) => updateMcpUpstream(instanceId, data),
    onSuccess: async () => {
      await invalidateMcpConfiguration(queryClient);
      toast.success("上游设置已保存");
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "保存上游设置失败");
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

export function useMcpLocalTools() {
  return useQuery({
    queryKey: [...MCP_KEY, "local-tools"],
    queryFn: listMcpLocalTools,
  });
}

export function useUpdateMcpLocalTools() {
  const queryClient = useQueryClient();
  const toast = useToast();

  return useMutation({
    mutationFn: (enabledTools: string[]) =>
      updateMcpLocalTools({ enabled_tools: enabledTools }),
    onSuccess: async () => {
      await invalidateMcpConfiguration(queryClient);
      toast.success("本地工具设置已保存");
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "保存本地工具设置失败");
    },
  });
}

export function useUpdateMcpApiOperations() {
  const queryClient = useQueryClient();
  const toast = useToast();

  return useMutation({
    mutationFn: (operationIds: string[]) =>
      updateMcpApiOperations({ operation_ids: operationIds }),
    onSuccess: async () => {
      await invalidateMcpConfiguration(queryClient);
      toast.success("API 权限已保存");
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "保存 API 权限失败");
    },
  });
}

export function useMcpTokens() {
  return useQuery({
    queryKey: [...MCP_KEY, "tokens"],
    queryFn: listMcpTokens,
  });
}

export function useCreateMcpToken() {
  const queryClient = useQueryClient();
  const toast = useToast();

  return useMutation({
    mutationFn: (data: McpTokenCreate) => createMcpToken(data),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [...MCP_KEY, "tokens"] });
      toast.success("Token 已创建，请立即复制保存");
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "创建 Token 失败");
    },
  });
}

export function useRevokeMcpToken() {
  const queryClient = useQueryClient();
  const toast = useToast();

  return useMutation({
    mutationFn: (id: number) => revokeMcpToken(id),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: [...MCP_KEY, "tokens"] });
      toast.success("Token 已撤销");
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "撤销 Token 失败");
    },
  });
}

export function useMcpAudit(limit = 100) {
  return useQuery({
    queryKey: [...MCP_KEY, "audit", limit],
    queryFn: () => listMcpAudit(limit),
  });
}
