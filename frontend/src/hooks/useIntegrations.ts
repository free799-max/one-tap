/**
 * 应用集成相关 React Query hooks
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useToast } from "@/components/ui/toast";
import type { ApiError } from "@/api/types";
import { useDeployTasks } from "@/hooks/useDeployTasks";
import {
  listIntegrations,
  getVaultwardenConfig,
  updateVaultwardenConfig,
  deployVaultwarden,
  removeVaultwarden,
  testVaultwarden,
  listVaultwardenApps,
  syncVaultwarden,
  getNetworkConfig,
  updateNetworkConfig,
  deployNetworkLucky,
  removeNetworkLucky,
  testNetwork,
  listNetworkHosts,
  listNetworkApps,
  upsertNetworkProxy,
  deleteNetworkProxy,
  revealIntegrationSecret,
  listNpmCertificates,
  listNetworkCertificates,
  autoProvisionNetworkCertificate,
  getNetworkDdns,
  listNetworkDdnsInterfaces,
  saveNetworkDdns,
  deleteNetworkDdns,
  type VaultwardenConfig,
  type VaultwardenUpdate,
  type VaultwardenApp,
  type VaultwardenSyncResult,
  type NetworkConfig,
  type NetworkUpdate,
  type NetworkHosts,
  type NetworkApp,
  type NetworkDdnsState,
  type NetworkDdnsUpdate,
  type NetworkProxyResult,
  type NetworkCertificate,
  type NetworkCertificateResult,
  type IntegrationSecretOut,
  type IntegrationSecretProvider,
  type IntegrationTestResult,
  type TestableProvider,
} from "@/api/integrations";

export type {
  IntegrationKind,
  IntegrationStatus,
  IntegrationSummary,
  IntegrationTestResult,
  IntegrationApp,
  VaultwardenConfig,
  VaultwardenUpdate,
  VaultwardenApp,
  VaultwardenSyncItem,
  VaultwardenSyncResult,
  NetworkProvider,
  NetworkConfig,
  NetworkUpdate,
  NetworkLuckyDeployRequest,
  NetworkLuckyDeployTask,
  NetworkLuckyRemoveResult,
  NetworkHosts,
  NetworkApp,
  NetworkDdns,
  NetworkDdnsState,
  NetworkDdnsTask,
  NetworkDdnsUpdate,
  NetworkProxyResult,
  NetworkCertificate,
  NpmCertificate,
} from "@/api/integrations";

const ERROR_FALLBACK = "操作失败";

function onMutationError(toast: ReturnType<typeof useToast>) {
  return (error: ApiError) => toast.error(error.displayMessage || ERROR_FALLBACK);
}

function testFeedback(toast: ReturnType<typeof useToast>) {
  return (data: IntegrationTestResult) => {
    if (data.ok) {
      toast.success(data.version ? `连接成功（${data.version}）` : "连接成功");
    } else {
      toast.error(data.message || "连接失败");
    }
  };
}

export function useIntegrations() {
  return useQuery({
    queryKey: ["integrations", "summary"],
    queryFn: listIntegrations,
  });
}

// ---------- Vaultwarden ----------

export function useVaultwardenConfig() {
  return useQuery<VaultwardenConfig>({
    queryKey: ["integrations", "vaultwarden"],
    queryFn: getVaultwardenConfig,
  });
}

export function useUpdateVaultwarden() {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: (data: VaultwardenUpdate) => updateVaultwardenConfig(data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["integrations"] });
      toast.success("保存成功");
    },
    onError: onMutationError(toast),
  });
}

export function useDeployVaultwarden() {
  const qc = useQueryClient();
  const toast = useToast();
  const { startTask } = useDeployTasks();
  return useMutation({
    mutationFn: (data: {
      email?: string | null;
      password?: string | null;
      web_port?: number | null;
      config?: Record<string, unknown> | null;
    }) => deployVaultwarden(data),
    onSuccess: (result) => {
      qc.invalidateQueries({ queryKey: ["integrations"] });
      startTask(result.task_id);
      toast.success("Vaultwarden 部署任务已启动");
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "Vaultwarden 部署失败");
    },
  });
}

export function useRemoveVaultwarden() {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: removeVaultwarden,
    onSuccess: (result) => {
      qc.invalidateQueries({ queryKey: ["integrations"] });
      toast.success(result.message);
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "删除 Vaultwarden 失败");
    },
  });
}

export function useTestVaultwarden() {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: (data: {
      url?: string | null;
      email?: string | null;
      password?: string | null;
    }) => testVaultwarden(data),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["integrations", "vaultwarden"] });
      testFeedback(toast)(data);
    },
    onError: onMutationError(toast),
  });
}

export function useVaultwardenApps(enabled = true) {
  return useQuery<VaultwardenApp[]>({
    queryKey: ["integrations", "vaultwarden", "apps"],
    queryFn: listVaultwardenApps,
    enabled,
  });
}

export function useSyncVaultwarden() {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation<VaultwardenSyncResult, ApiError, number[] | null>({
    mutationFn: (instanceIds) => syncVaultwarden(instanceIds),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["integrations", "vaultwarden"] });
      qc.invalidateQueries({
        queryKey: ["integrations", "vaultwarden", "apps"],
      });
      if (data.ok) toast.success(data.message);
      else toast.error(data.message);
    },
    onError: onMutationError(toast),
  });
}

// ---------- 网络集成 ----------

export function useNetworkConfig() {
  return useQuery<NetworkConfig>({
    queryKey: ["integrations", "network"],
    queryFn: getNetworkConfig,
  });
}

export function useDeployNetworkLucky() {
  const qc = useQueryClient();
  const toast = useToast();
  const { startTask } = useDeployTasks();
  return useMutation({
    mutationFn: (data: {
      username?: string | null;
      password?: string | null;
      web_port?: number | null;
      config?: Record<string, unknown> | null;
    }) => deployNetworkLucky(data),
    onSuccess: (result) => {
      qc.invalidateQueries({ queryKey: ["integrations"] });
      startTask(result.task_id);
      toast.success("Lucky 部署任务已启动");
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "Lucky 部署失败");
    },
  });
}

export function useRemoveNetworkLucky() {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: removeNetworkLucky,
    onSuccess: (result) => {
      qc.invalidateQueries({ queryKey: ["integrations"] });
      toast.success(result.message);
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "删除 Lucky 失败");
    },
  });
}

export function useNetworkHosts() {
  return useQuery<NetworkHosts>({
    queryKey: ["integrations", "network", "hosts"],
    queryFn: listNetworkHosts,
    staleTime: 60_000,
  });
}

export function useUpdateNetwork() {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: (data: NetworkUpdate) => updateNetworkConfig(data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["integrations"] });
      toast.success("保存成功");
    },
    onError: onMutationError(toast),
  });
}

export function useTestNetwork() {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation({
    mutationFn: (data: {
      provider: TestableProvider;
      url?: string | null;
      username?: string | null;
      email?: string | null;
      password?: string | null;
    }) => testNetwork(data),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["integrations", "network"] });
      testFeedback(toast)(data);
    },
    onError: onMutationError(toast),
  });
}

export function useNetworkApps(enabled = true) {
  return useQuery<NetworkApp[]>({
    queryKey: ["integrations", "network", "apps"],
    queryFn: listNetworkApps,
    enabled,
  });
}

export function useUpsertNetworkProxy() {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation<
    NetworkProxyResult,
    ApiError,
    {
      app_instance_id: number;
      custom_name?: string | null;
      domains: string[];
      target_url?: string | null;
      listen_port?: number | null;
      enable_tls?: boolean;
      network?: "tcp" | "tcp4" | "tcp6";
      certificate_id?: number;
      ssl_forced?: boolean;
    }
  >({
    mutationFn: (data) => upsertNetworkProxy(data),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["integrations", "network", "apps"] });
      qc.invalidateQueries({ queryKey: ["integrations", "network", "ddns"] });
      toast.success(data.message);
    },
    onError: onMutationError(toast),
  });
}

export function useDeleteNetworkProxy() {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation<NetworkProxyResult, ApiError, number>({
    mutationFn: (instanceId) => deleteNetworkProxy(instanceId),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ["integrations", "network", "apps"] });
      qc.invalidateQueries({ queryKey: ["integrations", "network", "ddns"] });
      qc.invalidateQueries({ queryKey: ["integrations", "network"] });
      toast.success(data.message);
    },
    onError: onMutationError(toast),
  });
}

export function useRevealIntegrationSecret() {
  const toast = useToast();
  return useMutation<
    IntegrationSecretOut,
    ApiError,
    {
      provider: IntegrationSecretProvider;
      password?: string;
      reveal_token?: string;
    }
  >({
    mutationFn: revealIntegrationSecret,
    onError: onMutationError(toast),
  });
}

export function useNpmCertificates(enabled = true) {
  return useQuery({
    queryKey: ["integrations", "network", "npm-certificates"],
    queryFn: listNpmCertificates,
    enabled,
    retry: false,
  });
}

export function useNetworkCertificates(enabled = true) {
  return useQuery({
    queryKey: ["integrations", "network", "certificates"],
    queryFn: listNetworkCertificates,
    enabled,
    retry: false,
    // 有证书正在 ACME 申请中时轮询，签发完成或失败后自动停止
    refetchInterval: (query) =>
      (query.state.data as NetworkCertificate[] | undefined)?.some(
        (item) => item.status === "pending"
      )
        ? 3000
        : false,
  });
}

export function useAutoNetworkCertificate() {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation<
    NetworkCertificateResult,
    ApiError,
    { domains: string[]; mode?: "auto" | "selfsigned" | "acme" }
  >({
    mutationFn: autoProvisionNetworkCertificate,
    onSuccess: (data) => {
      qc.invalidateQueries({
        queryKey: ["integrations", "network", "certificates"],
      });
      if (data.ok) {
        toast.success(data.message);
      } else {
        toast.error(data.message);
      }
    },
    onError: onMutationError(toast),
  });
}

export function useNetworkDdns(enabled = true) {
  return useQuery<NetworkDdnsState>({
    queryKey: ["integrations", "network", "ddns"],
    queryFn: getNetworkDdns,
    enabled,
    refetchInterval: 60_000,
  });
}

export function useNetworkDdnsInterfaces(enabled = true) {
  return useQuery({
    queryKey: ["integrations", "network", "ddns-interfaces"],
    queryFn: listNetworkDdnsInterfaces,
    enabled,
    retry: false,
    staleTime: 60000,
  });
}

export function useSaveNetworkDdns() {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation<NetworkDdnsState, ApiError, NetworkDdnsUpdate>({
    mutationFn: (data) => saveNetworkDdns(data),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["integrations", "network"] });
      toast.success("DDNS 配置已保存");
    },
    onError: onMutationError(toast),
  });
}

export function useDeleteNetworkDdns() {
  const qc = useQueryClient();
  const toast = useToast();
  return useMutation<NetworkDdnsState, ApiError, void>({
    mutationFn: () => deleteNetworkDdns(),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["integrations", "network"] });
      toast.success("DDNS 任务已删除");
    },
    onError: onMutationError(toast),
  });
}
