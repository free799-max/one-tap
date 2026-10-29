/**
 * 应用组合的展示元信息
 *
 * 统一编排流水线与实例应用卡片的阶段/关系文案。
 */

import type { AppCompositionItem } from "@/hooks/useOrchestrations";

/** 应用组合关系的中文标签 */
export const RELATION_LABELS: Record<AppCompositionItem["relation"], string> = {
  required: "必选",
  suggested: "推荐",
  optional: "可选",
  conflicting: "互斥",
};

/** 应用在组合中的角色文案，如「必选」「播放器」 */
export function getAppRoleLabel(
  item?: AppCompositionItem | null
): string | null {
  if (!item) return null;
  if (item.group) return item.group;
  return RELATION_LABELS[item.relation] ?? null;
}
