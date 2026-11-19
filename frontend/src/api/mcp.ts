import api from "@/lib/api";

const MCP_BASE = "/settings/mcp";

export interface McpConfig {
  enabled: boolean;
  endpoint: string;
}

export interface McpUpstream {
  instance_id: number;
  app_name: string;
  display_name: string;
  instance_name: string;
  status: string;
  prefix: string;
  available: boolean;
  unavailable_reason: string | null;
}

export interface McpTool {
  name: string;
  title?: string | null;
  description: string;
  input_schema: Record<string, unknown>;
  output_schema?: Record<string, unknown> | null;
  annotations?: Record<string, unknown> | null;
}

export interface McpToolList {
  tools: McpTool[];
}

export interface McpKey {
  key: string;
  masked_key: string;
  created_at: string;
  last_used_at: string | null;
}

export interface McpConnectInfo {
  endpoint: string;
  masked_key: string;
  config_json: string;
  prompt: string;
}

export interface McpAuditRecord {
  id: number;
  tool_name: string;
  source: string;
  upstream_instance_id: number | null;
  success: boolean;
  error_message: string | null;
  duration_ms: number;
  created_at: string;
}

export function getMcpConfig(): Promise<McpConfig> {
  return api.get(MCP_BASE).then((r) => r.data as McpConfig);
}

export function updateMcpConfig(data: {
  enabled: boolean;
}): Promise<McpConfig> {
  return api.put(MCP_BASE, data).then((r) => r.data as McpConfig);
}

export function listMcpUpstreams(): Promise<McpUpstream[]> {
  return api.get(`${MCP_BASE}/upstreams`).then((r) => r.data as McpUpstream[]);
}

export function testMcpUpstreamTools(
  instanceId: number
): Promise<McpToolList> {
  return api
    .post(`${MCP_BASE}/upstreams/${instanceId}/tools`, {}, { timeout: 30000 })
    .then((r) => r.data as McpToolList);
}

export interface McpCatalogSource {
  id: string;
  instance_id: number;
  kind: string;
  name: string;
  available: boolean;
  error: string;
  count: number;
}

export interface McpCatalogEntry {
  kind: "remote_tool";
  entry_id: string;
  source: string;
  source_name: string;
  domain: string;
  name: string;
  title: string;
  description: string;
  method: string;
  path: string;
  public_name: string;
  tags: string[];
  risk: "read" | "write" | "destructive" | "unknown";
  transport: "mcp";
  required_role: string;
  invocable: boolean;
  blocked_reason: string;
  upstream_instance_id: number | null;
}

export interface McpCatalog {
  entries: McpCatalogEntry[];
  sources: McpCatalogSource[];
  generated_at: string;
}

export interface McpCatalogDetail extends McpCatalogEntry {
  input_schema: Record<string, unknown> | null;
  output_schema: Record<string, unknown> | null;
  annotations: Record<string, unknown> | null;
}

export function listMcpCatalog(): Promise<McpCatalog> {
  return api.get(`${MCP_BASE}/catalog`).then((r) => r.data as McpCatalog);
}

export function getMcpCatalogDetail(
  entryId: string
): Promise<McpCatalogDetail> {
  return api
    .get(`${MCP_BASE}/catalog/detail`, { params: { entry_id: entryId } })
    .then((r) => r.data as McpCatalogDetail);
}

export function getMcpKey(): Promise<McpKey> {
  return api.get(`${MCP_BASE}/key`).then((r) => r.data as McpKey);
}

export function resetMcpKey(): Promise<McpKey> {
  return api.post(`${MCP_BASE}/key/reset`).then((r) => r.data as McpKey);
}

export function getMcpConnect(): Promise<McpConnectInfo> {
  return api.get(`${MCP_BASE}/connect`).then((r) => r.data as McpConnectInfo);
}

export function listMcpAudit(limit = 100): Promise<McpAuditRecord[]> {
  return api
    .get(`${MCP_BASE}/audit`, { params: { limit } })
    .then((r) => r.data as McpAuditRecord[]);
}
