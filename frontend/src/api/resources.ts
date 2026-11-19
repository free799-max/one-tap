/**
 * 资源搜索与订阅 API
 */

import api from "@/lib/api";

export type ResourceDomain = "video" | "comic" | "music" | "novel";

export interface ResourceInstance {
  instance_id: number;
  name: string;
  app: string;
  url?: string;
  variant?: "online" | "local" | "cloud" | string;
  lifecycle?: string[];
  operations?: string[];
}

export interface ResourceDomainCapability {
  available: boolean;
  instances: ResourceInstance[];
  variants?: ResourceInstance[];
  requires_instance_id?: boolean;
  reason?: string;
}

export interface ResourceCapabilities {
  domains: Record<string, ResourceDomainCapability>;
}

export interface ResourceSearchItem {
  item_id?: string;
  media_id?: string | number;
  title?: string;
  name?: string;
  author?: string;
  artist?: string;
  album?: string;
  year?: string | number;
  type?: string;
  media_source?: string;
  vote?: number;
  overview?: string;
  poster?: string;
  cover?: string;
  origin_name?: string;
  total_chapters?: number;
  chapter_count?: number;
  update_info?: string;
  status?: string;
  bitrate?: string;
  size?: string;
  source?: string;
  in_library?: boolean;
  [key: string]: unknown;
}

export interface ResourceSearchResult {
  instance_id?: number;
  items: ResourceSearchItem[];
  total?: number;
  has_more?: boolean;
  [key: string]: unknown;
}

export interface ResourceSubscribePayload {
  domain: ResourceDomain;
  item_id: string;
  options: Record<string, unknown>;
  instance_id?: number;
}

export function getResourceCapabilities(): Promise<ResourceCapabilities> {
  return api.get("/resources/capabilities").then((response) => response.data);
}

export function searchResources(params: {
  domain: ResourceDomain;
  keyword: string;
  instance_id?: number;
  page?: number;
  count?: number;
}): Promise<ResourceSearchResult> {
  return api.get("/resources/search", { params }).then((response) => response.data);
}

export function addResourceSubscription(
  payload: ResourceSubscribePayload
): Promise<Record<string, unknown>> {
  return api.post("/resources/subscribe", payload).then((response) => response.data);
}
