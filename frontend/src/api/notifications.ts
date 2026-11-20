/**
 * 通知中心 API
 */

import api from "@/lib/api";

export interface NotificationItem {
  id: number;
  event_id: string;
  type: string;
  severity: string;
  domain: string | null;
  subject_key: string | null;
  resource_subscription_id: number | null;
  title: string;
  markdown: string;
  image_url: string | null;
  image_proxy_path: string | null;
  link: string | null;
  payload: Record<string, unknown>;
  read_at: string | null;
  created_at: string;
}

export interface NotificationList {
  items: NotificationItem[];
  total: number;
  unread: number;
}

export interface NotificationChannel {
  id: number;
  name: string;
  type: "feishu" | "dingtalk_app" | "wechatclawbot" | "qqbot";
  enabled: boolean;
  config: Record<string, unknown>;
  secret_set: boolean;
  created_at: string;
  updated_at: string;
}

export interface ChannelUpsert {
  name: string;
  type: "feishu" | "dingtalk_app" | "wechatclawbot" | "qqbot";
  enabled: boolean;
  app_id?: string;
  app_key?: string;
  app_secret?: string;
  robot_code?: string;
  card_template_id?: string;
  receive_id?: string;
  receive_id_type?: string;
  base_url?: string;
  bot_token?: string;
  account_id?: string;
  to_user?: string;
  context_token?: string;
  target?: string;
}

export interface NotificationSettings {
  event_types: string[];
}

export interface NotificationEventType {
  value: string;
  label: string;
}

export interface WeChatClawbotLoginSession {
  session_id: string;
  qrcode_image: string;
  expires_in: number;
}

export type WeChatClawbotLoginState =
  | "wait"
  | "scaned"
  | "need_message"
  | "expired"
  | "done";

export interface WeChatClawbotLoginStatus {
  status: WeChatClawbotLoginState;
  message?: string | null;
  channel?: NotificationChannel | null;
}

export interface ChannelTestResult {
  ok: boolean;
  message: string;
}

export interface SubscriptionRecord {
  id: number;
  domain: "video" | "comic" | "novel" | "music" | string;
  subject_key: string;
  kind: "subscription" | "request" | string;
  title: string;
  year: string | null;
  poster: string | null;
  status: string;
  revision: number;
  notify_enabled: boolean;
  last_seen: Record<string, unknown>;
  created_at: string;
  updated_at: string;
  stage: string;
  last_event_type: string | null;
  last_event_at: string | null;
}

export function listSubscriptions(params?: {
  domain?: string;
  limit?: number;
}): Promise<SubscriptionRecord[]> {
  return api
    .get("/notifications/subscriptions", { params })
    .then((r) => r.data);
}

export function listNotifications(params?: {
  unread?: boolean;
  domain?: string;
  page?: number;
  count?: number;
}): Promise<NotificationList> {
  return api.get("/notifications", { params }).then((r) => r.data);
}

export function getUnreadCount(): Promise<{ unread: number }> {
  return api.get("/notifications/unread-count").then((r) => r.data);
}

export function markNotificationRead(id: number): Promise<NotificationItem> {
  return api.post(`/notifications/${id}/read`).then((r) => r.data);
}

export function markAllNotificationsRead(): Promise<{ unread: number }> {
  return api.post("/notifications/read-all").then((r) => r.data);
}

export function listNotificationChannels(): Promise<NotificationChannel[]> {
  return api.get("/notifications/channels").then((r) => r.data);
}

export function createNotificationChannel(
  data: ChannelUpsert
): Promise<NotificationChannel> {
  return api.post("/notifications/channels", data).then((r) => r.data);
}

export function updateNotificationChannel(
  id: number,
  data: ChannelUpsert
): Promise<NotificationChannel> {
  return api.put(`/notifications/channels/${id}`, data).then((r) => r.data);
}

export function deleteNotificationChannel(id: number): Promise<NotificationChannel> {
  return api.delete(`/notifications/channels/${id}`).then((r) => r.data);
}

export function testNotificationChannel(id: number): Promise<ChannelTestResult> {
  return api.post(`/notifications/channels/${id}/test`).then((r) => r.data);
}

export function startWeChatClawbotLogin(data: {
  name?: string;
  channel_id?: number | null;
}): Promise<WeChatClawbotLoginSession> {
  return api
    .post("/notifications/channels/wechatclawbot/login", data)
    .then((r) => r.data);
}

export function getWeChatClawbotLoginStatus(
  sessionId: string,
  skipMessage = false
): Promise<WeChatClawbotLoginStatus> {
  return api
    .get(`/notifications/channels/wechatclawbot/login/${sessionId}`, {
      params: skipMessage ? { skip_message: true } : undefined,
    })
    .then((r) => r.data);
}

export function getNotificationSettings(): Promise<NotificationSettings> {
  return api.get("/notifications/settings").then((r) => r.data);
}

export function updateNotificationSettings(
  data: Partial<NotificationSettings>
): Promise<NotificationSettings> {
  return api.put("/notifications/settings", data).then((r) => r.data);
}

export function listNotificationEventTypes(): Promise<NotificationEventType[]> {
  return api.get("/notifications/event-types").then((r) => r.data);
}
