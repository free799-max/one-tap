import { useState } from "react";
import {
  AlertCircle,
  BadgeCheck,
  Check,
  Clock3,
  Copy,
  Infinity as InfinityIcon,
  KeyRound,
  Loader2,
  RefreshCw,
  ShieldCheck,
  ShieldOff,
  Unplug,
  WifiOff,
  XCircle,
} from "lucide-react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import { Label } from "@/components/ui/label";
import { PasswordInput } from "@/components/ui/password-input";
import { useToast } from "@/components/ui/toast";
import { useAuth } from "@/hooks/useAuth";
import {
  useActivateLicense,
  useDeactivateLicense,
  useLicenseStatus,
  useRefreshLicense,
} from "@/hooks/useLicense";
import { copyToClipboard } from "@/lib/utils";
import type { LicenseStatus } from "@/api/license";

const INPUT_CLASS =
  "h-8 rounded-md border-input bg-white px-2 text-sm shadow-none";

function formatTime(value: string | null) {
  if (!value) return "-";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString("zh-CN", { hour12: false });
}

/** 半隐藏授权码：保留前 6 位与后 4 位 */
function maskLicenseKey(key: string) {
  if (key.length <= 10) return "*".repeat(key.length);
  return `${key.slice(0, 6)}****${key.slice(-4)}`;
}

function LicenseStatusBadge({ status }: { status: LicenseStatus }) {
  const normalized = status.status.toLowerCase();
  let label = "授权无效";
  let className = "bg-destructive/10 text-destructive";
  let Icon = XCircle;

  if (!status.activated) {
    label = "未激活";
    className = "bg-muted text-muted-foreground";
    Icon = ShieldOff;
  } else if (normalized.includes("expired")) {
    label = "已过期";
    Icon = Clock3;
  } else if (normalized.includes("revoked")) {
    label = "已吊销";
    Icon = ShieldOff;
  } else if (normalized.includes("error")) {
    label = "授权错误";
    Icon = AlertCircle;
  } else if (status.valid && normalized.includes("offline")) {
    label = "离线有效";
    className = "bg-amber-500/10 text-amber-700";
    Icon = WifiOff;
  } else if (status.valid) {
    label = "授权有效";
    className = "bg-emerald-500/10 text-emerald-700";
    Icon = BadgeCheck;
  }

  return (
    <Badge className={className}>
      <Icon aria-hidden="true" />
      {label}
    </Badge>
  );
}

function StatusItem({
  label,
  value,
}: {
  label: string;
  value: string | null;
}) {
  return (
    <div className="flex items-baseline gap-2">
      <span className="shrink-0 text-sm text-muted-foreground">{label}</span>
      <span className="text-base text-foreground">{value || "-"}</span>
    </div>
  );
}

