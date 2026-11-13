import { useState } from "react";
import {
  AlertCircle,
  BadgeCheck,
  CheckCircle2,
  Circle,
  Clock3,
  Crown,
  ExternalLink,
  Eye,
  KeyRound,
  Loader2,
  RefreshCw,
  ShieldOff,
  Sparkles,
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
import { RevealSecretDialog } from "@/components/RevealSecretDialog";
import { Label } from "@/components/ui/label";
import { PasswordInput } from "@/components/ui/password-input";
import { useAuth } from "@/hooks/useAuth";
import {
  useActivateLicense,
  useDeactivateLicense,
  useLicenseStatus,
  useRefreshLicense,
} from "@/hooks/useLicense";
import { useDockerMirrorStatus } from "@/hooks/useSettings";
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

/** 距离到期还有多少天；无到期时间返回 null */
function daysUntil(value: string | null): number | null {
  if (!value) return null;
  const target = new Date(value).getTime();
  if (Number.isNaN(target)) return null;
  return Math.ceil((target - Date.now()) / 86_400_000);
}

function AccelerationStatusBadge({ status }: { status: LicenseStatus }) {
  const normalized = status.status.toLowerCase();
  let label = "状态异常";
  let className = "bg-destructive/10 text-destructive";
  let Icon = XCircle;

  if (!status.activated) {
    label = "未开通";
    className = "bg-muted text-muted-foreground";
    Icon = ShieldOff;
  } else if (normalized.includes("expired")) {
    label = "已过期";
    Icon = Clock3;
  } else if (normalized.includes("revoked")) {
    label = "已吊销";
    Icon = ShieldOff;
  } else if (normalized.includes("error")) {
    label = "状态异常";
    Icon = AlertCircle;
  } else if (status.valid && normalized.includes("offline")) {
    label = "已开通（离线）";
    className = "bg-amber-500/10 text-amber-700";
    Icon = WifiOff;
  } else if (status.valid) {
    label = "已开通";
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

function FeatureStatusBadge({
  label,
  enabled,
  loading = false,
  title,
}: {
  label: string;
  enabled: boolean | null;
  loading?: boolean;
  title?: string;
}) {
  const Icon = loading
    ? Loader2
    : enabled === null
      ? AlertCircle
      : enabled
        ? CheckCircle2
        : Circle;
  const stateLabel = loading
    ? "核验中"
    : enabled === null
      ? "状态未知"
      : enabled
        ? "已生效"
        : "未生效";
  const className = loading
    ? "border-border bg-muted text-muted-foreground"
    : enabled === null
      ? "border-amber-500/30 bg-amber-500/10 text-amber-700 dark:text-amber-400"
      : enabled
        ? "border-emerald-500/30 bg-emerald-500/10 text-emerald-700 dark:text-emerald-400"
        : "border-border bg-muted text-muted-foreground";

  return (
    <Badge variant="outline" className={className} title={title}>
      <Icon
        className={loading ? "animate-spin" : undefined}
        aria-hidden="true"
      />
      {label}
      <span className="font-normal">{stateLabel}</span>
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
  const {
    data: license,
    isLoading,
    error,
    refetch,
  } = useLicenseStatus();
  const hasValidLicense = Boolean(license?.valid);
  const mirrorQuery = useDockerMirrorStatus(hasValidLicense);
  const activate = useActivateLicense();
  const refresh = useRefreshLicense();
  const deactivate = useDeactivateLicense();
  const [licenseKey, setLicenseKey] = useState("");
  const [confirmOpen, setConfirmOpen] = useState(false);
  const [revealDialogOpen, setRevealDialogOpen] = useState(false);

  const isMutating =
    activate.isPending || refresh.isPending || deactivate.isPending;

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

  const openPurchase = () => {
    if (license?.purchase_url) {
      window.open(license.purchase_url, "_blank", "noopener,noreferrer");
    }
  };

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 text-sm text-muted-foreground">
        <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" />
        加载专属加速状态中...
      </div>
    );
  }

  if (error || !license) {
    return (
      <Card className="rounded-xl">
        <CardContent className="flex flex-col items-start gap-3 py-6">
          <p className="flex items-center gap-2 text-sm text-destructive">
            <AlertCircle className="h-4 w-4" aria-hidden="true" />
            专属加速状态加载失败，请稍后重试。
          </p>
          <Button variant="outline" size="sm" onClick={() => refetch()}>
            <RefreshCw aria-hidden="true" />
            重新加载
          </Button>
        </CardContent>
      </Card>
    );
  }

  const remainingDays = daysUntil(license.expires_at);
  const expiringSoon =
    license.valid &&
    remainingDays !== null &&
    remainingDays <= 7;
  const purchaseUrl = license.purchase_url;
  const mirrorEnabled = mirrorQuery.data?.effective ?? null;

  return (
    <Card className="rounded-xl">
      <CardHeader className="pb-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <CardTitle className="flex items-center gap-2 text-base">
            <Crown className="h-4 w-4 text-muted-foreground" aria-hidden="true" />
            专属加速
          </CardTitle>
          <div className="flex flex-wrap items-center gap-2">
            {license.valid && (
              <FeatureStatusBadge
                label="镜像加速"
                enabled={mirrorEnabled}
                loading={mirrorQuery.isPending}
                title={
                  mirrorQuery.data?.message ||
                  "镜像加速状态获取失败，请稍后重试"
                }
              />
            )}
            <AccelerationStatusBadge status={license} />
            {!license.valid && (
              <a
                href="https://doc.one-tab.vip/pricing"
                target="_blank"
                rel="noreferrer"
                className="inline-flex items-center gap-1 text-xs text-primary hover:underline"
              >
                开通加速
                <ExternalLink className="h-3 w-3" aria-hidden="true" />
              </a>
            )}
            {!license.valid && purchaseUrl && (
              <Button
                type="button"
                size="sm"
                className="bg-violet-600 text-white hover:bg-violet-700"
                onClick={openPurchase}
              >
                <Sparkles aria-hidden="true" />
                购买专属加速
              </Button>
            )}
            {expiringSoon && purchaseUrl && (
              <Button type="button" size="sm" variant="outline" onClick={openPurchase}>
                续费
              </Button>
            )}
          </div>
        </div>
        <CardDescription>
          {license.valid
            ? "专属加速已开启，镜像下载走专属加速源，其余功能始终免费开放。"
            : "OneTap 全部功能免费开放。专属加速为可选增值服务，包含镜像下载加速。作用于 OneTap 拉取镜像，不改宿主机配置。"}
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

        {expiringSoon && (
          <div
            role="status"
            className="rounded-lg bg-amber-500/10 px-3 py-2 text-sm text-amber-700"
          >
            专属加速将在 {remainingDays} 天后到期，到期后域名解析回落到本机优选，
            其他功能不受影响。
          </div>
        )}

        {license.valid ? (
          <div className="flex flex-wrap items-center gap-4">
            <div className="grid min-w-0 flex-1 grid-cols-2 gap-x-6 gap-y-3 sm:grid-cols-3">
              <StatusItem
                label="最近刷新"
                value={formatTime(license.last_refreshed_at)}
              />
              <StatusItem
                label="剩余时间"
                value={
                  remainingDays === null
                    ? null
                    : remainingDays > 0
                      ? `${remainingDays} 天后过期`
                      : "今天过期"
                }
              />
              {isAdmin && (
                <div className="flex items-center gap-2">
                  <span className="shrink-0 text-sm text-muted-foreground">
                    授权码
                  </span>
                  <span className="font-mono text-base text-foreground">
                    {license.license_key_set ? "••••••••" : "未记录"}
                  </span>
                  {license.license_key_set && (
                    <Button
                      type="button"
                      variant="ghost"
                      size="icon-sm"
                      className="h-6 w-6"
                      onClick={() => setRevealDialogOpen(true)}
                      title="查看授权码"
                      aria-label="查看授权码"
                    >
                      <Eye className="h-4 w-4" aria-hidden="true" />
                    </Button>
                  )}
                </div>
              )}
            </div>

            {isAdmin && (
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
        ) : (
          <div className="space-y-3">
            {isAdmin ? (
              <>
                <form
                  onSubmit={handleActivate}
                  className="flex flex-wrap items-center gap-2"
                >
                  <Label
                    htmlFor="license_key"
                    className="shrink-0 text-sm font-medium"
                  >
                    <KeyRound className="mr-1 inline h-4 w-4" aria-hidden="true" />
                    授权码
                  </Label>
                  <PasswordInput
                    id="license_key"
                    value={licenseKey}
                    onChange={(event) => setLicenseKey(event.target.value)}
                    placeholder="请输入专属加速授权码"
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
                    {activate.isPending ? "激活中..." : "激活专属加速"}
                  </Button>
                </form>
              </>
            ) : (
              <p className="text-sm text-muted-foreground">
                未开通专属加速；如需开通请联系管理员。
              </p>
            )}
          </div>
        )}

        {!isAdmin && (
          <p className="text-xs text-muted-foreground">
            当前为普通用户，仅可查看专属加速状态。如需开通、刷新或解绑，请联系管理员。
          </p>
        )}
      </CardContent>

      <ConfirmDialog
        open={confirmOpen}
        onOpenChange={setConfirmOpen}
        title="确认解绑专属加速？"
        description="解绑后当前设备将失去专属加速，域名解析回落到本机优选；其他功能不受影响。"
        confirmText="确认解绑"
        destructive
        isPending={deactivate.isPending}
        onConfirm={() =>
          deactivate.mutate(undefined, {
            onSuccess: () => setConfirmOpen(false),
          })
        }
      />

      <RevealSecretDialog
        open={revealDialogOpen}
        onOpenChange={setRevealDialogOpen}
        provider="license"
        title="查看专属加速授权码"
      />
    </Card>
  );
}
