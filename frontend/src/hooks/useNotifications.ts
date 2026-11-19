import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";
import { useToast } from "@/components/ui/toast";
import type { ApiError } from "@/api/types";
import {
  createNotificationChannel,
  deleteNotificationChannel,
  getNotificationSettings,
  listNotificationChannels,
  listNotificationEventTypes,
  listNotifications,
  listSubscriptions,
  markAllNotificationsRead,
  markNotificationRead,
  testNotificationChannel,
  updateNotificationChannel,
  updateNotificationSettings,
  type ChannelUpsert,
  type NotificationChannel,
  type NotificationSettings,
} from "@/api/notifications";

export type {
  ChannelUpsert,
  ChannelTestResult,
  NotificationChannel,
  NotificationEventType,
  NotificationItem,
  NotificationList,
  NotificationSettings,
  SubscriptionRecord,
  WeChatClawbotLoginSession,
  WeChatClawbotLoginState,
  WeChatClawbotLoginStatus,
} from "@/api/notifications";

const NOTIFICATION_KEY = ["notifications"] as const;
const SETTINGS_KEY = ["settings", "notifications"] as const;

export function refreshNotificationChannels(queryClient: QueryClient) {
  return queryClient.invalidateQueries({
    queryKey: [...SETTINGS_KEY, "channels"],
  });
}

async function invalidateNotifications(queryClient: QueryClient) {
  await queryClient.invalidateQueries({ queryKey: [...NOTIFICATION_KEY] });
}

export function useNotificationList(params?: {
  unread?: boolean;
  domain?: string;
  page?: number;
  count?: number;
}) {
  return useQuery({
    queryKey: [...NOTIFICATION_KEY, "list", params],
    queryFn: () => listNotifications(params),
    refetchInterval: 30_000,
    refetchOnWindowFocus: false,
  });
}

export function useSubscriptions(limit = 500) {
  return useQuery({
    queryKey: [...NOTIFICATION_KEY, "subscriptions", limit],
    queryFn: () => listSubscriptions({ limit }),
    refetchInterval: 30_000,
    refetchOnWindowFocus: false,
  });
}

export function useUnreadNotifications() {
  return useQuery({
    queryKey: [...NOTIFICATION_KEY, "recent"],
    queryFn: () => listNotifications({ count: 20 }),
    refetchInterval: 30_000,
    refetchOnWindowFocus: false,
  });
}

export function useMarkNotificationRead() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: markNotificationRead,
    onSuccess: () => invalidateNotifications(queryClient),
  });
}

export function useMarkAllNotificationsRead() {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: markAllNotificationsRead,
    onSuccess: () => invalidateNotifications(queryClient),
  });
}

export function useNotificationChannels() {
  return useQuery({
    queryKey: [...SETTINGS_KEY, "channels"],
    queryFn: listNotificationChannels,
  });
}

export function useNotificationEventTypes() {
  return useQuery({
    queryKey: [...SETTINGS_KEY, "event-types"],
    queryFn: listNotificationEventTypes,
  });
}

export function useCreateNotificationChannel() {
  const queryClient = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: (data: ChannelUpsert) => createNotificationChannel(data),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: [...SETTINGS_KEY, "channels"] });
      toast.success("通知渠道已添加");
    },
    onError: (error: ApiError) => toast.error(error.displayMessage || "添加通知渠道失败"),
  });
}

export function useUpdateNotificationChannel() {
  const queryClient = useQueryClient();
  const toast = useToast();
  const queryKey = [...SETTINGS_KEY, "channels"];
  return useMutation({
    mutationFn: ({ id, data }: { id: number; data: ChannelUpsert }) =>
      updateNotificationChannel(id, data),
    onMutate: async ({ id, data }) => {
      await queryClient.cancelQueries({ queryKey });
      const previous = queryClient.getQueryData<NotificationChannel[]>(queryKey);
      if (previous) {
        queryClient.setQueryData<NotificationChannel[]>(
          queryKey,
          previous.map((item) =>
            item.id === id
              ? { ...item, name: data.name, enabled: data.enabled }
              : item
          )
        );
      }
      return { previous };
    },
    onError: (error: ApiError, _variables, context) => {
      if (context?.previous) queryClient.setQueryData(queryKey, context.previous);
      toast.error(error.displayMessage || "保存通知渠道失败");
    },
    onSuccess: () => toast.success("通知渠道已保存"),
    onSettled: () => queryClient.invalidateQueries({ queryKey }),
  });
}

export function useDeleteNotificationChannel() {
  const queryClient = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: deleteNotificationChannel,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: [...SETTINGS_KEY, "channels"] });
      toast.success("通知渠道已删除");
    },
    onError: (error: ApiError) => toast.error(error.displayMessage || "删除通知渠道失败"),
  });
}

export function useTestNotificationChannel() {
  const toast = useToast();
  return useMutation({
    mutationFn: testNotificationChannel,
    onSuccess: (result) => {
      if (result.ok) toast.success(result.message);
      else toast.error(result.message);
    },
    onError: (error: ApiError) => toast.error(error.displayMessage || "测试发送失败"),
  });
}

export function useNotificationSettings() {
  return useQuery({
    queryKey: [...SETTINGS_KEY, "config"],
    queryFn: getNotificationSettings,
  });
}

export function useUpdateNotificationSettings() {
  const queryClient = useQueryClient();
  const toast = useToast();
  const queryKey = [...SETTINGS_KEY, "config"];
  return useMutation({
    mutationFn: updateNotificationSettings,
    onMutate: async (input) => {
      await queryClient.cancelQueries({ queryKey });
      const previous = queryClient.getQueryData<NotificationSettings>(queryKey);
      if (previous && input.event_types) {
        queryClient.setQueryData<NotificationSettings>(queryKey, {
          ...previous,
          event_types: input.event_types,
        });
      }
      return { previous };
    },
    onError: (error: ApiError, _variables, context) => {
      if (context?.previous) queryClient.setQueryData(queryKey, context.previous);
      toast.error(error.displayMessage || "保存通知设置失败");
    },
    onSuccess: () => toast.success("通知设置已保存"),
    onSettled: () => queryClient.invalidateQueries({ queryKey }),
  });
}
