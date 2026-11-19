/**
 * 系统设置 - 通知渠道
 */

import { useEffect, useMemo, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import {
  Bell,
  Bird,
  Bot,
  Loader2,
  MessageCircle,
  MessagesSquare,
  Pencil,
  Plus,
  QrCode,
  Send,
  Trash2,
  type LucideIcon,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import { useToast } from "@/components/ui/toast";
import {
  refreshNotificationChannels,
  useCreateNotificationChannel,
  useDeleteNotificationChannel,
  useNotificationChannels,
  useNotificationEventTypes,
  useNotificationSettings,
  useTestNotificationChannel,
  useUpdateNotificationChannel,
  useUpdateNotificationSettings,
  type ChannelUpsert,
  type NotificationChannel,
  type WeChatClawbotLoginSession,
} from "@/hooks/useNotifications";
import {
  getWeChatClawbotLoginStatus,
  startWeChatClawbotLogin,
} from "@/api/notifications";
import { cn } from "@/lib/utils";

const INPUT_CLASS =
  "h-8 rounded-md border-input bg-white px-2 text-sm shadow-none dark:bg-input/30";

const SELECT_CLASS = cn(
  INPUT_CLASS,
  "w-full rounded-lg border border-input bg-white text-foreground"
);

const RECEIVE_PLACEHOLDER: Record<string, string> = {
  chat_id: "oc_xxx（群 ID）",
  open_id: "ou_xxx（用户 open_id）",
};

const TYPE_LABELS: Record<string, string> = {
  feishu: "飞书",
  dingtalk: "钉钉",
  dingtalk_app: "钉钉应用机器人",
  wechatclawbot: "微信 Bot",
  qqbot: "QQ Bot",
};

const TYPE_META: Record<
  string,
  { icon: LucideIcon; chip: string; dot: string }
> = {
  feishu: {
    icon: MessagesSquare,
    chip: "bg-sky-50 text-sky-600 dark:bg-sky-500/10 dark:text-sky-400",
    dot: "bg-sky-500",
  },
  dingtalk: {
    icon: Bird,
    chip: "bg-blue-50 text-blue-600 dark:bg-blue-500/10 dark:text-blue-400",
    dot: "bg-blue-500",
  },
  dingtalk_app: {
    icon: Bird,
    chip: "bg-indigo-50 text-indigo-600 dark:bg-indigo-500/10 dark:text-indigo-400",
    dot: "bg-indigo-500",
  },
  wechatclawbot: {
    icon: MessageCircle,
    chip: "bg-emerald-50 text-emerald-600 dark:bg-emerald-500/10 dark:text-emerald-400",
    dot: "bg-emerald-500",
  },
  qqbot: {
    icon: Bot,
    chip: "bg-violet-50 text-violet-600 dark:bg-violet-500/10 dark:text-violet-400",
    dot: "bg-violet-500",
  },
};

const FALLBACK_META: { icon: LucideIcon; chip: string; dot: string } = {
  icon: Bell,
  chip: "bg-muted text-muted-foreground",
  dot: "bg-muted-foreground",
};

type ChannelType =
  | "feishu"
  | "dingtalk"
  | "dingtalk_app"
  | "wechatclawbot"
  | "qqbot";

interface FormState {
  name: string;
  type: ChannelType;
  enabled: boolean;
  app_id: string;
  app_key: string;
  app_secret: string;
  robot_code: string;
  card_template_id: string;
  receive_id: string;
  receive_id_type: string;
  webhook: string;
  secret: string;
  base_url: string;
  bot_token: string;
  account_id: string;
  to_user: string;
  context_token: string;
  target: string;
}

const EMPTY_FORM: FormState = {
  name: "",
  type: "feishu",
  enabled: true,
  app_id: "",
  app_key: "",
  app_secret: "",
  robot_code: "",
  card_template_id: "",
  receive_id: "",
  receive_id_type: "chat_id",
  webhook: "",
  secret: "",
  base_url: "https://ilinkai.weixin.qq.com",
  bot_token: "",
  account_id: "",
  to_user: "",
  context_token: "",
  target: "",
};

function buildPayload(form: FormState): ChannelUpsert {
  const payload: ChannelUpsert = {
    name: form.name.trim(),
    type: form.type,
    enabled: form.enabled,
  };
  if (form.type === "feishu") {
    payload.app_id = form.app_id.trim();
    payload.receive_id = form.receive_id.trim();
    payload.receive_id_type = form.receive_id_type || "chat_id";
    if (form.app_secret.trim()) payload.app_secret = form.app_secret.trim();
  } else if (form.type === "dingtalk") {
    payload.webhook = form.webhook.trim();
    if (form.secret.trim()) payload.secret = form.secret.trim();
  } else if (form.type === "dingtalk_app") {
    payload.app_key = form.app_key.trim();
    payload.robot_code = form.robot_code.trim();
    payload.card_template_id = form.card_template_id.trim();
    payload.receive_id = form.receive_id.trim();
    payload.receive_id_type = form.receive_id_type || "user_id";
    if (form.app_secret.trim()) payload.app_secret = form.app_secret.trim();
  } else if (form.type === "wechatclawbot") {
    payload.base_url = form.base_url.trim() || "https://ilinkai.weixin.qq.com";
    payload.account_id = form.account_id.trim();
    payload.to_user = form.to_user.trim();
    payload.context_token = form.context_token.trim();
    if (form.bot_token.trim()) payload.bot_token = form.bot_token.trim();
  } else {
    payload.app_id = form.app_id.trim();
    payload.target = form.target.trim();
    if (form.app_secret.trim()) payload.app_secret = form.app_secret.trim();
  }
  return payload;
}

function channelPayload(
  channel: NotificationChannel,
  overrides: Partial<ChannelUpsert> = {}
): ChannelUpsert {
  const config = channel.config as Record<string, unknown>;
  const base: ChannelUpsert = {
    name: channel.name,
    type: channel.type,
    enabled: channel.enabled,
  };
  if (channel.type === "feishu") {
    base.app_id = String(config.app_id ?? "");
    base.receive_id = String(config.receive_id ?? "");
    base.receive_id_type = String(config.receive_id_type ?? "chat_id");
  } else if (channel.type === "dingtalk") {
    base.webhook = String(config.webhook ?? "");
  } else if (channel.type === "dingtalk_app") {
    base.app_key = String(config.app_key ?? "");
    base.robot_code = String(config.robot_code ?? "");
    base.card_template_id = String(config.card_template_id ?? "");
    base.receive_id = String(config.receive_id ?? "");
    base.receive_id_type = String(config.receive_id_type ?? "user_id");
  } else if (channel.type === "wechatclawbot") {
    base.base_url = String(config.base_url ?? "https://ilinkai.weixin.qq.com");
    base.account_id = String(config.account_id ?? "");
    base.to_user = String(config.to_user ?? "");
    base.context_token = String(config.context_token ?? "");
  } else {
    base.app_id = String(config.app_id ?? "");
    base.target = String(config.target ?? "");
  }
  return { ...base, ...overrides };
}

const CLAWBOT_STATUS_HINT: Record<string, string> = {
  wait: "请使用微信扫描二维码",
  scaned: "已在手机端扫码，请在微信中确认",
  need_message: "已扫码。请在微信里打开 ClawBot 会话，随便发一句（如「你好」）完成绑定",
};

function ClawbotScanPanel({
  channel,
  name,
  onDone,
}: {
  channel: NotificationChannel | null;
  name: string;
  onDone: () => void;
}) {
  const queryClient = useQueryClient();
  const toast = useToast();
  const [session, setSession] = useState<WeChatClawbotLoginSession | null>(null);
  const [status, setStatus] = useState("");
  const [hint, setHint] = useState("");
  const [error, setError] = useState("");
  const [starting, setStarting] = useState(false);
  const [skipNonce, setSkipNonce] = useState(0);
  const onDoneRef = useRef(onDone);
  useEffect(() => {
    onDoneRef.current = onDone;
  }, [onDone]);

  const start = async () => {
    setStarting(true);
    setError("");
    setHint("");
    setStatus("");
    setSession(null);
    try {
      const result = await startWeChatClawbotLogin({
        name: name.trim() || undefined,
        channel_id: channel?.id ?? null,
      });
      setSession(result);
      setStatus("wait");
      setHint(CLAWBOT_STATUS_HINT.wait);
    } catch (err) {
      setError(
        (err as { displayMessage?: string })?.displayMessage ||
          "获取微信登录二维码失败，请稍后重试"
      );
    } finally {
      setStarting(false);
    }
  };

  useEffect(() => {
    if (!session) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let skipNext = skipNonce > 0;
    const poll = async () => {
      try {
        const result = await getWeChatClawbotLoginStatus(
          session.session_id,
          skipNext
        );
        skipNext = false;
        if (cancelled) return;
        setStatus(result.status);
        setHint(result.message || CLAWBOT_STATUS_HINT[result.status] || "");
        if (result.status === "done") {
          await refreshNotificationChannels(queryClient);
          toast.success(
            result.channel
              ? `微信 Bot 已绑定「${result.channel.name}」`
              : "微信 Bot 已绑定"
          );
          onDoneRef.current();
          return;
        }
        if (result.status === "expired") {
          setSession(null);
          setError(result.message || "二维码已过期，请重新扫码");
          return;
        }
        timer = setTimeout(poll, result.status === "scaned" ? 1200 : 900);
      } catch (err) {
        if (cancelled) return;
        setError(
          (err as { displayMessage?: string })?.displayMessage ||
            "查询扫码状态失败，正在重试"
        );
        timer = setTimeout(poll, 3000);
      }
    };
    timer = setTimeout(poll, 800);
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [session, skipNonce, queryClient, toast]);

  return (
    <div className="space-y-2 rounded-lg border border-dashed border-border px-3 py-3">
      <div className="flex items-center justify-between gap-2">
        <span className="flex items-center gap-2 text-xs font-medium">
          <QrCode className="h-3.5 w-3.5 text-muted-foreground" />
          扫码登录（自动保存）
        </span>
        <Button
          type="button"
          size="sm"
          variant="outline"
          className="h-7"
          disabled={starting}
          onClick={start}
        >
          {starting ? <Loader2 className="h-3.5 w-3.5 animate-spin" /> : null}
          {session ? "重新获取二维码" : "扫码登录"}
        </Button>
      </div>
      {session ? (
        <div className="flex flex-col items-center gap-2">
          <img
            src={session.qrcode_image}
            alt="微信登录二维码"
            className="h-40 w-40 rounded-md border border-border/60 bg-white p-1"
          />
          <p
            className={cn(
              "text-center text-[11px]",
              error ? "text-destructive" : "text-muted-foreground"
            )}
          >
            {error || hint || CLAWBOT_STATUS_HINT.wait}
          </p>
          {status === "need_message" && (
            <Button
              type="button"
              size="sm"
              variant="ghost"
              className="h-7 text-[11px]"
              onClick={() => setSkipNonce((value) => value + 1)}
            >
              跳过，仅保存登录信息
            </Button>
          )}
        </div>
      ) : error ? (
        <p className="text-[11px] text-destructive">{error}</p>
      ) : (
        <p className="text-[11px] text-muted-foreground">
          用微信扫码并确认，再给机器人发一条消息，凭证会自动保存。
        </p>
      )}
    </div>
  );
}

function ChannelDialog({
  open,
  onOpenChange,
  channel,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  channel: NotificationChannel | null;
}) {
  const [form, setForm] = useState<FormState>(() => {
    if (!channel) return { ...EMPTY_FORM };
    const config = channel.config as Record<string, unknown>;
    return {
      name: channel.name,
      type: channel.type,
      enabled: channel.enabled,
      app_id: String(config.app_id ?? ""),
      app_key: String(config.app_key ?? ""),
      app_secret: "",
      robot_code: String(config.robot_code ?? ""),
      card_template_id: String(config.card_template_id ?? ""),
      receive_id: String(config.receive_id ?? ""),
      receive_id_type: String(
        config.receive_id_type ?? (channel.type === "dingtalk_app" ? "user_id" : "chat_id")
      ),
      webhook: String(config.webhook ?? ""),
      secret: "",
      base_url: String(config.base_url ?? "https://ilinkai.weixin.qq.com"),
      bot_token: "",
      account_id: String(config.account_id ?? ""),
      to_user: String(config.to_user ?? ""),
      context_token: String(config.context_token ?? ""),
      target: String(config.target ?? ""),
    };
  });
  const create = useCreateNotificationChannel();
  const update = useUpdateNotificationChannel();
  const pending = create.isPending || update.isPending;

  const handleSave = () => {
    const payload = buildPayload(form);
    if (!payload.name) return;
    const onSuccess = () => onOpenChange(false);
    if (channel) {
      update.mutate({ id: channel.id, data: payload }, { onSuccess });
    } else {
      create.mutate(payload, { onSuccess });
    }
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="flex max-h-[90vh] flex-col overflow-hidden sm:max-w-xl">
        <DialogHeader>
          <div className="flex items-center gap-3 pr-6">
            <DialogTitle className="shrink-0">
              {channel ? "编辑通知渠道" : "添加通知渠道"}
            </DialogTitle>
            {form.type === "wechatclawbot" && (
              <div className="inline-flex items-start gap-1.5 rounded-md border border-amber-200 bg-amber-50 px-2 py-1 text-[11px] font-medium leading-relaxed text-amber-700 dark:border-amber-500/30 dark:bg-amber-500/10 dark:text-amber-400">
                <Bot className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                <span>此渠道主动推送条数限制很严，通知请慎用</span>
              </div>
            )}
          </div>
          <DialogDescription>
            支持飞书、钉钉（Webhook / 应用机器人）、微信 Bot、QQ Bot；有封面时尽量随消息发送。
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-4 overflow-y-auto pr-1">
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="channel-name">名称</Label>
              <Input
                id="channel-name"
                value={form.name}
                onChange={(e) => setForm((prev) => ({ ...prev, name: e.target.value }))}
                className={INPUT_CLASS}
                placeholder="家庭通知"
              />
            </div>
            <div className="space-y-1.5">
              <Label>渠道类型</Label>
              <select
                value={form.type}
                onChange={(e) =>
                  setForm((prev) => ({
                    ...prev,
                    type: e.target.value as ChannelType,
                  }))
                }
                className={SELECT_CLASS}
              >
                <option value="feishu">飞书</option>
                <option value="dingtalk">钉钉（Webhook 机器人）</option>
                <option value="dingtalk_app">钉钉应用机器人（互动卡片）</option>
                <option value="wechatclawbot">微信 Bot</option>
                <option value="qqbot">QQ Bot</option>
              </select>
            </div>
          </div>

          {form.type === "feishu" && (
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label htmlFor="feishu-app-id">App ID</Label>
                <Input
                  id="feishu-app-id"
                  value={form.app_id}
                  onChange={(e) =>
                    setForm((prev) => ({ ...prev, app_id: e.target.value }))
                  }
                  className={INPUT_CLASS}
                  placeholder="cli_xxx"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="feishu-secret">App Secret</Label>
                <Input
                  id="feishu-secret"
                  type="password"
                  value={form.app_secret}
                  onChange={(e) =>
                    setForm((prev) => ({ ...prev, app_secret: e.target.value }))
                  }
                  className={INPUT_CLASS}
                  placeholder={channel?.secret_set ? "留空表示不修改" : ""}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="feishu-receive">接收人 ID</Label>
                <Input
                  id="feishu-receive"
                  value={form.receive_id}
                  onChange={(e) =>
                    setForm((prev) => ({ ...prev, receive_id: e.target.value }))
                  }
                  className={INPUT_CLASS}
                  placeholder={RECEIVE_PLACEHOLDER[form.receive_id_type] ?? "oc_xxx"}
                />
              </div>
              <div className="space-y-1.5">
                <Label>接收人类型</Label>
                <select
                  value={form.receive_id_type}
                  onChange={(e) =>
                    setForm((prev) => ({
                      ...prev,
                      receive_id_type: e.target.value,
                    }))
                  }
                  className={SELECT_CLASS}
                >
                  <option value="chat_id">chat_id（群）</option>
                  <option value="open_id">open_id（用户）</option>
                  {!["chat_id", "open_id"].includes(form.receive_id_type) && (
                    <option value={form.receive_id_type}>
                      {form.receive_id_type}（旧配置）
                    </option>
                  )}
                </select>
              </div>
              <p className="col-span-2 text-[11px] text-muted-foreground">
                发到群里选 chat_id（oc_ 开头）；私聊发给个人选 open_id（ou_ 开头）。
              </p>
            </div>
          )}

          {form.type === "dingtalk" && (
            <div className="grid grid-cols-2 gap-3">
              <div className="col-span-2 space-y-1.5">
                <Label htmlFor="dingtalk-webhook">Webhook 地址</Label>
                <Input
                  id="dingtalk-webhook"
                  value={form.webhook}
                  onChange={(e) =>
                    setForm((prev) => ({ ...prev, webhook: e.target.value }))
                  }
                  className={INPUT_CLASS}
                  placeholder="https://oapi.dingtalk.com/robot/send?access_token=..."
                />
              </div>
              <div className="col-span-2 space-y-1.5">
                <Label htmlFor="dingtalk-secret">加签密钥（可选）</Label>
                <Input
                  id="dingtalk-secret"
                  type="password"
                  value={form.secret}
                  onChange={(e) =>
                    setForm((prev) => ({ ...prev, secret: e.target.value }))
                  }
                  className={INPUT_CLASS}
                  placeholder={channel?.secret_set ? "留空表示不修改" : "SEC..."}
                />
              </div>
            </div>
          )}

          {form.type === "dingtalk_app" && (
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label htmlFor="ding-app-key">AppKey</Label>
                <Input
                  id="ding-app-key"
                  value={form.app_key}
                  onChange={(e) =>
                    setForm((prev) => ({ ...prev, app_key: e.target.value }))
                  }
                  className={INPUT_CLASS}
                  placeholder="ding..."
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="ding-app-secret">AppSecret</Label>
                <Input
                  id="ding-app-secret"
                  type="password"
                  value={form.app_secret}
                  onChange={(e) =>
                    setForm((prev) => ({ ...prev, app_secret: e.target.value }))
                  }
                  className={INPUT_CLASS}
                  placeholder={channel?.secret_set ? "留空表示不修改" : ""}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="ding-robot-code">机器人编码</Label>
                <Input
                  id="ding-robot-code"
                  value={form.robot_code}
                  onChange={(e) =>
                    setForm((prev) => ({ ...prev, robot_code: e.target.value }))
                  }
                  className={INPUT_CLASS}
                  placeholder="robotCode"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="ding-card-template">卡片模板 ID（可选）</Label>
                <Input
                  id="ding-card-template"
                  value={form.card_template_id}
                  onChange={(e) =>
                    setForm((prev) => ({ ...prev, card_template_id: e.target.value }))
                  }
                  className={INPUT_CLASS}
                  placeholder="留空则发 markdown"
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="ding-receive-id">接收目标 ID</Label>
                <Input
                  id="ding-receive-id"
                  value={form.receive_id}
                  onChange={(e) =>
                    setForm((prev) => ({ ...prev, receive_id: e.target.value }))
                  }
                  className={INPUT_CLASS}
                  placeholder={
                    form.receive_id_type === "open_conversation_id"
                      ? "cid...（群会话 ID）"
                      : "manager1234（员工 userId）"
                  }
                />
              </div>
              <div className="space-y-1.5">
                <Label>接收目标类型</Label>
                <select
                  value={form.receive_id_type}
                  onChange={(e) =>
                    setForm((prev) => ({
                      ...prev,
                      receive_id_type: e.target.value,
                    }))
                  }
                  className={SELECT_CLASS}
                >
                  <option value="user_id">user_id（单聊）</option>
                  <option value="open_conversation_id">openConversationId（群）</option>
                </select>
              </div>
              <p className="col-span-2 text-[11px] text-muted-foreground">
                应用凭证在钉钉开放平台「企业内部应用 → 机器人」里获取。配了卡片模板就发互动卡片，
                模板里可用变量：title、subtitle、fields、content、image、link；不配则发 markdown。
                单聊需要用户先在钉钉里跟机器人说过话。
              </p>
            </div>
          )}

          {form.type === "wechatclawbot" && (
            <>
              <div className="grid grid-cols-2 gap-3">
                <div className="col-span-2 space-y-1.5">
                  <Label htmlFor="claw-base">iLink 服务地址</Label>
                  <Input
                    id="claw-base"
                    value={form.base_url}
                    onChange={(e) =>
                      setForm((prev) => ({ ...prev, base_url: e.target.value }))
                    }
                    className={INPUT_CLASS}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="claw-token">Bot Token</Label>
                  <Input
                    id="claw-token"
                    type="password"
                    value={form.bot_token}
                    onChange={(e) =>
                      setForm((prev) => ({ ...prev, bot_token: e.target.value }))
                    }
                    className={INPUT_CLASS}
                    placeholder={channel?.secret_set ? "留空表示不修改" : ""}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="claw-account">Account ID</Label>
                  <Input
                    id="claw-account"
                    value={form.account_id}
                    onChange={(e) =>
                      setForm((prev) => ({ ...prev, account_id: e.target.value }))
                    }
                    className={INPUT_CLASS}
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="claw-user">接收人</Label>
                  <Input
                    id="claw-user"
                    value={form.to_user}
                    onChange={(e) =>
                      setForm((prev) => ({ ...prev, to_user: e.target.value }))
                    }
                    className={INPUT_CLASS}
                    placeholder="wxid_xxx 或 xxx@im.wechat"
                  />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="claw-context">Context Token（可选）</Label>
                  <Input
                    id="claw-context"
                    value={form.context_token}
                    onChange={(e) =>
                      setForm((prev) => ({ ...prev, context_token: e.target.value }))
                    }
                    className={INPUT_CLASS}
                  />
                </div>
              </div>
              <p className="text-[11px] text-muted-foreground">
                上面的参数也可以手动填写；扫码成功会自动保存下面这些凭证。
              </p>
              <ClawbotScanPanel
                channel={channel}
                name={form.name}
                onDone={() => onOpenChange(false)}
              />
            </>
          )}

          {form.type === "qqbot" && (
            <div className="grid grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label htmlFor="qq-app-id">App ID</Label>
                <Input
                  id="qq-app-id"
                  value={form.app_id}
                  onChange={(e) =>
                    setForm((prev) => ({ ...prev, app_id: e.target.value }))
                  }
                  className={INPUT_CLASS}
                />
              </div>
              <div className="space-y-1.5">
                <Label htmlFor="qq-secret">App Secret</Label>
                <Input
                  id="qq-secret"
                  type="password"
                  value={form.app_secret}
                  onChange={(e) =>
                    setForm((prev) => ({ ...prev, app_secret: e.target.value }))
                  }
                  className={INPUT_CLASS}
                  placeholder={channel?.secret_set ? "留空表示不修改" : ""}
                />
              </div>
              <div className="col-span-2 space-y-1.5">
                <Label htmlFor="qq-target">接收目标</Label>
                <Input
                  id="qq-target"
                  value={form.target}
                  onChange={(e) =>
                    setForm((prev) => ({ ...prev, target: e.target.value }))
                  }
                  className={INPUT_CLASS}
                  placeholder="group:群 openid 或用户 openid"
                />
              </div>
            </div>
          )}
        </div>

        <DialogFooter className="flex-row items-center justify-between gap-3 px-4 sm:justify-between">
          <div className="flex items-center gap-2">
            <Label htmlFor="channel-enabled">启用该渠道</Label>
            <Switch
              id="channel-enabled"
              checked={form.enabled}
              onCheckedChange={(enabled) =>
                setForm((prev) => ({ ...prev, enabled }))
              }
            />
          </div>
          <div className="flex items-center gap-2">
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>
              取消
            </Button>
            <Button type="button" onClick={handleSave} disabled={pending || !form.name.trim()}>
              {pending ? <Loader2 className="h-4 w-4 animate-spin" /> : null}
              保存
            </Button>
          </div>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function EventSettingsCard() {
  const settings = useNotificationSettings();
  const eventTypes = useNotificationEventTypes();
  const update = useUpdateNotificationSettings();
  const allEvents = useMemo(
    () => (eventTypes.data ?? []).map((item) => item.value),
    [eventTypes.data]
  );
  const configured = settings.data?.event_types ?? [];
  const selected = configured.length ? configured : allEvents;

  const toggle = (value: string) => {
    const next = selected.includes(value)
      ? selected.filter((item) => item !== value)
      : [...selected, value];
    if (next.length === 0) return;
    update.mutate({ event_types: next.length === allEvents.length ? [] : next });
  };

  return (
    <Card className="rounded-xl">
      <CardHeader className="border-b">
        <CardTitle className="text-base">接收事件</CardTitle>
      </CardHeader>
      <CardContent className="space-y-2">
        <div className="grid grid-cols-2 gap-1.5 sm:grid-cols-4">
          {(eventTypes.data ?? []).map((item) => {
            const checked = selected.includes(item.value);
            return (
              <label
                key={item.value}
                className={cn(
                  "flex items-center gap-2 rounded-md border border-border/60 px-2 py-1.5 text-xs",
                  !checked && "text-muted-foreground"
                )}
              >
                <input
                  type="checkbox"
                  checked={checked}
                  disabled={checked && selected.length === 1}
                  onChange={() => toggle(item.value)}
                  className="h-3.5 w-3.5 accent-primary"
                />
                {item.label}
              </label>
            );
          })}
        </div>
        <p className="text-[11px] text-muted-foreground">
          对外推送只发勾选的事件，全选表示全部事件（新增事件也自动包含）；站内收件箱始终接收全部事件。
        </p>
      </CardContent>
    </Card>
  );
}

export function NotificationSettingsTab() {
  const channels = useNotificationChannels();
  const updateChannel = useUpdateNotificationChannel();
  const deleteChannel = useDeleteNotificationChannel();
  const testChannel = useTestNotificationChannel();
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<NotificationChannel | null>(null);

  const openCreate = () => {
    setEditing(null);
    setDialogOpen(true);
  };
  const openEdit = (channel: NotificationChannel) => {
    setEditing(channel);
    setDialogOpen(true);
  };

  return (
    <div className="space-y-5">
      <Card className="rounded-xl">
        <CardHeader className="border-b">
          <CardTitle className="flex items-center gap-2 text-base">
            <Bell className="h-4 w-4 text-muted-foreground" />
            通知渠道
          </CardTitle>
        </CardHeader>
        <CardContent className="space-y-3">
          {channels.isLoading ? (
            <div className="flex items-center gap-2 py-4 text-sm text-muted-foreground">
              <Loader2 className="h-4 w-4 animate-spin" />
              加载渠道…
            </div>
          ) : (
            <div className="grid grid-cols-[repeat(auto-fill,minmax(250px,270px))] items-start gap-3">
              {(channels.data ?? []).map((channel) => {
                const meta = TYPE_META[channel.type] ?? FALLBACK_META;
                const typeLabel = TYPE_LABELS[channel.type] ?? channel.type;
                const Icon = meta.icon;
                return (
                  <div
                    key={channel.id}
                    className={cn(
                      "flex flex-col overflow-hidden rounded-xl border bg-card transition-all hover:border-primary/25 hover:shadow-sm",
                      channel.enabled
                        ? "border-border/70"
                        : "border-border/40 opacity-60"
                    )}
                  >
                    <div className="flex flex-1 items-start gap-3 px-3.5 py-4">
                      <div
                        className={cn(
                          "flex h-11 w-11 shrink-0 items-center justify-center rounded-xl",
                          meta.chip
                        )}
                      >
                        <Icon className="h-5 w-5" />
                      </div>
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-semibold">
                          {channel.name}
                        </p>
                        <div className="mt-1 flex flex-wrap items-center gap-x-1.5 gap-y-0.5 text-[11px] text-muted-foreground">
                          <span className="flex items-center gap-1">
                            <span
                              className={cn(
                                "h-1.5 w-1.5 rounded-full",
                                channel.enabled
                                  ? meta.dot
                                  : "bg-muted-foreground/40"
                              )}
                            />
                            {channel.enabled ? "已启用" : "已停用"}
                          </span>
                          {typeLabel !== channel.name && (
                            <span>· {typeLabel}</span>
                          )}
                          {!channel.secret_set && (
                            <span className="text-amber-600">· 未配置密钥</span>
                          )}
                        </div>
                      </div>
                      <Switch
                        className="h-[18px] w-8 [&_[data-slot=switch-thumb]]:h-3.5 [&_[data-slot=switch-thumb]]:w-3.5 [&_[data-slot=switch-thumb]]:data-[checked]:translate-x-3.5"
                        checked={channel.enabled}
                        onCheckedChange={(enabled) =>
                          updateChannel.mutate({
                            id: channel.id,
                            data: channelPayload(channel, { enabled }),
                          })
                        }
                      />
                    </div>
                    <div className="flex items-center justify-end gap-1 border-t border-border/50 bg-muted/30 px-2 py-1">
                      <Button
                        type="button"
                        size="icon-sm"
                        variant="ghost"
                        title="发送测试消息"
                        aria-label="发送测试消息"
                        className="text-muted-foreground hover:bg-background hover:text-foreground"
                        disabled={testChannel.isPending}
                        onClick={() => testChannel.mutate(channel.id)}
                      >
                        <Send className="h-4 w-4" />
                      </Button>
                      <span className="h-3.5 w-px bg-border" />
                      <Button
                        type="button"
                        size="icon-sm"
                        variant="ghost"
                        title="编辑渠道"
                        aria-label="编辑渠道"
                        className="text-muted-foreground hover:bg-background hover:text-foreground"
                        onClick={() => openEdit(channel)}
                      >
                        <Pencil className="h-4 w-4" />
                      </Button>
                      <span className="h-3.5 w-px bg-border" />
                      <Button
                        type="button"
                        size="icon-sm"
                        variant="ghost"
                        title="删除渠道"
                        aria-label="删除渠道"
                        className="text-muted-foreground hover:bg-background hover:text-destructive"
                        onClick={() => {
                          if (window.confirm(`删除通知渠道「${channel.name}」？`)) {
                            deleteChannel.mutate(channel.id);
                          }
                        }}
                      >
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    </div>
                  </div>
                );
              })}
              <button
                type="button"
                onClick={openCreate}
                className="flex min-h-[108px] flex-col items-center justify-center gap-1.5 rounded-xl border border-dashed border-border/70 text-muted-foreground transition-colors hover:border-primary/50 hover:bg-muted/30 hover:text-foreground"
              >
                <span className="flex h-9 w-9 items-center justify-center rounded-full bg-muted">
                  <Plus className="h-4 w-4" />
                </span>
                <span className="text-xs">添加渠道</span>
              </button>
            </div>
          )}
          <p className="text-[11px] text-muted-foreground">
            如果 MoviePilot 也配置了同一渠道，请关闭它的下载/整理/订阅通知，避免重复推送。
          </p>
        </CardContent>
      </Card>

      <EventSettingsCard />

      <ChannelDialog
        key={`${editing?.id ?? "new"}-${dialogOpen ? "open" : "closed"}`}
        open={dialogOpen}
        onOpenChange={setDialogOpen}
        channel={editing}
      />
    </div>
  );
}
