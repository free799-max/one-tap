import { Link } from "react-router-dom";
import { AlertCircle, Loader2, RefreshCw, Settings, ShieldX } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Card,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { useLicenseStatus } from "@/hooks/useLicense";
import type { LicenseStatus } from "@/api/license";

function getInvalidReason(license: LicenseStatus) {
  const status = license.status.toLowerCase();
  if (!license.activated) return "当前设备尚未激活订阅授权。";
  if (status.includes("expired")) return "订阅授权已过期，请续期或重新激活。";
  if (status.includes("revoked")) return "订阅授权已被吊销，请重新激活有效授权。";
  if (license.last_error) return license.last_error;
  if (status.includes("error")) return "授权校验发生错误，请刷新授权或重新激活。";
  return "当前订阅授权无效，请前往设置查看并处理。";
}

export function LicenseGuard({ children }: { children: React.ReactNode }) {
  const { data: license, isLoading, error, refetch, isRefetching } =
    useLicenseStatus();

  if (isLoading) {
    return (
      <div
        className="flex min-h-[50vh] items-center justify-center gap-2 text-sm text-muted-foreground"
        role="status"
      >
        <Loader2 className="h-5 w-5 animate-spin" aria-hidden="true" />
        正在校验授权...
      </div>
    );
  }

  if (license?.valid) return <>{children}</>;

  return (
    <div className="flex min-h-[50vh] items-center justify-center py-8">
      <Card className="w-full max-w-md rounded-xl">
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            {error ? (
              <AlertCircle className="h-5 w-5 text-destructive" aria-hidden="true" />
            ) : (
              <ShieldX className="h-5 w-5 text-destructive" aria-hidden="true" />
            )}
            {error ? "授权状态获取失败" : "自动化功能未授权"}
          </CardTitle>
          <CardDescription className="leading-6">
            {error
              ? "暂时无法校验订阅授权，请检查网络后重试。"
              : license
                ? getInvalidReason(license)
                : "未获取到授权信息，请重试。"}
          </CardDescription>
        </CardHeader>
        <CardContent className="flex flex-wrap gap-2">
          <Button render={<Link to="/settings" />}>
            <Settings aria-hidden="true" />
            前往授权设置
          </Button>
          {error && (
            <Button
              variant="outline"
              disabled={isRefetching}
              onClick={() => refetch()}
            >
              <RefreshCw
                className={isRefetching ? "animate-spin" : undefined}
                aria-hidden="true"
              />
              重新校验
            </Button>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
