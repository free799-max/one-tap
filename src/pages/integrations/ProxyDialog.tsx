import { useEffect, useId, useRef, useState } from "react";
import {
  Check,
  ChevronDown,
  Info,
  LayoutGrid,
  Loader2,
  Route,
  Search,
  SearchX,
  ShieldCheck,
  X,
} from "lucide-react";

import { cn } from "@/lib/utils";
import { AppIcon } from "@/pages/apps/AppIcon";

import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Switch } from "@/components/ui/switch";
import {
  Tooltip,
  TooltipContent,
  TooltipTrigger,
} from "@/components/ui/tooltip";
import {
  useAutoNetworkCertificate,
  useNetworkCertificates,
  useNetworkDdns,
  useUpsertNetworkProxy,
} from "@/hooks/useIntegrations";
import type { NetworkApp, NetworkProvider } from "@/api/integrations";

type ListenNetwork = "tcp" | "tcp4" | "tcp6";

const INPUT_CLASS = "h-8 rounded-md border-input bg-white text-sm shadow-none";

const NETWORK_OPTIONS: { value: ListenNetwork; label: string }[] = [
  { value: "tcp", label: "双栈（IPv4 + IPv6）" },
  { value: "tcp4", label: "仅 IPv4" },
  { value: "tcp6", label: "仅 IPv6" },
];

const CUSTOM_APP: NetworkApp = {
  instance_id: 0,
  app_name: "custom",
  display_name: "自定义应用",
  instance_name: "自定义后端",
  status: "custom",
  url: null,
  icon: null,
  username: null,
  password_set: false,
  api_key_set: false,
  has_credentials: false,
  domain: null,
  domains: [],
  target_url: null,
  proxy_enabled: false,
  is_custom: true,
  listen_port: null,
  enable_tls: false,
  network: "tcp",
  certificate_id: 0,
  ssl_forced: true,
};

