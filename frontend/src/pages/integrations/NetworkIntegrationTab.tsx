/**
 * 网络集成 Tab
 *
 * 支持两套互斥的反代方案：
 * - Lucky：反向代理与 DDNS 一体
 * - Nginx Proxy Manager + DDNS-Go：反代与 DDNS 分开部署
 *
 * 连接后可为已部署应用自动写入反向代理，并创建 DDNS 任务。
 */

import { useEffect, useState } from "react";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { PasswordInput } from "@/components/ui/password-input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { Switch } from "@/components/ui/switch";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { useToast } from "@/components/ui/toast";
import { copyToClipboard } from "@/lib/utils";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import {
  useDeleteNetworkDdns,
  useDeleteNetworkProxy,
  useNetworkApps,
  useNetworkConfig,
  useNetworkDdns,
  useNetworkDdnsInterfaces,
  useNetworkHosts,
  useNpmCertificates,
  useRevealNetworkSecret,
  useSaveNetworkDdns,
  useTestNetwork,
  useUpdateNetwork,
} from "@/hooks/useIntegrations";
import type {
  NetworkApp,
  NetworkConfig,
  NetworkDdnsFamily,
  NetworkDdnsInterface,
  NetworkProvider,
  NetworkSecretProvider,
  NetworkUpdate,
} from "@/api/integrations";
import { ProxyDialog } from "./ProxyDialog";
import {
  CheckCircle2,
  Cloud,
  ExternalLink,
  Globe2,
  Loader2,
  Network,
  RefreshCw,
  Route,
  Server,
  XCircle,
} from "lucide-react";

const INPUT_CLASS =
  "h-8 rounded-md border-input bg-white px-2 text-sm shadow-none";

/** 下拉框中触发手工输入内网地址的占位值。 */
const CUSTOM_HOST = "__custom__";

interface ProviderOption {
  value: NetworkProvider;
  title: string;
  description: string;
  detail: string;
}

const PROVIDERS: ProviderOption[] = [
  {
    value: "lucky",
    title: "Lucky",
    description: "反向代理 + DDNS 一体",
    detail: "OneTap 在 Lucky 中维护「OneTap」规则和 DDNS 任务。",
  },
  {
    value: "npm",
    title: "Nginx Proxy Manager + DDNS-Go",
    description: "反代与 DDNS 分开部署",
    detail: "NPM 负责域名转发，DDNS-Go 负责动态域名解析。",
  },
];

const DNS_PROVIDERS: Record<
  NetworkProvider,
  { value: string; label: string }[]
> = {
  lucky: [
    { value: "alidns", label: "阿里云 DNS" },
    { value: "cloudflare", label: "Cloudflare" },
    { value: "dnspod", label: "DNSPod（.cn）" },
    { value: "dnspodcom", label: "DNSPod（.com）" },
    { value: "tencentcloudV3", label: "腾讯云 DNSPod" },
    { value: "huaweicloud", label: "华为云 DNS" },
    { value: "baiducloud", label: "百度云 DNS" },
    { value: "jdcloud", label: "京东云 DNS" },
    { value: "volcengine", label: "火山引擎 DNS" },
    { value: "porkbun", label: "Porkbun" },
    { value: "godaddy", label: "GoDaddy" },
    { value: "namesilo", label: "NameSilo" },
    { value: "duckdns", label: "Duck DNS" },
    { value: "dynv6", label: "Dynv6" },
    { value: "freemyip", label: "FreeMyIP" },
    { value: "spaceship", label: "Spaceship" },
    { value: "vercel", label: "Vercel" },
  ],
  npm: [
    { value: "cloudflare", label: "Cloudflare" },
    { value: "alidns", label: "阿里云 DNS" },
    { value: "dnspod", label: "DNSPod" },
    { value: "tencentcloud", label: "腾讯云" },
    { value: "huaweicloud", label: "华为云 DNS" },
    { value: "baiducloud", label: "百度云 DNS" },
    { value: "porkbun", label: "Porkbun" },
    { value: "godaddy", label: "GoDaddy" },
    { value: "dynv6", label: "Dynv6" },
  ],
};

const PROVIDER_HINTS: Record<
  string,
  { id: string; secret: string; idOptional?: boolean }
> = {
  cloudflare: { id: "账号 ID（可留空）", secret: "API Token", idOptional: true },
  alidns: { id: "AccessKey ID", secret: "AccessKey Secret" },
  aliesa: { id: "AccessKey ID", secret: "AccessKey Secret" },
  dnspod: { id: "ID", secret: "Token" },
  dnspodcom: { id: "ID", secret: "Token" },
  tencentcloud: { id: "SecretId", secret: "SecretKey" },
  tencentcloudV3: { id: "SecretId", secret: "SecretKey" },
  huaweicloud: { id: "Access Key ID", secret: "Secret Access Key" },
  baiducloud: { id: "AccessKey ID", secret: "AccessKey Secret" },
  jdcloud: { id: "Access Key ID", secret: "Access Key Secret" },
  volcengine: { id: "Access Key ID", secret: "Secret Access Key" },
  porkbun: { id: "API Key", secret: "Secret Key" },
  godaddy: { id: "Key", secret: "Secret" },
  namesilo: { id: "可留空", secret: "API Key", idOptional: true },
  duckdns: { id: "可留空", secret: "Token", idOptional: true },
  dynv6: { id: "可留空", secret: "Token", idOptional: true },
  freemyip: { id: "可留空", secret: "Token", idOptional: true },
  spaceship: { id: "API Key", secret: "API Secret" },
  vercel: { id: "Team ID", secret: "Token" },
};

const DEFAULT_DDNS_URLS = {
  ipv4: "https://api.ipify.org\nhttps://ip.3322.net\nhttps://4.ipw.cn",
  ipv6: "https://api6.ipify.org\nhttps://v6.ident.me\nhttps://6.ipw.cn",
};

interface FormState {
  enabled: boolean;
  provider: NetworkProvider;
  targetHost: string;
  luckyUrl: string;
  luckyUsername: string;
  luckyPassword: string;
  listenPort: number;
  enableTls: boolean;
  npmUrl: string;
  npmEmail: string;
  npmPassword: string;
  certificateId: string;
  sslForced: boolean;
  ddnsgoUrl: string;
  ddnsgoUsername: string;
  ddnsgoPassword: string;
}

function formFromConfig(config: NetworkConfig): FormState {
  return {
    enabled: config.enabled,
    provider: config.provider,
    targetHost: config.target_host,
    luckyUrl: config.lucky.url,
    luckyUsername: config.lucky.username,
    luckyPassword: "",
    listenPort: config.lucky.listen_port,
    enableTls: config.lucky.enable_tls,
    npmUrl: config.npm.url,
    npmEmail: config.npm.email,
    npmPassword: "",
    certificateId: String(config.npm.certificate_id),
    sslForced: config.npm.ssl_forced,
    ddnsgoUrl: config.ddnsgo.url,
    ddnsgoUsername: config.ddnsgo.username,
    ddnsgoPassword: "",
  };
}

