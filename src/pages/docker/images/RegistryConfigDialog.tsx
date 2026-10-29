/**
 * Registry 配置列表弹窗组件
 */

import { useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  useRegistries,
  useDeleteRegistry,
  useSetDefaultRegistry,
  type Registry,
} from "@/hooks/useDocker";
import { RefreshCw, Check } from "lucide-react";

interface RegistryConfigDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onOpenEdit: (registry: Registry) => void;
  onOpenCreate: () => void;
}

export function RegistryConfigDialog({
  open,
  onOpenChange,
  onOpenEdit,
  onOpenCreate,
}: RegistryConfigDialogProps) {
  const { data: registries = [], refetch, isLoading } = useRegistries();
  const deleteRegistry = useDeleteRegistry();
  const setDefaultRegistry = useSetDefaultRegistry();
  const [selectedId, setSelectedId] = useState<number | null>(null);

  const selectedRegistry = registries.find((r) => r.id === selectedId);

  const handleOpenChange = (newOpen: boolean) => {
    if (!newOpen) setSelectedId(null);
    onOpenChange(newOpen);
  };

  const handleSetDefault = () => {
    if (selectedId && !selectedRegistry?.is_default) {
      setDefaultRegistry.mutate(selectedId);
    }
  };

  const handleDeleteRegistry = () => {
    if (selectedId) {
      if (confirm("确定删除此配置吗？")) {
        deleteRegistry.mutate(selectedId);
        setSelectedId(null);
      }
    }
  };

  const handleEdit = () => {
    if (selectedRegistry) {
      onOpenEdit(selectedRegistry);
    }
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent
        className="w-[680px] p-0 gap-0 rounded shadow-lg border border-gray-300"
        style={{ maxWidth: 680 }}
      >
        {/* 标题栏 */}
        <DialogHeader className="px-5 pt-5 pb-3.5 border-b border-gray-200">
          <DialogTitle className="text-base font-bold text-gray-900">
            镜像仓库设置
          </DialogTitle>
        </DialogHeader>

        <div className="px-5 py-4">
          {/* 按钮栏 */}
          <div className="flex items-center gap-2 mb-3">
            <ActionBtn onClick={onOpenCreate}>新增</ActionBtn>
            <ActionBtn onClick={handleEdit} disabled={!selectedRegistry}>
              编辑
            </ActionBtn>
            <ActionBtn
              onClick={handleDeleteRegistry}
              disabled={!selectedRegistry || selectedRegistry.is_default}
            >
              删除
            </ActionBtn>
            <ActionBtn
              onClick={handleSetDefault}
              disabled={
                !selectedRegistry ||
                selectedRegistry.is_default ||
                setDefaultRegistry.isPending
              }
            >
              使用
            </ActionBtn>
          </div>

          {/* 表格 */}
          <div className="border border-gray-300 rounded-sm overflow-hidden">
            {/* 表头 */}
            <div className="flex bg-gray-100 border-b border-gray-300">
              <div className="flex-1 px-4 py-2 text-sm text-gray-700 font-semibold">
                存储库
              </div>
              <div className="flex-1 px-4 py-2 text-sm text-gray-700 font-semibold">
                地址
              </div>
              <div className="w-24 px-4 py-2 text-sm text-gray-700 font-semibold text-center">
                使用中
              </div>
            </div>

            {/* 表体 */}
            <div className="min-h-[180px] max-h-[320px] overflow-auto">
              {isLoading ? (
                <div className="flex items-center justify-center h-[180px] text-sm text-gray-500">
                  加载中...
                </div>
              ) : registries.length === 0 ? (
                <div className="flex items-center justify-center h-[180px] text-sm text-gray-500">
                  暂无配置
                </div>
              ) : (
                registries.map((registry) => (
                  <div
                    key={registry.id}
                    className={`flex border-b border-gray-200 last:border-b-0 cursor-pointer ${
                      selectedId === registry.id
                        ? "bg-blue-50"
                        : "bg-white hover:bg-gray-50"
                    }`}
                    onClick={() => setSelectedId(registry.id)}
                    onDoubleClick={() => onOpenEdit(registry)}
                  >
                    <div className="flex-1 px-4 py-2 text-sm text-gray-900 truncate font-medium">
                      {registry.name}
                    </div>
                    <div className="flex-1 px-4 py-2 text-sm text-gray-600 truncate">
                      {registry.search_api_url}
                    </div>
                    <div className="w-24 px-4 py-2 flex items-center justify-center">
                      {registry.is_default ? (
                        <Check
                          className="h-5 w-5 text-blue-600"
                          strokeWidth={2.5}
                        />
                      ) : null}
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>

          {/* 底部状态栏 */}
          <div className="flex items-center justify-between mt-3">
            <div className="flex items-center gap-3">
              <span className="text-xs text-gray-500">
                {registries.length} 个项目
              </span>
              <button
                className="text-gray-400 hover:text-gray-600 transition-colors"
                onClick={() => refetch()}
                title="刷新"
              >
                <RefreshCw className="h-4 w-4" />
              </button>
            </div>
          </div>
        </div>

        {/* 底部按钮 */}
        <div className="flex justify-end px-5 py-3.5 border-t border-gray-200 bg-gray-50">
          <ActionBtn onClick={() => onOpenChange(false)}>关闭</ActionBtn>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function ActionBtn({
  children,
  onClick,
  disabled,
}: {
  children: React.ReactNode;
  onClick: () => void;
  disabled?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      className={`px-3.5 py-1.5 text-sm rounded-sm border transition-colors ${
        disabled
          ? "border-gray-200 bg-gray-100 text-gray-400 cursor-not-allowed"
          : "border-gray-300 bg-gray-100 text-gray-800 hover:bg-gray-200 hover:border-gray-400"
      }`}
    >
      {children}
    </button>
  );
}