function splitLines(text: string): string[] {
  return text
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

const TRANSIENT_ACME_HINTS = [
  "nonce",
  "context deadline exceeded",
  "request cancelled",
  "request canceled",
  "timeout",
  "timed out",
  "connection reset",
  "connection refused",
  "temporary failure",
];

/** Let's Encrypt 连接抖动或 nonce 失效，属于可自动重试的临时错误。 */
function isTransientAcmeError(message?: string | null): boolean {
  if (!message) return false;
  const text = message.toLowerCase();
  return TRANSIENT_ACME_HINTS.some((hint) => text.includes(hint));
}

function certCovers(certDomains: string[], domain: string): boolean {
  const target = domain.toLowerCase();
  return certDomains.some((pattern) => {
    const value = pattern.trim().toLowerCase();
    if (!value) return false;
    if (value === target) return true;
    if (value.startsWith("*.")) {
      return (
        target.endsWith(value.slice(1)) &&
        target.split(".").length === value.split(".").length
      );
    }
    return false;
  });
}

interface AppPickerProps {
  apps: NetworkApp[];
  value: number;
  onChange: (id: number) => void;
  disabled?: boolean;
  showPort?: boolean;
  placeholder?: string;
}

function AppPicker({
  apps,
  value,
  onChange,
  disabled,
  showPort,
  placeholder = "选择应用",
}: AppPickerProps) {
  const [open, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);

  const rootRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const searchRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);
  const listboxId = useId();

  const selected = apps.find((item) => item.instance_id === value) ?? null;
  const queryText = query.trim().toLowerCase();
  const filtered = queryText
    ? apps.filter((item) =>
        [
          item.display_name,
          item.instance_name,
          item.url ?? "",
          item.target_url ?? "",
          item.listen_port ? String(item.listen_port) : "",
        ].some((text) => text.toLowerCase().includes(queryText))
      )
    : apps;

  useEffect(() => {
    if (!open) return;
    const onPointerDown = (event: MouseEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    searchRef.current?.focus();
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const node = listRef.current?.querySelector<HTMLElement>(
      '[data-active="true"]'
    );
    node?.scrollIntoView({ block: "nearest" });
  }, [open, activeIndex]);

  const openPicker = () => {
    setQuery("");
    const index = apps.findIndex((item) => item.instance_id === value);
    setActiveIndex(index < 0 ? 0 : index);
    setOpen(true);
  };

  const closePicker = () => {
    setOpen(false);
    triggerRef.current?.focus();
  };

  const pick = (id: number) => {
    onChange(id);
    closePicker();
  };

  const moveActive = (delta: number) => {
    if (!filtered.length) return;
    setActiveIndex((prev) => {
      const next = (prev + delta + filtered.length) % filtered.length;
      return next;
    });
  };

  const handleKeyDown = (event: React.KeyboardEvent) => {
    if (event.key === "Escape") {
      event.stopPropagation();
      closePicker();
      return;
    }
    if (event.key === "ArrowDown") {
      event.preventDefault();
      moveActive(1);
      return;
    }
    if (event.key === "ArrowUp") {
      event.preventDefault();
      moveActive(-1);
      return;
    }
    if (event.key === "Enter") {
      event.preventDefault();
      const item = filtered[activeIndex];
      if (item) pick(item.instance_id);
    }
  };

  return (
    <div className="relative" ref={rootRef}>
      <button
        ref={triggerRef}
        type="button"
        role="combobox"
        aria-haspopup="listbox"
        aria-expanded={open}
        aria-controls={listboxId}
        disabled={disabled}
        onClick={() => (open ? setOpen(false) : openPicker())}
        onKeyDown={(event) => {
          if (!open && (event.key === "ArrowDown" || event.key === "Enter")) {
            event.preventDefault();
            openPicker();
          } else if (open) {
            handleKeyDown(event);
          }
        }}
        className={cn(
          "flex h-10 w-full items-center gap-2.5 rounded-lg border border-input bg-white px-3 text-left text-sm shadow-xs outline-none transition-colors",
          "hover:border-foreground/25 focus-visible:border-foreground/60 focus-visible:ring-2 focus-visible:ring-ring/20",
          "disabled:cursor-not-allowed disabled:bg-muted/40 disabled:opacity-80",
          open && "border-foreground/60 ring-2 ring-ring/20"
        )}
      >
        {selected ? (
          <AppIcon
            app={{ name: selected.app_name, icon: selected.icon }}
            className="h-6 w-6 shrink-0 ring-1 ring-black/5"
          />
        ) : (
          <span className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-muted text-muted-foreground">
            <LayoutGrid className="h-3.5 w-3.5" />
          </span>
        )}
        <span className="min-w-0 flex-1">
          {selected ? (
            <span className="flex items-center gap-1.5">
              <span className="truncate font-medium text-foreground">
                {selected.display_name}
              </span>
              <span className="shrink-0 truncate rounded bg-muted px-1.5 py-px text-[11px] text-muted-foreground">
                {selected.instance_name}
              </span>
              {showPort && selected.listen_port && (
                <span
                  className="shrink-0 rounded bg-primary/10 px-1.5 py-px text-[11px] font-medium text-primary"
                  title="Lucky 中这条规则监听的端口"
                >
                  监听 {selected.listen_port}
                </span>
              )}
            </span>
          ) : (
            <span className="text-muted-foreground">{placeholder}</span>
          )}
        </span>
        <ChevronDown
          className={cn(
            "h-4 w-4 shrink-0 text-muted-foreground transition-transform duration-150",
            open && "rotate-180"
          )}
        />
      </button>

      {open && (
        <div className="absolute inset-x-0 top-full z-50 mt-1.5 origin-top overflow-hidden rounded-xl border bg-popover shadow-xl animate-in fade-in-0 zoom-in-95 duration-100">
          <div className="flex items-center gap-2 border-b bg-muted/30 px-3 py-2">
            <Search className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
            <input
              ref={searchRef}
              value={query}
              onChange={(event) => {
                setQuery(event.target.value);
                setActiveIndex(0);
              }}
              onKeyDown={handleKeyDown}
              placeholder="搜索应用、实例或地址"
              aria-label="搜索应用"
              className="h-6 min-w-0 flex-1 bg-transparent text-sm text-foreground outline-none placeholder:text-muted-foreground"
            />
            {query ? (
              <button
                type="button"
                aria-label="清空搜索"
                onClick={() => {
                  setQuery("");
                  setActiveIndex(0);
                  searchRef.current?.focus();
                }}
                className="flex h-5 w-5 shrink-0 items-center justify-center rounded text-muted-foreground transition-colors hover:bg-muted hover:text-foreground"
              >
                <X className="h-3.5 w-3.5" />
              </button>
            ) : (
              <span className="shrink-0 text-[11px] text-muted-foreground">
                {apps.length} 个
              </span>
            )}
          </div>

          <div
            ref={listRef}
            id={listboxId}
            role="listbox"
            aria-label="应用列表"
            className="max-h-[340px] overflow-y-auto p-2 [scrollbar-width:thin]"
          >
            {filtered.length === 0 ? (
              <div className="flex flex-col items-center gap-1.5 px-3 py-10 text-muted-foreground">
                <SearchX className="h-5 w-5 opacity-60" />
                <p className="text-xs">没有匹配的应用</p>
              </div>
            ) : (
              <div
                role="group"
                aria-label="可选应用"
                className="grid grid-cols-2 gap-2 sm:grid-cols-3"
              >
                {filtered.map((item, index) => {
                  const isSelected = item.instance_id === value;
                  const isActive = index === activeIndex;
                  const address = item.is_custom
                    ? "手动填写后端地址"
                    : (item.url ?? item.target_url ?? "未探测到后端地址");
                  return (
                    <button
                      key={item.instance_id}
                      type="button"
                      role="option"
                      aria-selected={isSelected}
                      data-active={isActive}
                      onMouseEnter={() => setActiveIndex(index)}
                      onClick={() => pick(item.instance_id)}
                      className={cn(
                        "group relative flex flex-col gap-2 rounded-xl border p-2.5 text-left transition-all",
                        isSelected
                          ? "border-primary/60 bg-primary/[0.06] shadow-sm"
                          : "border-border bg-white hover:border-foreground/25 hover:shadow-sm",
                        isActive && "ring-2 ring-ring/30"
                      )}
                    >
                      <span className="flex items-start gap-2">
                        <AppIcon
                          app={{ name: item.app_name, icon: item.icon }}
                          className="h-8 w-8 shrink-0 ring-1 ring-black/5"
                        />
                        <span className="min-w-0 flex-1">
                          <span
                            className={cn(
                              "block truncate text-[13px] leading-tight",
                              isSelected
                                ? "font-semibold text-primary"
                                : "font-medium text-foreground"
                            )}
                            title={item.display_name}
                          >
                            {item.display_name}
                          </span>
                          <span className="mt-0.5 block truncate text-[11px] text-muted-foreground">
                            {item.instance_name}
                          </span>
                        </span>
                        <span
                          className={cn(
                            "flex h-4 w-4 shrink-0 items-center justify-center rounded-full border transition-opacity",
                            isSelected
                              ? "border-primary bg-primary text-primary-foreground opacity-100"
                              : "border-border opacity-0 group-hover:opacity-100"
                          )}
                        >
                          {isSelected && (
                            <Check className="h-2.5 w-2.5" strokeWidth={3} />
                          )}
                        </span>
                      </span>
                      <span className="flex items-center gap-1.5">
                        {showPort && item.listen_port && (
                          <span
                            className="shrink-0 rounded bg-primary/10 px-1.5 py-px text-[11px] font-medium text-primary"
                            title="Lucky 中这条规则监听的端口"
                          >
                            监听 {item.listen_port}
                          </span>
                        )}
                        <span
                          className="min-w-0 flex-1 truncate text-[11px] text-muted-foreground"
                          title={address}
                        >
                          {address.replace(/^https?:\/\//, "")}
                        </span>
                      </span>
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

interface ProxyDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  provider: NetworkProvider;
  apps: NetworkApp[];
  editing: NetworkApp | null;
  defaultPort: number;
  defaultCertificateId: number;
}

export function ProxyDialog({
  open,
  onOpenChange,
  provider,
  apps,
  editing,
  defaultPort,
  defaultCertificateId,
}: ProxyDialogProps) {
  const isLucky = provider === "lucky";
  const upsert = useUpsertNetworkProxy();
  const certificates = useNetworkCertificates(open);
  const autoCertificate = useAutoNetworkCertificate();
  const ddns = useNetworkDdns(open && isLucky);

  const [appId, setAppId] = useState(0);
  const [customName, setCustomName] = useState("");
  const [domainsText, setDomainsText] = useState("");
  const [targetUrl, setTargetUrl] = useState("");
  const [listenPort, setListenPort] = useState(defaultPort);
  const [enableTls, setEnableTls] = useState(false);
  const [network, setNetwork] = useState<ListenNetwork>("tcp4");
  const [certificateId, setCertificateId] = useState(defaultCertificateId);
  const [sslForced, setSslForced] = useState(true);
  const [certMode, setCertMode] = useState<"auto" | "selfsigned" | "acme">(
    "auto"
  );

  const [initializedKey, setInitializedKey] = useState<string | null>(null);

  const editingId = editing?.instance_id ?? 0;

  useEffect(() => {
    if (!open) {
      setInitializedKey(null);
      return;
    }
    const key = editing ? `edit:${editing.instance_id}` : "create";
    if (initializedKey === key) return;
    setCertMode("auto");
    if (editing) {
      setAppId(editing.instance_id);
      setCustomName(editing.is_custom ? editing.display_name : "");
      setDomainsText(editing.domains.join("\n"));
      setTargetUrl(editing.target_url ?? editing.url ?? "");
      setListenPort(editing.listen_port ?? defaultPort);
      setEnableTls(editing.enable_tls);
      setNetwork(editing.network);
      setCertificateId(editing.certificate_id || defaultCertificateId);
      setSslForced(editing.ssl_forced);
    } else {
      const first = apps.find((item) => !item.proxy_enabled) ?? null;
      setAppId(first?.instance_id ?? 0);
      setCustomName("");
      setDomainsText("");
      setTargetUrl(first?.target_url ?? first?.url ?? "");
      setListenPort(defaultPort);
      setEnableTls(false);
      setNetwork("tcp4");
      setCertificateId(defaultCertificateId);
      setSslForced(true);
    }
    setInitializedKey(key);
  }, [
    open,
    editing,
    apps,
    defaultPort,
    defaultCertificateId,
    initializedKey,
  ]);

  const domains = splitLines(domainsText);
  const ipv6DdnsDomains = (ddns.data?.ipv6.domains ?? []).map((item) =>
    item.toLowerCase()
  );
  const hasIpv6Domain = domains.some((domain) =>
    ipv6DdnsDomains.includes(domain.toLowerCase())
  );
  const networkHint =
    network === "tcp4" && hasIpv6Domain
      ? "该域名在 DDNS 中是 IPv6，仅 IPv4 监听会连不上。"
      : network === "tcp6"
        ? "仅 IPv6 监听，域名只写入 IPv6 DDNS。"
        : network === "tcp4"
          ? "仅 IPv4 监听，域名只写入 IPv4 DDNS。"
          : "双栈监听，域名自动写入两套 DDNS。";
  const redirectPort = listenPort === 443 ? 80 : listenPort;
  const app = apps.find((item) => item.instance_id === appId) ?? null;
  const isCustom = appId <= 0;
  const selectableApps = apps.filter(
    (item) => !item.proxy_enabled || item.instance_id === editingId
  );
  const appChoices = editing
    ? selectableApps.filter((item) => item.instance_id === editingId)
    : [...selectableApps, CUSTOM_APP];

  const certOptions = certificates.data ?? [];
  const coveringCert =
    domains.length > 0
      ? (certOptions.find(
          (item) =>
            item.status === "valid" &&
            domains.every((domain) => certCovers(item.domains, domain))
        ) ?? null)
      : null;
  const pendingCert =
    domains.length > 0
      ? (certOptions.find(
          (item) =>
            item.status === "pending" &&
            domains.some((domain) => certCovers(item.domains, domain))
        ) ?? null)
      : null;
  const failedCert =
    domains.length > 0
      ? certOptions.find(
          (item) =>
            item.status === "failed" &&
            domains.some((domain) => certCovers(item.domains, domain))
        ) ?? null
      : null;
  const canSave =
    (!!app || isCustom) &&
    (!isCustom || customName.trim().length > 0) &&
    domains.length > 0 &&
    targetUrl.trim().length > 0 &&
    (!isLucky || (listenPort >= 1 && listenPort <= 65535));

  // ACME 网络抖动时在弹窗内自动重试，最多两次
  const autoRetryKey = useRef("");
  const [autoRetryCount, setAutoRetryCount] = useState(0);
  const autoCertificatePending = autoCertificate.isPending;
  const autoCertificateMutate = autoCertificate.mutate;

  useEffect(() => {
    if (!open) {
      autoRetryKey.current = "";
      setAutoRetryCount(0);
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;
    const key = `${certMode}:${domainsText.trim()}`;
    if (autoRetryKey.current !== key) {
      autoRetryKey.current = key;
      setAutoRetryCount(0);
      return;
    }
    if (!failedCert?.error || !isTransientAcmeError(failedCert.error)) return;
    if (autoRetryCount >= 2 || autoCertificatePending) return;
    const nextDomains = splitLines(domainsText);
    if (!nextDomains.length) return;
    const timer = window.setTimeout(() => {
      setAutoRetryCount((count) => count + 1);
      autoCertificateMutate({ domains: nextDomains, mode: certMode });
    }, 2000);
    return () => window.clearTimeout(timer);
  }, [
    open,
    certMode,
    domainsText,
    failedCert?.error,
    autoRetryCount,
    autoCertificatePending,
    autoCertificateMutate,
  ]);

  const handleAppChange = (nextId: number) => {
    setAppId(nextId);
    if (nextId === CUSTOM_APP.instance_id) {
      setCustomName("");
      setTargetUrl("");
      return;
    }
    const next = apps.find((item) => item.instance_id === nextId);
    if (next) {
      setTargetUrl(next.target_url ?? next.url ?? "");
      if (!editing && next.listen_port) {
        setListenPort(next.listen_port);
      }
    }
  };

  const handleAutoCertificate = () => {
    if (!domains.length) return;
    autoCertificate.mutate({ domains, mode: certMode });
  };

  const handleSave = async () => {
    if (!canSave || (!app && !isCustom)) return;
    // HTTPS 已开启但没有覆盖全部域名的证书时，先自动准备证书
    if (isLucky && enableTls && !coveringCert && !pendingCert) {
      const result = await autoCertificate
        .mutateAsync({ domains, mode: certMode })
        .catch(() => null);
      if (!result?.ok) return;
    }
    upsert.mutate(
      {
        app_instance_id: app?.instance_id ?? CUSTOM_APP.instance_id,
        custom_name: isCustom ? customName.trim() : null,
        domains,
        target_url: targetUrl.trim() || null,
        listen_port: isLucky ? listenPort : null,
        enable_tls: isLucky ? enableTls : certificateId > 0,
        network: isLucky ? network : "tcp",
        certificate_id: isLucky ? 0 : certificateId,
        ssl_forced: sslForced,
      },
      {
        onSuccess: () => onOpenChange(false),
      }
    );
  };

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-2xl">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Route className="h-4 w-4 text-muted-foreground" />
            {editing ? "编辑反向代理" : "新建反向代理"}
          </DialogTitle>
          <DialogDescription>
            {isLucky
              ? "在 Lucky 中按端口创建规则；HTTPS 证书可自动添加到 Lucky 的 SSL 模块，也可自行管理。"
              : "在 Nginx Proxy Manager 中创建代理主机，可绑定已有证书。"}
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3">
          <div className="space-y-1">
            <Label className="text-xs">应用</Label>
            <AppPicker
              apps={appChoices}
              value={appId}
              onChange={handleAppChange}
              disabled={!!editing}
              showPort={isLucky}
            />
            {editing && (
              <p className="text-[11px] text-muted-foreground">
                应用创建后不可更换，如需更换请删除后重新创建。
              </p>
            )}
          </div>

          {isCustom && (
            <div className="space-y-1">
              <Label htmlFor="proxy_custom_name" className="text-xs">
                应用名称
              </Label>
              <Input
                id="proxy_custom_name"
                value={customName}
                onChange={(e) => setCustomName(e.target.value)}
                maxLength={100}
                placeholder="例如：内部管理系统"
                className={INPUT_CLASS}
              />
            </div>
          )}

          <div className="space-y-1">
            <Label htmlFor="proxy_domains" className="text-xs">
              域名
            </Label>
            <textarea
              id="proxy_domains"
              value={domainsText}
              onChange={(e) => setDomainsText(e.target.value)}
              rows={3}
              placeholder={"app.example.com\napp.lan\n*.example.com"}
              className="w-full resize-y rounded-md border border-input bg-white px-2 py-1.5 text-sm shadow-none outline-none focus-visible:border-foreground/60"
            />
            <p className="text-[11px] text-muted-foreground">
              每行一个，支持内网域名和通配符；公网域名会自动写入对应协议族的
              DDNS。
            </p>
          </div>

          <div className="grid gap-3 md:grid-cols-2">
            <div className="space-y-1">
              <Label htmlFor="proxy_target" className="text-xs">
                后端地址
              </Label>
              <Input
                id="proxy_target"
                value={targetUrl}
                onChange={(e) => setTargetUrl(e.target.value)}
                placeholder="http://192.168.1.10:8096"
                className={INPUT_CLASS}
              />
              <p className="text-[11px] text-muted-foreground">
                支持 IPv4 / IPv6，IPv6 地址写成 http://[fd00::10]:8096。
              </p>
            </div>

            {isLucky && (
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1">
                  <Label htmlFor="proxy_port" className="text-xs">
                    监听端口
                  </Label>
                  <Input
                    id="proxy_port"
                    type="number"
                    min={1}
                    max={65535}
                    value={listenPort}
                    onChange={(e) => setListenPort(Number(e.target.value) || 0)}
                    className={INPUT_CLASS}
                  />
                </div>
                <div className="space-y-1">
                  <div className="flex items-center gap-1">
                    <Label htmlFor="proxy_network" className="text-xs">
                      监听协议
                    </Label>
                    <Tooltip>
                      <TooltipTrigger
                        delay={100}
                        render={
                          <button
                            type="button"
                            className="text-muted-foreground hover:text-foreground"
                            aria-label="监听协议说明"
                          >
                            <Info className="h-3 w-3" />
                          </button>
                        }
                      />
                      <TooltipContent side="right">
                        {networkHint}
                      </TooltipContent>
                    </Tooltip>
                  </div>
                  <select
                    id="proxy_network"
                    value={network}
                    onChange={(e) =>
                      setNetwork(e.target.value as ListenNetwork)
                    }
                    className="h-8 w-full rounded-md border border-input bg-white px-2 text-sm shadow-none outline-none focus-visible:border-foreground/60"
                  >
                    {NETWORK_OPTIONS.map((item) => (
                      <option key={item.value} value={item.value}>
                        {item.label}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
            )}
          </div>

          {isLucky && network === "tcp4" && hasIpv6Domain && (
            <p className="text-[11px] text-amber-600">{networkHint}</p>
          )}

          {isLucky ? (
            <div className="space-y-2">
              <div className="grid grid-cols-2 gap-3">
                <div className="space-y-1">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-medium">启用 HTTPS</span>
                    <Switch
                      checked={enableTls}
                      onCheckedChange={setEnableTls}
                    />
                  </div>
                  <p className="text-[11px] text-muted-foreground">
                    证书由 Lucky 自动匹配
                  </p>
                </div>
                <div
                  className={cn(
                    "space-y-1",
                    !enableTls && "opacity-60"
                  )}
                >
                  <div className="flex items-center gap-2">
                    <span className="text-xs">HTTP 自动跳转</span>
                    <Switch
                      checked={sslForced && enableTls}
                      disabled={!enableTls}
                      onCheckedChange={setSslForced}
                    />
                  </div>
                  <p className="text-[11px] text-muted-foreground">
                    {enableTls
                      ? `${redirectPort} 端口跳到 HTTPS`
                      : "开启 HTTPS 后生效"}
                  </p>
                </div>
              </div>
              {enableTls && (
                <div className="space-y-2">
                  {certificates.isLoading ? (
                    <p className="text-[11px] text-muted-foreground">
                      读取 Lucky 证书中…
                    </p>
                  ) : coveringCert ? (
                    <p
                      className="text-[11px] text-emerald-600"
                      title={coveringCert.name}
                    >
                      {coveringCert.expires_on
                        ? `证书有效期至 ${coveringCert.expires_on}`
                        : "证书已匹配"}
                    </p>
                  ) : pendingCert ? (
                    <div className="flex items-center gap-2 text-[11px] text-muted-foreground">
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                      <span>证书申请中…</span>
                    </div>
                  ) : (                    <>
                      {failedCert?.error && (
                        <p
                          className="rounded-md bg-destructive/5 px-2 py-1 text-[11px] break-words text-destructive"
                          title={failedCert.error}
                        >
                          上次申请失败：
                          {failedCert.error.length > 200
                            ? `${failedCert.error.slice(0, 200)}…`
                            : failedCert.error}
                        </p>
                      )}
                      {autoRetryCount > 0 && (
                        <p className="text-[11px] text-amber-600">
                          Let&apos;s Encrypt
                          连接抖动，正在自动重试（{autoRetryCount}/2）…
                        </p>
                      )}
                      <div className="flex flex-wrap items-center gap-2">
                        <select
                          value={certMode}
                          onChange={(e) =>
                            setCertMode(
                              e.target.value as "auto" | "selfsigned" | "acme"
                            )
                          }
                          className="h-8 rounded-md border border-input bg-white px-2 text-xs shadow-none outline-none focus-visible:border-foreground/60"
                        >
                          <option value="auto">自动（推荐）</option>
                          <option value="selfsigned">自签名</option>
                          <option value="acme">公网 ACME</option>
                        </select>
                        <Button
                          type="button"
                          size="sm"
                          variant="outline"
                          onClick={handleAutoCertificate}
                          disabled={!domains.length || autoCertificate.isPending}
                        >
                          {autoCertificate.isPending ? (
                            <Loader2 className="h-3.5 w-3.5 animate-spin" />
                          ) : (
                            <ShieldCheck className="h-3.5 w-3.5" />
                          )}
                          自动添加证书
                        </Button>
                      </div>
                      <p className="text-[11px] text-muted-foreground">
                        {`内网域名生成自签名证书；公网域名优先用 DNS 验证（复用 DDNS 里的 DNS 服务商凭据），没有凭据时走 HTTP 验证（需要 80 端口可达）。证书会添加到 Lucky 的 SSL 模块。`}
                      </p>
                    </>
                  )}
                </div>
              )}
            </div>
          ) : (
            <div className="space-y-2 rounded-lg border p-3">
              <div className="grid gap-3 md:grid-cols-2">
                <div className="space-y-1">
                  <Label htmlFor="proxy_certificate" className="text-xs">
                    证书
                  </Label>
                  <select
                    id="proxy_certificate"
                    value={certificateId}
                    onChange={(e) => setCertificateId(Number(e.target.value))}
                    className="h-8 w-full rounded-md border border-input bg-white px-2 text-sm shadow-none outline-none focus-visible:border-foreground/60"
                  >
                    <option value={0}>不启用 HTTPS</option>
                    {certOptions.map((item) => (
                      <option key={item.id} value={Number(item.id)}>
                        {item.name}
                        {item.domains.length
                          ? `（${item.domains.join(", ")}）`
                          : ""}
                      </option>
                    ))}
                  </select>
                  {certificates.isLoading && (
                    <p className="text-[11px] text-muted-foreground">
                      读取证书中…
                    </p>
                  )}
                </div>
                <div className="flex items-center justify-between gap-2 self-end pb-1">
                  <div>
                    <p className="text-xs font-medium">强制 HTTPS</p>
                    <p className="text-[11px] text-muted-foreground">
                      把 HTTP 请求跳转到 HTTPS
                    </p>
                  </div>
                  <Switch
                    checked={sslForced && certificateId > 0}
                    disabled={certificateId === 0}
                    onCheckedChange={setSslForced}
                  />
                </div>
              </div>
            </div>
          )}
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>
            取消
          </Button>
          <Button
            onClick={handleSave}
            disabled={!canSave || upsert.isPending || autoCertificate.isPending}
          >
            {(upsert.isPending || autoCertificate.isPending) && (
              <Loader2 className="mr-1 h-3.5 w-3.5 animate-spin" />
            )}
            {editing ? "保存并应用" : "创建并应用"}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}
