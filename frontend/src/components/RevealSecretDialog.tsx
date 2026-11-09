import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { PasswordInput } from "@/components/ui/password-input";
import { useToast } from "@/components/ui/toast";
import { useRevealIntegrationSecret } from "@/hooks/useIntegrations";
import type {
  IntegrationSecretOut,
  IntegrationSecretProvider,
} from "@/api/integrations";
import {
  clearSecretRevealAuthorization,
  getSecretRevealAuthorization,
  saveSecretRevealAuthorization,
} from "@/lib/secret-reveal";
import { copyToClipboard } from "@/lib/utils";

interface RevealSecretDialogProps {
  open: boolean;
  provider: IntegrationSecretProvider | null;
  title: string;
  onOpenChange: (open: boolean) => void;
}

export function RevealSecretDialog({
  open,
  provider,
  title,
  onOpenChange,
}: RevealSecretDialogProps) {
  const toast = useToast();
  const { mutate: revealSecret, isPending } = useRevealIntegrationSecret();
  const [loginPassword, setLoginPassword] = useState("");
  const [secret, setSecret] = useState<string | null>(null);
  const attemptedTokenRef = useRef<string | null>(null);
  const requestVersionRef = useRef(0);

  const handleRevealSuccess = useCallback((data: IntegrationSecretOut) => {
    saveSecretRevealAuthorization(data.reveal_token, data.expires_at);
    setLoginPassword("");
    setSecret(data.password);
  }, []);

  useEffect(() => {
    if (!open) {
      attemptedTokenRef.current = null;
      requestVersionRef.current += 1;
      setLoginPassword("");
      setSecret(null);
      return;
    }
    if (!provider) return;

    const authorization = getSecretRevealAuthorization();
    if (!authorization) return;

    const attemptKey = `${provider}:${authorization.token}`;
    if (attemptedTokenRef.current === attemptKey) return;
    attemptedTokenRef.current = attemptKey;
    const requestVersion = ++requestVersionRef.current;

    revealSecret(
      { provider, reveal_token: authorization.token },
      {
        onSuccess: (data) => {
          if (requestVersion !== requestVersionRef.current) return;
          handleRevealSuccess(data);
        },
        onError: () => {
          if (requestVersion !== requestVersionRef.current) return;
          clearSecretRevealAuthorization();
          setLoginPassword("");
          setSecret(null);
        },
      }
    );
  }, [handleRevealSuccess, open, provider, revealSecret]);

  const handleOpenChange = (nextOpen: boolean) => {
    if (!nextOpen) {
      attemptedTokenRef.current = null;
      requestVersionRef.current += 1;
      setLoginPassword("");
      setSecret(null);
    }
    onOpenChange(nextOpen);
  };

  const handleVerify = () => {
    if (!provider || !loginPassword || isPending) return;
    const requestVersion = ++requestVersionRef.current;
    revealSecret(
      { provider, password: loginPassword },
      {
        onSuccess: (data) => {
          if (requestVersion !== requestVersionRef.current) return;
          handleRevealSuccess(data);
        },
      }
    );
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-sm">
        <DialogHeader>
          <DialogTitle>{title}</DialogTitle>
        </DialogHeader>

        {secret === null ? (
          <div className="space-y-2">
            <Label htmlFor="reveal_login_password" className="text-xs">
              当前账号密码
            </Label>
            <PasswordInput
              id="reveal_login_password"
              autoFocus
              autoComplete="current-password"
              value={loginPassword}
              onChange={(e) => setLoginPassword(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") handleVerify();
              }}
              placeholder="输入登录 OneTap 的密码"
              className="h-8"
            />
            <p className="text-[11px] text-muted-foreground">
              验证后 3 小时内无需重复验证
            </p>
            <div className="flex justify-end gap-2 pt-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => handleOpenChange(false)}
              >
                取消
              </Button>
              <Button
                size="sm"
                onClick={handleVerify}
                disabled={!loginPassword || isPending}
              >
                {isPending ? "验证中..." : "验证并查看"}
              </Button>
            </div>
          </div>
        ) : (
          <div className="space-y-2">
            <Input
              value={secret}
              readOnly
              className="h-8 font-mono"
              onFocus={(e) => e.currentTarget.select()}
            />
            <div className="flex justify-end gap-2 pt-2">
              <Button
                variant="outline"
                size="sm"
                onClick={async () => {
                  await copyToClipboard(secret);
                  toast.success("已复制密码");
                }}
              >
                复制
              </Button>
              <Button size="sm" onClick={() => handleOpenChange(false)}>
                关闭
              </Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