const EMPTY_FORM: FormState = {
  enabled: false,
  provider: "lucky",
  targetHost: "",
  luckyUrl: "",
  luckyUsername: "",
  luckyPassword: "",
  listenPort: 443,
  enableTls: true,
  npmUrl: "",
  npmEmail: "",
  npmPassword: "",
  certificateId: "0",
  sslForced: true,
  ddnsgoUrl: "",
  ddnsgoUsername: "",
  ddnsgoPassword: "",
};

interface DdnsFamilyForm {
  enabled: boolean;
  domainsText: string;
  interval: number;
  getType: "url" | "netInterface";
  url: string;
  netInterface: string;
  ipReg: string;
}

interface DdnsFormState {
  enabled: boolean;
  provider: string;
  accessId: string;
  secret: string;
  ipv4: DdnsFamilyForm;
  ipv6: DdnsFamilyForm;
}

function emptyDdnsFamily(kind: "ipv4" | "ipv6"): DdnsFamilyForm {
  return {
    enabled: false,
    domainsText: "",
    interval: 300,
    getType: "url",
    url: DEFAULT_DDNS_URLS[kind],
    netInterface: "",
    ipReg: "",
  };
}

function ddnsFamilyFrom(
  family: NetworkDdnsFamily | undefined,
  kind: "ipv4" | "ipv6"
): DdnsFamilyForm {
  const base = emptyDdnsFamily(kind);
  if (!family) return base;
  return {
    ...base,
    enabled: family.enabled,
    domainsText: (family.manual_domains ?? family.domains).join("\n"),
    interval: family.interval,
    getType: family.get_type,
    url: family.url || base.url,
    netInterface: family.net_interface,
    ipReg: family.ip_reg,
  };
}

function ddnsSharedFrom(
  ipv4: NetworkDdnsFamily | undefined,
  ipv6: NetworkDdnsFamily | undefined
) {
  // 两套配置现在共用一套凭据，优先取已启用的那一套
  const preferred =
    ipv4 && (ipv4.enabled || ipv4.provider !== "cloudflare")
      ? ipv4
      : ipv6 || ipv4;
  return {
    provider: preferred?.provider || "cloudflare",
    accessId: preferred?.access_id || "",
  };
}

const EMPTY_DDNS_FORM: DdnsFormState = {
  enabled: false,
  provider: "cloudflare",
  accessId: "",
  secret: "",
  ipv4: emptyDdnsFamily("ipv4"),
  ipv6: emptyDdnsFamily("ipv6"),
};

