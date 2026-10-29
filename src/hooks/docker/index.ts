/**
 * Docker 相关 hooks 统一出口
 *
 * 按职责拆分为：
 * - useContainers：容器查询/操作/日志流/命令执行 + Docker 服务状态
 * - useImages：镜像查询/搜索/拉取/删除/清理
 * - useRegistries：镜像搜索接口配置
 * - usePullProgress：拉取进度（SSE，全局共享连接）
 * - useContainerTerminal：xterm WebSocket 终端
 * - useHostInfo：Docker 宿主机综合信息
 */

export * from "./useContainers";
export * from "./useImages";
export * from "./useRegistries";
export * from "./usePullProgress";
export * from "./useContainerTerminal";
export * from "./useHostInfo";

// 保持既有导出，页面侧 import 路径无需变更
export type {
  BatchImageDeleteRequest,
  ContainerActionResponse,
  ContainerBatchActionRequest,
  ContainerCreateRequest,
  ContainerDetail,
  ContainerExecRequest,
  ContainerExecResponse,
  ContainerInfo,
  ContainerMount,
  ContainerNetwork,
  ContainerPortBinding,
  DockerStatsInfo,
  DockerVersionInfo,
  HostInfo,
  ImageDetail,
  ImageInfo,
  ImageLayer,
  ImagePruneResult,
  ImageSearchPage,
  ImageSearchResult,
  ImageTag,
  NetworkInfo,
  PullProgressEvent,
  PullProgressLayer,
  PullTaskResponse,
  PullTaskStatus,
  Registry,
  RegistryCreate,
  RegistryUpdate,
  ResourceInfo,
} from "@/api/docker";
