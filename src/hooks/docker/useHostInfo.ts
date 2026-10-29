/**
 * Docker 宿主机信息查询 hook
 */

import { useQuery } from "@tanstack/react-query";
import * as dockerApi from "@/api/docker";
import type { HostInfo } from "@/api/docker";

/**
 * 查询 Docker 宿主机综合信息
 *
 * 获取 Docker 宿主机的主机名、操作系统、架构、内核版本、
 * Docker 引擎版本、资源信息、Docker 统计信息和网络列表。
 * 静态展示，不设置自动刷新。
 *
 * @returns useQuery 对象，data 类型为 HostInfo
 */
export function useDockerHostInfo() {
  return useQuery<HostInfo>({
    queryKey: ["docker", "host", "info"],
    queryFn: dockerApi.getDockerHostInfo,
    // CPU / 内存 / 磁盘等实时指标需要周期刷新
    refetchInterval: 10_000,
    staleTime: 5_000,
  });
}
