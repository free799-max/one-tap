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
  enabled: boolean;
  available: boolean;
  unavailable_reason: string | null;
  allowed_tools: string[];
}

export interface McpUpstreamUpdate {
  enabled: boolean;
  allowed_tools?: string[];
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

export interface McpLocalTool {
  name: string;
  title: string;
  description: string;
  read_only: boolean;
  enabled: boolean;
}

export interface McpToken {
  id: number;
  name: string;
  prefix: string;
  created_at: string;
  expires_at: string | null;
  revoked_at: string | null;
  last_used_at: string | null;
}

export interface McpTokenCreate {
  name: string;
  expires_at?: string | null;
}

export interface McpCreatedToken extends McpToken {
  token: string;
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

export function updateMcpUpstream(
  instanceId: number,
  data: McpUpstreamUpdate
): Promise<McpUpstream> {
  return api
    .put(`${MCP_BASE}/upstreams/${instanceId}`, data)
    .then((r) => r.data as McpUpstream);
}

export function testMcpUpstreamTools(
  instanceId: number
): Promise<McpToolList> {
  return api
    .post(`${MCP_BASE}/upstreams/${instanceId}/tools`, {}, { timeout: 30000 })
    .then((r) => r.data as McpToolList);
}

export function listMcpLocalTools(): Promise<McpLocalTool[]> {
  return api
    .get(`${MCP_BASE}/local-tools`)
    .then((r) => r.data as McpLocalTool[]);
}

export function updateMcpLocalTools(data: {
  enabled_tools: string[];
}): Promise<McpLocalTool[]> {
  return api
    .put(`${MCP_BASE}/local-tools`, data)
    .then((r) => r.data as McpLocalTool[]);
}

export interface McpCatalogSource {
  id: string;
  kind: string;
  name: string;
  available: boolean;
  enabled: boolean;
  error: string;
  count: number;
}

export interface McpCatalogEntry {
  kind: "onetap_api" | "local_tool" | "remote_tool" | string;
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
  risk:
    | "read"
    | "write"
    | "destructive"
    | "secret"
    | "control_plane"
    | "unsupported"
    | "unknown"
    | string;
  transport: "http" | "mcp" | "sse" | "file" | "websocket" | string;
  required_role: string;
  invocable: boolean;
  blocked_reason: string;
  enabled: boolean;
  effective: boolean;
  status_reason: string;
  upstream_instance_id: number | null;
}

export interface McpCatalog {
  entries: McpCatalogEntry[];
  sources: McpCatalogSource[];
  generated_at: string;
}

export interface McpCatalogDetail extends McpCatalogEntry {
  parameters: Array<Record<string, unknown>>;
  request_body: Record<string, unknown> | null;
  responses: Record<string, unknown>;
  components: Record<string, unknown>;
  security: Array<Record<string, string[]>> | null;
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

export function updateMcpApiOperations(data: {
  operation_ids: string[];
}): Promise<string[]> {
  return api
    .put(`${MCP_BASE}/api-operations`, data)
    .then((r) => r.data as string[]);
}

export function listMcpTokens(): Promise<McpToken[]> {
  return api.get(`${MCP_BASE}/tokens`).then((r) => r.data as McpToken[]);
}

export function createMcpToken(
  data: McpTokenCreate
): Promise<McpCreatedToken> {
  return api
    .post(`${MCP_BASE}/tokens`, data)
    .then((r) => r.data as McpCreatedToken);
}

export function revokeMcpToken(id: number): Promise<McpToken> {
  return api.delete(`${MCP_BASE}/tokens/${id}`).then((r) => r.data as McpToken);
}

export function listMcpAudit(limit = 100): Promise<McpAuditRecord[]> {
  return api
    .get(`${MCP_BASE}/audit`, { params: { limit } })
    .then((r) => r.data as McpAuditRecord[]);
}