function splitDomains(text: string): string[] {
  return text
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function isDirty(config: NetworkConfig, form: FormState): boolean {
  return (
    form.enabled !== config.enabled ||
    form.provider !== config.provider ||
    form.targetHost.trim() !== config.target_host ||
    form.luckyUrl.trim() !== config.lucky.url ||
    form.luckyUsername.trim() !== config.lucky.username ||
    form.listenPort !== config.lucky.listen_port ||
    form.enableTls !== config.lucky.enable_tls ||
    form.npmUrl.trim() !== config.npm.url ||
    form.npmEmail.trim() !== config.npm.email ||
    form.certificateId !== String(config.npm.certificate_id) ||
    form.sslForced !== config.npm.ssl_forced ||
    form.ddnsgoUrl.trim() !== config.ddnsgo.url ||
    form.ddnsgoUsername.trim() !== config.ddnsgo.username ||
    form.luckyPassword.length > 0 ||
    form.npmPassword.length > 0 ||
    form.ddnsgoPassword.length > 0
  );
}

function toPayload(form: FormState): NetworkUpdate {
  return {
    enabled: form.enabled,
    provider: form.provider,
    target_host: form.targetHost.trim(),
    lucky: {
      url: form.luckyUrl.trim(),
      username: form.luckyUsername.trim(),
      password: form.luckyPassword || undefined,
      listen_port: form.listenPort,
      enable_tls: form.enableTls,
    },
    npm: {
      url: form.npmUrl.trim(),
      email: form.npmEmail.trim(),
      password: form.npmPassword || undefined,
      certificate_id:
        form.certificateId === "new" ? "new" : Number(form.certificateId) || 0,
      ssl_forced: form.sslForced,
    },
    ddnsgo: {
      url: form.ddnsgoUrl.trim(),
      username: form.ddnsgoUsername.trim(),
      password: form.ddnsgoPassword || undefined,
    },
  };
}

interface DdnsFamilyCardProps {
  kind: "ipv4" | "ipv6";
  title: string;
  recordLabel: string;
  value: DdnsFamilyForm;
  autoDomains: string[];
  onChange: (patch: Partial<DdnsFamilyForm>) => void;
  interfaces: NetworkDdnsInterface[];
}

function DdnsFamilyCard({
  kind,
  title,
  recordLabel,
  value,
  autoDomains,
  onChange,
  interfaces,
}: DdnsFamilyCardProps) {
  const idPrefix = `ddns_${kind}`;

  return (
    <div className="space-y-3 rounded-lg border p-3">
      <div className="flex items-center justify-between gap-2">
        <div className="flex items-center gap-2">
          <Badge variant={value.enabled ? "default" : "secondary"}>
            {recordLabel}
          </Badge>
          <span className="text-sm font-medium">{title}</span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[11px] text-muted-foreground">启用</span>
          <Switch
            checked={value.enabled}
            onCheckedChange={(checked) => onChange({ enabled: checked })}
          />
        </div>
      </div>

      <div className="grid gap-3 md:grid-cols-2">
        <div className="space-y-1">
          <Label htmlFor={`${idPrefix}_get_type`} className="text-xs">
            获取 IP 方式
          </Label>
          <select
            id={`${idPrefix}_get_type`}
            value={value.getType}
            onChange={(e) =>
              onChange({
                getType: e.target.value as DdnsFamilyForm["getType"],
              })
            }
            className="h-8 w-full rounded-md border border-input bg-white px-2 text-sm shadow-none outline-none focus-visible:border-foreground/60"
          >
            <option value="url">请求 URL</option>
            <option value="netInterface">
              {interfaces.length ? "网卡" : "网卡（手动填写）"}
            </option>
          </select>
        </div>
        <div className="space-y-1">
          <Label htmlFor={`${idPrefix}_interval`} className="text-xs">
            检查间隔（秒）
          </Label>
          <Input
            id={`${idPrefix}_interval`}
            type="number"
            min={30}
            max={3600}
            value={value.interval}
            onChange={(e) =>
              onChange({ interval: Number(e.target.value) || 0 })
            }
            className={`${INPUT_CLASS} max-w-[160px]`}
          />
        </div>
      </div>

      {value.getType === "url" ? (
        <div className="space-y-1">
          <Label htmlFor={`${idPrefix}_url`} className="text-xs">
            IP 查询地址（每行一个，按顺序尝试）
          </Label>
          <textarea
            id={`${idPrefix}_url`}
            value={value.url}
            onChange={(e) => onChange({ url: e.target.value })}
            rows={2}
            placeholder={DEFAULT_DDNS_URLS[kind]}
            className="w-full resize-y rounded-md border border-input bg-white px-2 py-1.5 text-sm shadow-none outline-none focus-visible:border-foreground/60"
          />
        </div>
      ) : (
        <div className="grid gap-3 md:grid-cols-2">
          <div className="space-y-1">
            <Label htmlFor={`${idPrefix}_interface`} className="text-xs">
              网卡
            </Label>
            {interfaces.length > 0 ? (
              <select
                id={`${idPrefix}_interface`}
                value={value.netInterface}
                onChange={(e) =>
                  onChange({ netInterface: e.target.value })
                }
                className="h-8 w-full rounded-md border border-input bg-white px-2 text-sm shadow-none outline-none focus-visible:border-foreground/60"
              >
                <option value="">选择网卡</option>
                {interfaces.map((item) => (
                  <option key={item.name} value={item.name}>
                    {item.name}
                    {item.addresses.length
                      ? `（${item.addresses.join(", ")}）`
                      : ""}
                  </option>
                ))}
              </select>
            ) : (
              <Input
                id={`${idPrefix}_interface`}
                value={value.netInterface}
                onChange={(e) =>
                  onChange({ netInterface: e.target.value })
                }
                placeholder="例如 eth0、ens2"
                className={INPUT_CLASS}
              />
            )}
          </div>
          <div className="space-y-1">
            <Label htmlFor={`${idPrefix}_ip_reg`} className="text-xs">
              IP 筛选正则（可选）
            </Label>
            <Input
              id={`${idPrefix}_ip_reg`}
              value={value.ipReg}
              onChange={(e) => onChange({ ipReg: e.target.value })}
              placeholder="例如 ^240[0-9]"
              className={INPUT_CLASS}
            />
          </div>
        </div>
      )}

      <div className="grid gap-3 md:grid-cols-2">
        <div className="space-y-1">
          <Label htmlFor={`${idPrefix}_domains`} className="text-xs">
            手动域名
          </Label>
          <textarea
            id={`${idPrefix}_domains`}
            value={value.domainsText}
            onChange={(e) => onChange({ domainsText: e.target.value })}
            rows={3}
            placeholder={"home.example.com\nmedia.example.com"}
            className="w-full resize-y rounded-md border border-input bg-white px-2 py-1.5 text-sm shadow-none outline-none focus-visible:border-foreground/60"
          />
          <p className="text-[11px] text-muted-foreground">
            每行一个，也可用逗号分隔
          </p>
        </div>
        <div className="space-y-1">
          <div className="flex items-center justify-between">
            <Label className="text-xs">自动域名</Label>
            <span className="text-[11px] text-muted-foreground">反向代理</span>
          </div>
          <div className="flex min-h-[76px] flex-wrap content-start gap-1.5 rounded-md border bg-muted/30 p-2">
            {autoDomains.length > 0 ? (
              autoDomains.map((domain) => (
                <span
                  key={domain}
                  className="h-fit rounded border bg-background px-1.5 py-0.5 font-mono text-[11px] text-foreground"
                >
                  {domain}
                </span>
              ))
            ) : (
              <span className="text-[11px] text-muted-foreground">
                暂无自动域名
              </span>
            )}
          </div>
          <p className="text-[11px] text-muted-foreground">随反向代理增删</p>
        </div>
      </div>
    </div>
  );
}

export function NetworkIntegrationTab() {
  const { data: config, isLoading, error } = useNetworkConfig();
  const { data: apps = [], isLoading: appsLoading } = useNetworkApps();
  const ddnsStateQuery = useNetworkDdns(!!config?.configured);
  const { data: ddnsState } = ddnsStateQuery;
  const updateConfig = useUpdateNetwork();
  const testLucky = useTestNetwork();
  const testNpm = useTestNetwork();
  const testDdnsGo = useTestNetwork();
  const deleteProxy = useDeleteNetworkProxy();
  const saveDdns = useSaveNetworkDdns();
  const deleteDdns = useDeleteNetworkDdns();

  const [form, setForm] = useState<FormState>(EMPTY_FORM);
  const [initialized, setInitialized] = useState(false);
  const [proxyDialogOpen, setProxyDialogOpen] = useState(false);
  const [editingProxy, setEditingProxy] = useState<NetworkApp | null>(null);
  const [customHost, setCustomHost] = useState<string | null>(null);
  const [revealProvider, setRevealProvider] =
    useState<NetworkSecretProvider | null>(null);
  const [ddnsForm, setDdnsForm] = useState<DdnsFormState>(EMPTY_DDNS_FORM);
  const [ddnsInitialized, setDdnsInitialized] = useState(false);
  const isNpm = form.provider === "npm";
  const hostsQuery = useNetworkHosts();
  const ddnsInterfacesQuery = useNetworkDdnsInterfaces(
    !!config?.configured && form.provider === "lucky"
  );
  const certificatesQuery = useNpmCertificates(
    form.provider === "npm" && !!config?.npm.password_set
  );

  useEffect(() => {
    if (config && !initialized) {
      setForm(formFromConfig(config));
      setInitialized(true);
    }
  }, [config, initialized]);

  useEffect(() => {
    if (ddnsState && !ddnsInitialized) {
      const shared = ddnsSharedFrom(ddnsState.ipv4, ddnsState.ipv6);
      setDdnsForm({
        enabled: ddnsState.enabled,
        provider: shared.provider,
        accessId: shared.accessId,
        secret: "",
        ipv4: ddnsFamilyFrom(ddnsState.ipv4, "ipv4"),
        ipv6: ddnsFamilyFrom(ddnsState.ipv6, "ipv6"),
      });
      setDdnsInitialized(true);
    }
  }, [ddnsState, ddnsInitialized]);

  const handleProviderChange = (provider: NetworkProvider) => {
    setForm((prev) => ({ ...prev, provider }));
    setDdnsForm((prev) => {
      const allowed = DNS_PROVIDERS[provider];
      const valid = allowed.some((item) => item.value === prev.provider);
      return valid
        ? prev
        : { ...prev, provider: allowed[0].value };
    });
  };

  const handleSave = () => {
    updateConfig.mutate(toPayload(form), {
      onSuccess: (data) => setForm(formFromConfig(data)),
    });
  };

  const applyTargetHost = (value: string) => {
    const targetHost = value.trim();
    setForm((prev) => ({ ...prev, targetHost }));
    if (!config || targetHost === config.target_host) return;
    updateConfig.mutate(toPayload({ ...form, targetHost }), {
      onSuccess: (data) => setForm(formFromConfig(data)),
    });
  };

  const commitCustomHost = () => {
    if (customHost === null) return;
    applyTargetHost(customHost);
    setCustomHost(null);
  };

  const handleTestLucky = () => {
    testLucky.mutate({
      provider: "lucky",
      url: form.luckyUrl.trim() || null,
      username: form.luckyUsername.trim() || null,
      password: form.luckyPassword || null,
    });
  };

  const handleTestNpm = () => {
    testNpm.mutate({
      provider: "npm",
      url: form.npmUrl.trim() || null,
      email: form.npmEmail.trim() || null,
      password: form.npmPassword || null,
    });
  };

  const handleTestDdnsGo = () => {
    testDdnsGo.mutate({
      provider: "ddnsgo",
      url: form.ddnsgoUrl.trim() || null,
      username: form.ddnsgoUsername.trim() || null,
      password: form.ddnsgoPassword || null,
    });
  };

  const openCreateProxy = () => {
    setEditingProxy(null);
    setProxyDialogOpen(true);
  };

  const openEditProxy = (app: NetworkApp) => {
    setEditingProxy(app);
    setProxyDialogOpen(true);
  };

  const handleRemoveProxy = (instanceId: number) => {
    deleteProxy.mutate(instanceId);
  };

  const updateDdnsFamily = (
    kind: "ipv4" | "ipv6",
    patch: Partial<DdnsFamilyForm>
  ) => {
    setDdnsForm((prev) => ({
      ...prev,
      [kind]: { ...prev[kind], ...patch },
    }));
  };

  const handleSaveDdns = () => {
    const toFamily = (family: DdnsFamilyForm) => ({
      enabled: family.enabled,
      provider: ddnsForm.provider,
      access_id: ddnsForm.accessId.trim(),
      secret: ddnsForm.secret || undefined,
      domains: splitDomains(family.domainsText),
      interval: Math.min(3600, Math.max(30, family.interval || 300)),
      get_type: family.getType,
      url: family.url,
      net_interface: family.netInterface,
      ip_reg: family.ipReg,
    });
    saveDdns.mutate(
      {
        enabled: ddnsForm.enabled,
        ipv4: toFamily(ddnsForm.ipv4),
        ipv6: toFamily(ddnsForm.ipv6),
      },
      {
        onSuccess: (data) => {
          const shared = ddnsSharedFrom(data.ipv4, data.ipv6);
          setDdnsForm({
            enabled: data.enabled,
            provider: shared.provider,
            accessId: shared.accessId,
            secret: "",
            ipv4: ddnsFamilyFrom(data.ipv4, "ipv4"),
            ipv6: ddnsFamilyFrom(data.ipv6, "ipv6"),
          });
        },
      }
    );
  };

  if (isLoading) {
    return <div className="text-sm text-muted-foreground">加载配置中...</div>;
  }
  if (error) {
    return (
      <div className="text-sm text-destructive">
        加载失败：{error instanceof Error ? error.message : "未知错误"}
      </div>
    );
  }

  const configuredApps = apps.filter((app) => app.proxy_enabled);
  const busy = deleteProxy.isPending;
  const dnsProviders = DNS_PROVIDERS[form.provider];
  const ipv4Interfaces = ddnsInterfacesQuery.data?.ipv4 ?? [];
  const ipv6Interfaces = ddnsInterfacesQuery.data?.ipv6 ?? [];
  const hostOptions = hostsQuery.data?.candidates ?? [];
  const savedHost = form.targetHost.trim();
  const extraHost =
    savedHost && !hostOptions.some((item) => item.value === savedHost)
      ? savedHost
      : "";
  const certificates = certificatesQuery.data ?? [];
  const certificateValue =
    form.certificateId === "0" ||
    form.certificateId === "new" ||
    certificates.some((item) => String(item.id) === form.certificateId)
      ? form.certificateId
      : "0";

  return (
    <div className="space-y-3">
      <Card size="sm" className="rounded-xl">
        <CardHeader className="border-b pb-3">
          <CardTitle className="flex items-center gap-2">
            <Network className="h-4 w-4 text-muted-foreground" />
            网络方案
          </CardTitle>
          <CardDescription className="text-[11px]">
            两套方案互斥；切换不会删除旧系统中已写入的规则。
          </CardDescription>
          <CardAction>
            <div className="flex items-center gap-2">
              <span className="text-[11px] text-muted-foreground">启用</span>
              <Switch
                checked={form.enabled}
                onCheckedChange={(checked) =>
                  setForm((prev) => ({ ...prev, enabled: checked }))
                }
              />
            </div>
          </CardAction>
        </CardHeader>

        <CardContent className="space-y-3">
          <div className="grid gap-2 md:grid-cols-2">
            {PROVIDERS.map((provider) => {
              const selected = form.provider === provider.value;
              return (
                <button
                  key={provider.value}
                  type="button"
                  title={provider.detail}
                  onClick={() => handleProviderChange(provider.value)}
                  className={`flex items-start justify-between gap-2 rounded-lg border px-3 py-2 text-left transition-colors ${
                    selected
                      ? "border-primary bg-primary/5"
                      : "border-border hover:border-foreground/30"
                  }`}
                >
                  <span>
                    <span className="block text-xs font-medium">
                      {provider.title}
                    </span>
                    <span className="block text-[11px] text-muted-foreground">
                      {provider.description}
                    </span>
                  </span>
                  {selected && (
                    <Badge className="bg-primary/10 text-[10px] text-primary">
                      当前
                    </Badge>
                  )}
                </button>
              );
            })}
          </div>

          {isNpm ? (
            <div className="space-y-3 border-t border-border pt-3">
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-1.5 text-xs font-medium">
                  <Server className="h-3.5 w-3.5 text-muted-foreground" />
                  Nginx Proxy Manager + DDNS-Go
                </div>
                {/^https?:\/\//i.test(form.npmUrl.trim()) && (
                  <a
                    href={form.npmUrl.trim()}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1 text-[11px] text-primary hover:underline"
                  >
                    打开 NPM
                    <ExternalLink className="h-3 w-3" />
                  </a>
                )}
              </div>

              <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-[minmax(0,1.5fr)_minmax(0,0.9fr)_minmax(0,0.9fr)_minmax(0,0.9fr)_minmax(0,1.1fr)]">
                <div className="space-y-1">
                <Label htmlFor="npm_url" className="text-xs">
                  服务地址
                </Label>
                <Input
                  id="npm_url"
                  value={form.npmUrl}
                  onChange={(e) =>
                    setForm((prev) => ({ ...prev, npmUrl: e.target.value }))
                  }
                  placeholder="http://192.168.1.23:81"
                  className={INPUT_CLASS}
                />
              </div>

              <div className="space-y-1">
                <Label htmlFor="npm_email" className="text-xs">
                  登录邮箱
                </Label>
                <Input
                  id="npm_email"
                  value={form.npmEmail}
                  onChange={(e) =>
                    setForm((prev) => ({ ...prev, npmEmail: e.target.value }))
                  }
                  placeholder="admin@example.com"
                  className={INPUT_CLASS}
                />
              </div>

              <div className="space-y-1">
                <Label htmlFor="npm_password" className="text-xs">
                  登录密码
                </Label>
                <PasswordInput
                  id="npm_password"
                  value={form.npmPassword}
                  onChange={(e) =>
                    setForm((prev) => ({
                      ...prev,
                      npmPassword: e.target.value,
                    }))
                  }
                  placeholder={
                    config?.npm.password_set ? "••••••••" : "NPM 登录密码"
                  }
                  onToggleClick={() => {
                    if (!form.npmPassword && config?.npm.password_set) {
                      setRevealProvider("npm");
                      return true;
                    }
                    return false;
                  }}
                  className={INPUT_CLASS}
                />
              </div>

              <div className="space-y-1">
                <Label htmlFor="npm_certificate" className="text-xs">
                  SSL 证书
                </Label>
                <select
                  id="npm_certificate"
                  value={certificateValue}
                  onChange={(e) =>
                    setForm((prev) => ({
                      ...prev,
                      certificateId: e.target.value,
                    }))
                  }
                  className="h-8 w-full rounded-md border border-input bg-white px-2 text-sm shadow-none outline-none focus-visible:border-foreground/60"
                >
                  <option value="0">不启用 HTTPS</option>
                  <option value="new">自动申请 Let&apos;s Encrypt</option>
                  {certificates.map((certificate) => (
                    <option key={certificate.id} value={String(certificate.id)}>
                      {certificate.nice_name || `证书 #${certificate.id}`}
                    </option>
                  ))}
                </select>
              </div>

              <div className="flex min-w-0 items-end justify-end gap-2">
                <Button
                  type="button"
                  variant="outline"
                  size="sm"
                  onClick={handleTestNpm}
                  disabled={testNpm.isPending}
                  className="shrink-0"
                >
                  {testNpm.isPending ? (
                    <Loader2 className="h-3.5 w-3.5 animate-spin" />
                  ) : (
                    <Globe2 className="h-3.5 w-3.5" />
                  )}
                  测试
                </Button>
                <span
                  className={`min-w-0 max-w-[240px] truncate pb-2 text-right text-[11px] ${
                    config?.status.ok === false
                      ? "text-destructive"
                      : "text-muted-foreground"
                  }`}
                  title={config?.status.message || undefined}
                >
                  {config?.status.message
                    ? `最近状态：${config.status.message}`
                    : "尚未测试连接"}
                </span>
              </div>
            </div>

            {testNpm.data && <TestFeedback data={testNpm.data} />}

            <div className="border-t border-border pt-3">
              <div className="mb-2 flex items-center justify-between">
                <div className="flex items-center gap-1.5 text-xs font-medium">
                  <Cloud className="h-3.5 w-3.5 text-muted-foreground" />
                  DDNS-Go
                </div>
                {/^https?:\/\//i.test(form.ddnsgoUrl.trim()) && (
                  <a
                    href={form.ddnsgoUrl.trim()}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1 text-[11px] text-primary hover:underline"
                  >
                    打开 DDNS-Go
                    <ExternalLink className="h-3 w-3" />
                  </a>
                )}
              </div>

              <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,0.9fr)_minmax(0,0.9fr)_100px]">
                <div className="space-y-1">
                  <Label htmlFor="ddnsgo_url" className="text-xs">
                    服务地址
                  </Label>
                  <Input
                    id="ddnsgo_url"
                    value={form.ddnsgoUrl}
                    onChange={(e) =>
                      setForm((prev) => ({
                        ...prev,
                        ddnsgoUrl: e.target.value,
                      }))
                    }
                    placeholder="http://192.168.1.23:9876"
                    className={INPUT_CLASS}
                  />
                </div>

                <div className="space-y-1">
                  <Label htmlFor="ddnsgo_username" className="text-xs">
                    登录账号
                  </Label>
                  <Input
                    id="ddnsgo_username"
                    value={form.ddnsgoUsername}
                    onChange={(e) =>
                      setForm((prev) => ({
                        ...prev,
                        ddnsgoUsername: e.target.value,
                      }))
                    }
                    placeholder="admin"
                    className={INPUT_CLASS}
                  />
                </div>

                <div className="space-y-1">
                  <Label htmlFor="ddnsgo_password" className="text-xs">
                    登录密码
                  </Label>
                  <PasswordInput
                    id="ddnsgo_password"
                    value={form.ddnsgoPassword}
                    onChange={(e) =>
                      setForm((prev) => ({
                        ...prev,
                        ddnsgoPassword: e.target.value,
                      }))
                    }
                    placeholder={
                      config?.ddnsgo.password_set
                        ? "••••••••"
                        : "DDNS-Go 登录密码"
                    }
                    onToggleClick={() => {
                      if (!form.ddnsgoPassword && config?.ddnsgo.password_set) {
                        setRevealProvider("ddnsgo");
                        return true;
                      }
                      return false;
                    }}
                    className={INPUT_CLASS}
                  />
                </div>

                <div className="flex items-end">
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={handleTestDdnsGo}
                    disabled={testDdnsGo.isPending}
                    className="w-full"
                  >
                    {testDdnsGo.isPending ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <Globe2 className="h-3.5 w-3.5" />
                    )}
                    测试
                  </Button>
                </div>
              </div>

              {testDdnsGo.data && <TestFeedback data={testDdnsGo.data} />}
              </div>
            </div>
          ) : (
            <div className="space-y-3 border-t border-border pt-3">
              <div className="flex items-center justify-between gap-2">
                <div className="flex items-center gap-1.5 text-xs font-medium">
                  <Network className="h-3.5 w-3.5 text-muted-foreground" />
                  Lucky
                </div>
                {/^https?:\/\//i.test(form.luckyUrl.trim()) && (
                  <a
                    href={form.luckyUrl.trim()}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex items-center gap-1 text-[11px] text-primary hover:underline"
                  >
                    打开 Lucky
                    <ExternalLink className="h-3 w-3" />
                  </a>
                )}
              </div>

              <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-[minmax(0,1.6fr)_minmax(0,0.9fr)_minmax(0,0.9fr)_minmax(0,1.1fr)]">
                <div className="space-y-1">
                  <Label htmlFor="lucky_url" className="text-xs">
                    服务地址
                  </Label>
                  <Input
                    id="lucky_url"
                    value={form.luckyUrl}
                    onChange={(e) =>
                      setForm((prev) => ({ ...prev, luckyUrl: e.target.value }))
                    }
                    placeholder="http://192.168.1.23:16601"
                    className={INPUT_CLASS}
                  />
                </div>

              <div className="space-y-1">
                <Label htmlFor="lucky_username" className="text-xs">
                  登录账号
                </Label>
                <Input
                  id="lucky_username"
                  value={form.luckyUsername}
                  onChange={(e) =>
                    setForm((prev) => ({
                      ...prev,
                      luckyUsername: e.target.value,
                    }))
                  }
                  placeholder="666"
                  className={INPUT_CLASS}
                />
              </div>

              <div className="space-y-1">
                <Label htmlFor="lucky_password" className="text-xs">
                  登录密码
                </Label>
                <div className="flex min-w-0 items-center gap-2">
                  <PasswordInput
                    id="lucky_password"
                    value={form.luckyPassword}
                    onChange={(e) =>
                      setForm((prev) => ({
                        ...prev,
                        luckyPassword: e.target.value,
                      }))
                    }
                    placeholder={
                      config?.lucky.password_set ? "••••••••" : "Lucky 登录密码"
                    }
                    onToggleClick={() => {
                      if (!form.luckyPassword && config?.lucky.password_set) {
                        setRevealProvider("lucky");
                        return true;
                      }
                      return false;
                    }}
                    containerClassName="min-w-0 flex-1"
                    className={INPUT_CLASS}
                  />
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={handleTestLucky}
                    disabled={testLucky.isPending}
                    className="h-8 shrink-0"
                  >
                    {testLucky.isPending ? (
                      <Loader2 className="h-3.5 w-3.5 animate-spin" />
                    ) : (
                      <Globe2 className="h-3.5 w-3.5" />
                    )}
                    测试
                  </Button>
                </div>
              </div>

              <div className="flex min-w-0 items-end justify-start">
                <span
                  className={`min-w-0 max-w-[240px] truncate pb-2 text-left text-[11px] ${
                    config?.status.ok === false
                      ? "text-destructive"
                      : "text-muted-foreground"
                  }`}
                  title={config?.status.message || undefined}
                >
                  {config?.status.message
                    ? `最近状态：${config.status.message}`
                    : "尚未测试连接"}
                </span>
              </div>
            </div>

            {testLucky.data && <TestFeedback data={testLucky.data} />}
            </div>
          )}

          <div className="flex flex-wrap items-center justify-between gap-2 border-t border-border pt-2.5">
            <div className="flex min-w-0 flex-wrap items-center gap-3">
              <div className="flex min-w-0 items-center gap-1.5">
                <Label
                  htmlFor="network_target_host"
                  className="whitespace-nowrap text-[11px] font-normal text-muted-foreground"
                >
                  内网地址
                </Label>
                {customHost === null ? (
                  <select
                    id="network_target_host"
                    value={form.targetHost.trim() || hostsQuery.data?.current || ""}
                    onChange={(e) => {
                      if (e.target.value === CUSTOM_HOST) {
                        setCustomHost(form.targetHost);
                        return;
                      }
                      applyTargetHost(e.target.value);
                    }}
                    title="反向代理后端使用的主机，默认跟随当前访问地址"
                    className="h-7 max-w-[280px] rounded-md border border-input bg-white px-1.5 text-xs shadow-none outline-none focus-visible:border-foreground/60"
                  >
                    {!hostsQuery.data?.current && (
                      <option value="" disabled>
                        {hostsQuery.isFetching ? "识别中…" : "未识别到地址"}
                      </option>
                    )}
                    {hostOptions.map((item) => (
                      <option key={item.value} value={item.value}>
                        {item.label}
                      </option>
                    ))}
                    {extraHost && <option value={extraHost}>{extraHost}</option>}
                    <option value={CUSTOM_HOST}>自定义…</option>
                  </select>
                ) : (
                  <div className="flex items-center gap-1">
                    <Input
                      autoFocus
                      value={customHost}
                      onChange={(e) => setCustomHost(e.target.value)}
                      onKeyDown={(e) => {
                        if (e.key === "Enter") commitCustomHost();
                        if (e.key === "Escape") setCustomHost(null);
                      }}
                      placeholder="192.168.1.10"
                      className="h-7 w-40 px-1.5 text-xs"
                    />
                    <Button
                      type="button"
                      size="sm"
                      variant="secondary"
                      className="h-7 px-2 text-xs"
                      onClick={commitCustomHost}
                    >
                      确定
                    </Button>
                  </div>
                )}
                <Button
                  type="button"
                  size="icon"
                  variant="ghost"
                  className="h-7 w-7"
                  title="重新识别内网地址"
                  onClick={() => hostsQuery.refetch()}
                >
                  <RefreshCw
                    className={`h-3.5 w-3.5 ${
                      hostsQuery.isFetching ? "animate-spin" : ""
                    }`}
                  />
                </Button>
              </div>

              {isNpm && (
                <div
                  className="flex items-center gap-1.5 text-[11px] text-muted-foreground"
                  title="选择证书后，把 HTTP 请求强制跳转到 HTTPS"
                >
                  <span>强制 HTTPS</span>
                  <Switch
                    checked={form.sslForced}
                    onCheckedChange={(checked) =>
                      setForm((prev) => ({ ...prev, sslForced: checked }))
                    }
                  />
                </div>
              )}
            </div>
            <Button
              type="button"
              size="sm"
              onClick={handleSave}
              disabled={updateConfig.isPending || !config || !isDirty(config, form)}
              className="rounded-md"
            >
              {updateConfig.isPending ? "保存中..." : "保存配置"}
            </Button>
          </div>
        </CardContent>
      </Card>

      <Card size="sm" className="rounded-xl">
        <CardHeader className="border-b pb-3">
          <CardTitle className="flex items-center gap-2">
            <Route className="h-4 w-4 text-muted-foreground" />
            反向代理与域名
          </CardTitle>
          <CardDescription className="text-[11px]">
            {isNpm
              ? "为应用创建 NPM 代理主机，可绑定多域名和已有证书。"
              : "为应用创建 Lucky 规则，按端口分组，支持多域名、HTTPS 和 IPv6。"}
            修改连接配置后需重新应用。
          </CardDescription>
          <CardAction>
            <Button
              type="button"
              size="sm"
              onClick={openCreateProxy}
              disabled={
                !config?.enabled ||
                !config?.configured ||
                busy
              }
            >
              新建代理
            </Button>
          </CardAction>
        </CardHeader>

        <CardContent className="pt-3">
          {appsLoading ? (
            <div className="py-6 text-center text-sm text-muted-foreground">
              加载应用列表中...
            </div>
          ) : configuredApps.length === 0 ? (
            <div className="flex flex-col items-center gap-3 rounded-lg border border-dashed py-8">
              <Route className="h-7 w-7 text-muted-foreground/50" />
              <p className="text-sm text-muted-foreground">
                还没有反向代理配置
              </p>
              <Button
                type="button"
                size="sm"
                variant="outline"
                onClick={openCreateProxy}
                disabled={!config?.enabled || !config?.configured}
              >
                新建反向代理
              </Button>
            </div>
          ) : (
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>应用</TableHead>
                  <TableHead>域名</TableHead>
                  <TableHead>后端地址</TableHead>
                  {!isNpm && <TableHead className="w-[80px]">端口</TableHead>}
                  <TableHead className="w-[110px]">HTTPS</TableHead>
                  <TableHead className="w-[130px] text-right">操作</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {configuredApps.map((app) => (
                  <TableRow key={app.instance_id}>
                    <TableCell>
                      <div className="flex flex-col">
                        <span className="text-sm font-medium text-foreground">
                          {app.display_name}
                        </span>
                        <span className="text-xs text-muted-foreground">
                          {app.instance_name}
                        </span>
                      </div>
                    </TableCell>
                    <TableCell>
                      <div className="flex max-w-[280px] flex-wrap gap-1">
                        {app.domains.map((domain) => (
                          <span
                            key={domain}
                            className="rounded bg-muted px-1.5 py-0.5 text-[11px] text-foreground"
                          >
                            {domain}
                          </span>
                        ))}
                      </div>
                    </TableCell>
                    <TableCell className="max-w-[220px] truncate text-xs text-muted-foreground">
                      {app.target_url ?? app.url ?? "-"}
                    </TableCell>
                    {!isNpm && (
                      <TableCell className="text-xs text-muted-foreground">
                        {app.listen_port ?? "-"}
                      </TableCell>
                    )}
                    <TableCell>
                      {app.enable_tls ? (
                        <Badge className="bg-emerald-500/10 text-emerald-700">
                          HTTPS
                        </Badge>
                      ) : (
                        <Badge variant="outline">HTTP</Badge>
                      )}
                    </TableCell>
                    <TableCell className="text-right">
                      <div className="flex justify-end gap-1">
                        <Button
                          type="button"
                          variant="outline"
                          size="sm"
                          onClick={() => openEditProxy(app)}
                          disabled={busy}
                        >
                          编辑
                        </Button>
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          className="text-destructive hover:text-destructive"
                          onClick={() => {
                            if (confirm(`确定删除 ${app.display_name} 的反向代理配置吗？`)) {
                              handleRemoveProxy(app.instance_id);
                            }
                          }}
                          disabled={busy}
                        >
                          删除
                        </Button>
                      </div>
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          )}
        </CardContent>
      </Card>

      <ProxyDialog
        open={proxyDialogOpen}
        onOpenChange={setProxyDialogOpen}
        provider={form.provider}
        apps={apps}
        editing={editingProxy}
        defaultPort={config?.lucky.listen_port || 443}
        defaultCertificateId={Number(form.certificateId) || 0}
      />

      <Card size="sm" className="rounded-xl">
        <CardHeader className="border-b pb-3">
          <CardTitle className="flex items-center gap-2">
            <Cloud className="h-4 w-4 text-muted-foreground" />
            DDNS 动态域名
          </CardTitle>
          <CardDescription className="text-[11px]">
            {isNpm
              ? "在 DDNS-Go 中创建「OneTap」配置；IPv4 / IPv6 分开设置域名和取 IP 方式。"
              : "在 Lucky 中为 IPv4 / IPv6 各创建一个任务，可按请求 URL 或网卡获取公网 IP。"}
          </CardDescription>
          <CardAction>
            <div className="flex items-center gap-2">
              <span className="text-[11px] text-muted-foreground">启用</span>
              <Switch
                checked={ddnsForm.enabled}
                onCheckedChange={(checked) =>
                  setDdnsForm((prev) => ({ ...prev, enabled: checked }))
                }
              />
            </div>
          </CardAction>
        </CardHeader>

        <CardContent className="space-y-3">
          <div className="space-y-2 rounded-lg border bg-muted/20 p-3">
            <div>
              <p className="text-xs font-medium">DNS 服务商</p>
              <p className="text-[11px] text-muted-foreground">
                {isNpm
                  ? "DDNS-Go 一个配置项只能使用一套 DNS 凭据，IPv4 / IPv6 共用这里的服务商和密钥。"
                  : "IPv4 / IPv6 共用同一套 DNS 服务商凭据，保存时会分别写入两条 Lucky 任务。"}
              </p>
            </div>
            <div className="grid gap-3 md:grid-cols-3">
              <div className="space-y-1">
                <Label htmlFor="ddns_shared_provider" className="text-xs">
                  服务商
                </Label>
                <select
                  id="ddns_shared_provider"
                  value={ddnsForm.provider}
                  onChange={(e) =>
                    setDdnsForm((prev) => ({
                      ...prev,
                      provider: e.target.value,
                    }))
                  }
                  className="h-8 w-full rounded-md border border-input bg-white px-2 text-sm shadow-none outline-none focus-visible:border-foreground/60"
                >
                  {dnsProviders.map((provider) => (
                    <option key={provider.value} value={provider.value}>
                      {provider.label}
                    </option>
                  ))}
                </select>
              </div>
              <div className="space-y-1">
                <Label htmlFor="ddns_shared_access_id" className="text-xs">
                  账号 ID
                </Label>
                <Input
                  id="ddns_shared_access_id"
                  value={ddnsForm.accessId}
                  onChange={(e) =>
                    setDdnsForm((prev) => ({
                      ...prev,
                      accessId: e.target.value,
                    }))
                  }
                  placeholder={
                    (PROVIDER_HINTS[ddnsForm.provider] ?? { id: "账号 ID" }).id
                  }
                  className={INPUT_CLASS}
                />
              </div>
              <div className="space-y-1">
                <Label htmlFor="ddns_shared_secret" className="text-xs">
                  API 密钥
                </Label>
                <PasswordInput
                  id="ddns_shared_secret"
                  value={ddnsForm.secret}
                  onChange={(e) =>
                    setDdnsForm((prev) => ({
                      ...prev,
                      secret: e.target.value,
                    }))
                  }
                  placeholder={
                    ddnsState?.ipv4.secret_set || ddnsState?.ipv6.secret_set
                      ? "••••••••"
                      : (PROVIDER_HINTS[ddnsForm.provider] ?? {
                          secret: "Token / Secret",
                        }).secret
                  }
                  onToggleClick={() => {
                    if (
                      !ddnsForm.secret &&
                      (ddnsState?.ipv4.secret_set || ddnsState?.ipv6.secret_set)
                    ) {
                      setRevealProvider("ddns");
                      return true;
                    }
                    return false;
                  }}
                  className={INPUT_CLASS}
                />
              </div>
            </div>
            {ddnsForm.provider !== "alidns" &&
              ddnsForm.provider !== "aliesa" &&
              ddnsForm.accessId.trim().startsWith("LTAI") && (
                <p className="text-[11px] text-amber-600">
                  账号 ID 看起来是阿里云 AccessKey，请把服务商切换为「阿里云 DNS」。
                </p>
              )}
          </div>

          <div className="grid gap-3 xl:grid-cols-2">
            <DdnsFamilyCard
              kind="ipv4"
              title="IPv4 动态域名"
              recordLabel="A 记录"
              value={ddnsForm.ipv4}
              autoDomains={ddnsState?.ipv4.auto_domains ?? []}
              onChange={(patch) => updateDdnsFamily("ipv4", patch)}
              interfaces={ipv4Interfaces}
            />
            <DdnsFamilyCard
              kind="ipv6"
              title="IPv6 动态域名"
              recordLabel="AAAA 记录"
              value={ddnsForm.ipv6}
              autoDomains={ddnsState?.ipv6.auto_domains ?? []}
              onChange={(patch) => updateDdnsFamily("ipv6", patch)}
              interfaces={ipv6Interfaces}
            />
          </div>

          {ddnsState?.message && (
            <div className="text-[11px] text-amber-600">{ddnsState.message}</div>
          )}

          {ddnsState?.tasks && ddnsState.tasks.length > 0 && (
            <div className="space-y-2">
              <div className="flex items-center justify-end">
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() => ddnsStateQuery.refetch()}
                  disabled={ddnsStateQuery.isFetching}
                >
                  <RefreshCw
                    className={`h-3.5 w-3.5 ${
                      ddnsStateQuery.isFetching ? "animate-spin" : ""
                    }`}
                  />
                  刷新状态
                </Button>
              </div>
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>任务</TableHead>
                    <TableHead>类型</TableHead>
                    <TableHead>域名</TableHead>
                    <TableHead>当前地址</TableHead>
                    <TableHead>状态</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {ddnsState.tasks.map((task) => (
                    <TableRow key={task.task_key}>
                      <TableCell className="text-sm">{task.task_name}</TableCell>
                      <TableCell className="text-xs text-muted-foreground">
                        {task.task_type}
                      </TableCell>
                      <TableCell className="text-xs text-muted-foreground">
                        {task.domains.join(", ") || "-"}
                      </TableCell>
                      <TableCell className="font-mono text-xs text-muted-foreground">
                        {task.address || "-"}
                      </TableCell>
                      <TableCell>
                        <div className="space-y-1">
                          <div className="flex flex-wrap items-center gap-1.5">
                            <Badge
                              variant={task.enabled ? "default" : "secondary"}
                            >
                              {task.enabled ? "已启用" : "已停用"}
                            </Badge>
                            {task.status === "ok" && (
                              <Badge className="bg-emerald-500/10 text-emerald-700">
                                同步成功
                              </Badge>
                            )}
                            {task.status === "unchanged" && (
                              <Badge variant="secondary">无变化</Badge>
                            )}
                            {task.status === "error" && (
                              <Badge variant="destructive">同步失败</Badge>
                            )}
                            {task.status === null && (
                              <span className="text-[11px] text-muted-foreground">
                                尚未同步
                              </span>
                            )}
                          </div>
                          {task.message && (
                            <p
                              className={`text-[11px] ${
                                task.status === "error"
                                  ? "text-destructive"
                                  : "text-muted-foreground"
                              }`}
                            >
                              {task.message}
                            </p>
                          )}
                          <p className="text-[11px] text-muted-foreground">
                            {task.last_sync
                              ? `上次同步 ${task.last_sync}`
                              : "暂无同步记录"}
                            {task.next_sync ? ` · 下次 ${task.next_sync}` : ""}
                          </p>
                        </div>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}

          <div className="flex items-center justify-end gap-2 border-t border-border pt-3">
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={() =>
                deleteDdns.mutate(undefined, {
                  onSuccess: (data) => {
                    const shared = ddnsSharedFrom(data.ipv4, data.ipv6);
                    setDdnsForm({
                      enabled: data.enabled,
                      provider: shared.provider,
                      accessId: shared.accessId,
                      secret: "",
                      ipv4: ddnsFamilyFrom(data.ipv4, "ipv4"),
                      ipv6: ddnsFamilyFrom(data.ipv6, "ipv6"),
                    });
                  },
                })
              }
              disabled={deleteDdns.isPending || !config?.configured}
            >
              删除任务
            </Button>
            <Button
              type="button"
              size="sm"
              onClick={handleSaveDdns}
              disabled={
                saveDdns.isPending || !config?.enabled || !config?.configured
              }
            >
              {saveDdns.isPending ? (
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
              ) : null}
              保存 DDNS
            </Button>
          </div>
        </CardContent>
      </Card>

      <RevealSecretDialog
        open={revealProvider !== null}
        onOpenChange={(open) => {
          if (!open) setRevealProvider(null);
        }}
        provider={revealProvider}
        title={
          revealProvider === "npm"
            ? "查看 NPM 登录密码"
            : revealProvider === "ddnsgo"
              ? "查看 DDNS-Go 登录密码"
              : revealProvider === "ddns"
                ? "查看 DDNS API 密钥"
                : "查看 Lucky 登录密码"
        }
      />
    </div>
  );
}

function RevealSecretDialog({
  open,
  onOpenChange,
  provider,
  title,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  provider: NetworkSecretProvider | null;
  title: string;
}) {
  const toast = useToast();
  const reveal = useRevealNetworkSecret();
  const [loginPassword, setLoginPassword] = useState("");
  const [secret, setSecret] = useState<string | null>(null);

  useEffect(() => {
    if (!open) {
      setLoginPassword("");
      setSecret(null);
    }
  }, [open]);

  const handleVerify = () => {
    if (!provider || !loginPassword) return;
    reveal.mutate(
      { provider, password: loginPassword },
      { onSuccess: (data) => setSecret(data.password) }
    );
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        onOpenChange(next);
      }}
    >
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
              value={loginPassword}
              onChange={(e) => setLoginPassword(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter") handleVerify();
              }}
              placeholder="输入登录 OneTap 的密码"
              className="h-8"
            />
            <div className="flex justify-end gap-2 pt-2">
              <Button
                variant="outline"
                size="sm"
                onClick={() => onOpenChange(false)}
              >
                取消
              </Button>
              <Button
                size="sm"
                onClick={handleVerify}
                disabled={!loginPassword || reveal.isPending}
              >
                {reveal.isPending ? "验证中..." : "验证并查看"}
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
              <Button size="sm" onClick={() => onOpenChange(false)}>
                关闭
              </Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

function TestFeedback({
  data,
}: {
  data: { ok: boolean; message: string; version?: string | null };
}) {
  return (
    <div
      className={`flex items-start gap-1.5 text-[11px] ${
        data.ok ? "text-emerald-600" : "text-destructive"
      }`}
    >
      {data.ok ? (
        <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0" />
      ) : (
        <XCircle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
      )}
      <span>
        {data.message}
        {data.version ? `（${data.version}）` : ""}
      </span>
    </div>
  );
}
