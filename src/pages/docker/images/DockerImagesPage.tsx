/**
 * Docker 镜像管理页面（重构版）
 *
 * 上下双区域布局：
 * - 上区域：镜像搜索配置 + 远程镜像搜索/拉取
 * - 下区域：本地镜像管理，支持多选批量删除
 */

import { useState } from "react";
import { useRegistries, type Registry } from "@/hooks/useDocker";
import { ImageSearchSection } from "./ImageSearchSection";
import { LocalImagesSection } from "./LocalImagesSection";
import { RegistryConfigDialog } from "./RegistryConfigDialog";
import { RegistryEditDialog } from "./RegistryEditDialog";

export function DockerImagesPage() {
  /* ---------- Dialog 开关 ---------- */
  const [showConfigDialog, setShowConfigDialog] = useState(false);
  const [showEditDialog, setShowEditDialog] = useState(false);
  const [editingRegistry, setEditingRegistry] = useState<Registry | null>(null);

  const { data: registries = [] } = useRegistries();
  const defaultRegistry = registries.find((r) => r.is_default);

  const openCreateDialog = () => {
    setEditingRegistry(null);
    setShowEditDialog(true);
  };

  const openEditDialog = (registry: Registry) => {
    setEditingRegistry(registry);
    setShowEditDialog(true);
  };

  return (
    <div className="space-y-6">
      {/* 上区域：搜索（进度条已集成到搜索区域右侧） */}
      <ImageSearchSection
        defaultRegistry={defaultRegistry}
        onOpenConfig={() => setShowConfigDialog(true)}
      />

      {/* 下区域：本地镜像 */}
      <LocalImagesSection />

      {/* Registry 配置列表弹窗 */}
      <RegistryConfigDialog
        open={showConfigDialog}
        onOpenChange={setShowConfigDialog}
        onOpenEdit={openEditDialog}
        onOpenCreate={openCreateDialog}
      />

      {/* 新增/编辑 Registry 弹窗 */}
      <RegistryEditDialog
        open={showEditDialog}
        onOpenChange={setShowEditDialog}
        editingRegistry={editingRegistry}
      />
    </div>
  );
}