export function LicenseTab() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const toast = useToast();
  const {
    data: license,
    isLoading,
    error,
    refetch,
  } = useLicenseStatus();
  const activate = useActivateLicense();
  const refresh = useRefreshLicense();
  const deactivate = useDeactivateLicense();
  const [licenseKey, setLicenseKey] = useState("");
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [copied, setCopied] = useState(false);

  const isMutating =
    activate.isPending || refresh.isPending || deactivate.isPending;

  const handleCopyLicenseKey = async () => {
    if (!license?.license_key) return;
    const copiedOk = await copyToClipboard(license.license_key);
    if (copiedOk) {
      toast.success("授权码已复制");
      setCopied(true);
      setTimeout(() => setCopied(false), 1500);
    } else {
      toast.error("复制失败，请手动选择授权码复制");
    }
  };

  const handleActivate = (event: React.FormEvent) => {
    event.preventDefault();
    const key = licenseKey.trim();
    if (!key) return;

    activate.mutate(
      { license_key: key },
      {
        onSuccess: () => setLicenseKey(""),
      }
    );
  };

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
        加载授权状态中...
      </div>
    );
  }

  if (error || !license) {
    return (
      <Card className="rounded-xl">
        <CardContent className="flex flex-col items-start gap-3 py-6">
          <p className="flex items-center gap-2 text-sm text-destructive">
            <AlertCircle className="h-4 w-4" aria-hidden="true" />
            授权状态加载失败，请稍后重试。
          </p>
          <Button variant="outline" size="sm" onClick={() => refetch()}>
            <RefreshCw aria-hidden="true" />
            重新加载
          </Button>
        </CardContent>
      </Card>
    );
  }

  return (
    <div className="grid grid-cols-1 items-start gap-5 xl:grid-cols-2">
      <Card className="rounded-xl xl:col-span-2">
        <CardHeader className="pb-3">
          <div className="flex flex-wrap items-center justify-between gap-3">
            <CardTitle className="flex items-center gap-2 text-base">
              <ShieldCheck className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
              授权状态
            </CardTitle>
            <LicenseStatusBadge status={license} />
          </div>
          <CardDescription>
            自动化功能需要有效授权；离线有效期间可继续使用已授权功能。
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-5">
          {license.last_error && (
            <div
              role="alert"
              className="rounded-lg bg-destructive/10 px-3 py-2 text-sm text-destructive"
            >
              {license.last_error}
            </div>
          )}

          <div className="flex flex-wrap items-center gap-4">
            <div className="grid min-w-0 flex-1 grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-3 xl:grid-cols-4">
              <StatusItem
                label="最近刷新"
                value={formatTime(license.last_refreshed_at)}
              />
              <StatusItem
                label="离线有效期至"
                value={formatTime(license.expires_at)}
              />
              <div className="flex items-center gap-2">
                <span className="shrink-0 text-sm text-muted-foreground">
                  授权时长
                </span>
                {license.perpetual ? (
                  <span className="inline-flex items-center gap-1 rounded-full border border-emerald-500/30 bg-emerald-500/10 px-2 py-0.5 text-sm font-semibold text-emerald-700 dark:text-emerald-400">
                    <InfinityIcon className="h-3.5 w-3.5" aria-hidden="true" />
                    永久
                  </span>
                ) : (
                  <span className="text-base text-foreground">-</span>
                )}
              </div>
              {isAdmin && (
                <div className="flex items-center gap-2">
                  <span className="shrink-0 text-sm text-muted-foreground">
                    授权码
                  </span>
                  <span
                    className="font-mono text-base text-foreground"
                    title={
                      license.license_key
                        ? maskLicenseKey(license.license_key)
                        : "重新激活后即可显示"
                    }
                  >
                    {license.license_key
                      ? maskLicenseKey(license.license_key)
                      : "未记录"}
                  </span>
                  {license.license_key && (
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-sm"
                      className="h-6 w-6"
                      onClick={handleCopyLicenseKey}
                      title={copied ? "已复制" : "复制授权码"}
                      aria-label="复制授权码"
                    >
                      {copied ? (
                        <Check className="h-4 w-4" aria-hidden="true" />
                      ) : (
                        <Copy className="h-4 w-4" aria-hidden="true" />
                      )}
                    </Button>
                  )}
                </div>
              )}
            </div>

            {isAdmin && license.activated && (
              <div className="flex shrink-0 flex-wrap gap-2">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  disabled={isMutating}
                  onClick={() => refresh.mutate()}
                >
                  {refresh.isPending ? (
                    <Loader2 className="animate-spin" aria-hidden="true" />
                  ) : (
                    <RefreshCw aria-hidden="true" />
                  )}
                  刷新授权
                </Button>
                <Button
                  type="button"
                  variant="destructive"
                  size="sm"
                  disabled={isMutating}
                  onClick={() => setConfirmOpen(true)}
                >
                  <Unplug aria-hidden="true" />
                  解绑授权
                </Button>
              </div>
            )}
          </div>
        </CardContent>
      </Card>

      {isAdmin && !license.valid && (
        <Card className="rounded-xl xl:col-span-2">
          <CardHeader className="pb-3">
            <CardTitle className="flex items-center gap-2 text-base">
              <KeyRound className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
              激活授权
            </CardTitle>
            <CardDescription>
              输入订阅授权码，将当前设备与授权绑定。
            </CardDescription>
          </CardHeader>
          <CardContent>
            <form
              onSubmit={handleActivate}
              className="flex flex-wrap items-center gap-2"
            >
              <Label htmlFor="license_key" className="shrink-0 text-sm font-medium">
                授权码
              </Label>
              <PasswordInput
                id="license_key"
                value={licenseKey}
                onChange={(event) => setLicenseKey(event.target.value)}
                placeholder="请输入授权码"
                autoComplete="off"
                required
                containerClassName="min-w-0 max-w-md flex-1"
                className={INPUT_CLASS}
              />
              <Button
                type="submit"
                size="sm"
                className="shrink-0"
                disabled={!licenseKey.trim() || isMutating}
              >
                {activate.isPending && (
                  <Loader2 className="animate-spin" aria-hidden="true" />
                )}
                {activate.isPending ? "激活中..." : "激活授权"}
              </Button>
            </form>
          </CardContent>
        </Card>
      )}

      {!isAdmin && (
        <p className="text-xs text-muted-foreground xl:col-span-2">
          当前为普通用户，仅可查看授权状态。如需激活、刷新或解绑，请联系管理员。
        </p>
      )}

      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title="确认解绑授权？"
        description="解绑后当前设备将无法使用自动化功能，需要重新激活授权才能恢复。"
        confirmText="确认解绑"
        destructive
        isPending={deactivate.isPending}
        onConfirm={() =>
          deactivate.mutate(undefined, {
            onSuccess: () => setConfirmOpen(false),
          })
        }
      />
    </div>
  );
}
