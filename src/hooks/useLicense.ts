import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  activateLicense,
  deactivateLicense,
  getLicenseStatus,
  refreshLicense,
  type ActivateLicenseRequest,
} from "@/api/license";
import type { ApiError } from "@/api/types";
import { useToast } from "@/components/ui/toast";

export const licenseStatusQueryKey = ["license", "status"] as const;

export function useLicenseStatus() {
  return useQuery({
    queryKey: licenseStatusQueryKey,
    queryFn: getLicenseStatus,
  });
}

export function useActivateLicense() {
  const queryClient = useQueryClient();
  const toast = useToast();

  return useMutation({
    mutationFn: (data: ActivateLicenseRequest) => activateLicense(data),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: licenseStatusQueryKey });
      toast.success("授权激活成功");
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "授权激活失败");
    },
  });
}

export function useRefreshLicense() {
  const queryClient = useQueryClient();
  const toast = useToast();

  return useMutation({
    mutationFn: refreshLicense,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: licenseStatusQueryKey });
      toast.success("授权刷新成功");
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "授权刷新失败");
    },
  });
}

export function useDeactivateLicense() {
  const queryClient = useQueryClient();
  const toast = useToast();

  return useMutation({
    mutationFn: deactivateLicense,
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: licenseStatusQueryKey });
      toast.success("授权已解绑");
    },
    onError: (error: ApiError) => {
      toast.error(error.displayMessage || "授权解绑失败");
    },
  });
}
