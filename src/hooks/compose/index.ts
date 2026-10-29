/**
 * Docker Compose 相关 hooks 统一出口
 *
 * 按职责拆分为：
 * - useProjects：项目查询/变更/版本/操作/状态
 * - useLogs：日志查询与 SSE 日志流
 */

export * from "./useProjects";
export * from "./useLogs";

// 保持既有导出，页面侧 import 路径无需变更
export type {
  ComposeVersion,
  ComposeStackStatus,
  ComposeProject,
  ComposeProjectCreate,
  ComposeProjectUpdate,
  ComposeEditRequest,
  ComposeActionRequest,
  ComposeDeployTaskResponse,
} from "@/api/compose";
