/**
 * 应用分类展示辅助
 *
 * 分类的显示名与排序统一由管理端维护（经后端 /api/apps/categories 下发），
 * 前端不再内置本地映射；接口不可用时回退为显示原始分类值。
 */

import type { CategoryOption } from "@/api/apps";

export function categoryLabel(
  options: CategoryOption[],
  category: string
): string {
  return options.find((option) => option.value === category)?.label ?? category;
}

export function categoryRank(
  options: CategoryOption[],
  category: string
): number {
  const index = options.findIndex((option) => option.value === category);
  return index === -1 ? options.length : index;
}
