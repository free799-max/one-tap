import type { AppOrchestration } from "@/api/orchestrations";
import {
  BookImage,
  BookOpen,
  Boxes,
  Film,
  Gamepad2,
  Images,
  Music,
  Newspaper,
  RefreshCw,
  type LucideIcon,
} from "lucide-react";

export interface AutomationCategoryMeta {
  label: string;
  icon: LucideIcon;
  description: string;
}

export interface AutomationCategory extends AutomationCategoryMeta {
  key: string;
}

const AUTOMATION_CATEGORY_META: Record<string, AutomationCategoryMeta> = {
  影视: {
    label: "影视",
    icon: Film,
    description:
      "全流程影视自动化：搜索、下载、刮削、播放一体化，打造专属私人影库。",
  },
  漫画: {
    label: "漫画",
    icon: BookImage,
    description:
      "漫画自动化可以帮你抓取、整理、阅读漫画资源，让书架始终保持最新。",
  },
  书籍: {
    label: "书籍",
    icon: BookOpen,
    description:
      "书籍自动化可以追踪新书上架、自动下载并整理书库，随时畅享阅读。",
  },
  音乐: {
    label: "音乐",
    icon: Music,
    description:
      "音乐自动化可以整理曲库、抓取专辑信息并搭建流媒体服务，随时畅听。",
  },
  游戏: {
    label: "游戏",
    icon: Gamepad2,
    description:
      "游戏自动化可以管理游戏资源、存档与相关服务，让游戏库井井有条。",
  },
  图库: {
    label: "图库",
    icon: Images,
    description:
      "图库自动化可以整理照片、生成缩略图并提供展示页面，轻松管理回忆。",
  },
  资讯: {
    label: "资讯",
    icon: Newspaper,
    description:
      "资讯自动化可以聚合 RSS、订阅源与文章，自动归档你关心的信息。",
  },
  刷流: {
    label: "刷流",
    icon: RefreshCw,
    description: "自动管理 PT 刷流任务与独立下载器，维护保种和分享率。",
  },
};

const LEGACY_CATEGORY_KEYS: Record<string, string> = {
  media: "影视",
  comics: "漫画",
  books: "书籍",
  music: "音乐",
  games: "游戏",
  gallery: "图库",
  news: "资讯",
};

export function getAutomationCategoryMeta(
  category?: string
): AutomationCategoryMeta {
  const normalized = category
    ? (LEGACY_CATEGORY_KEYS[category] ?? category)
    : undefined;
  if (normalized && AUTOMATION_CATEGORY_META[normalized]) {
    return AUTOMATION_CATEGORY_META[normalized];
  }
  return {
    label: category || "自动化",
    icon: Boxes,
    description: "该分类的自动化应用组合，支持一键部署与统一管理。",
  };
}

export function buildAutomationCategories(
  orchestrations: Pick<AppOrchestration, "category">[]
): AutomationCategory[] {
  const seen = new Set<string>();
  const categories: AutomationCategory[] = [];
  for (const { category } of orchestrations) {
    if (seen.has(category)) continue;
    seen.add(category);
    categories.push({ key: category, ...getAutomationCategoryMeta(category) });
  }
  return categories;
}
