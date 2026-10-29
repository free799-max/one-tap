/**
 * Registry 新增/编辑弹窗组件
 */

import { useState } from "react";
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  useCreateRegistry,
  useUpdateRegistry,
  type Registry,
  type RegistryCreate,
} from "@/hooks/useDocker";
import { useToast } from "@/components/ui/toast";
import { PasswordInput } from "@/components/ui/password-input";
import { Plus, Minus, Loader2 } from "lucide-react";

interface RegistryEditDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  editingRegistry: Registry | null;
}

const emptyForm = {
  name: "",
  search_api_url: "",
  enable_mirror: false,
  mirror_urls: [] as string[],
  trust_ssl_self_signed: false,
  enable_auth: false,
  username: "",
  password: "",
};

function getInitialForm(registry: Registry | null) {
  if (!registry) return emptyForm;
  return {
    name: registry.name,
    search_api_url: registry.search_api_url,
    enable_mirror: registry.enable_mirror,
    mirror_urls: registry.mirror_urls?.length
      ? registry.mirror_urls
      : registry.mirror_url
        ? [registry.mirror_url]
        : [],
    trust_ssl_self_signed: registry.trust_ssl_self_signed,
    enable_auth: !!registry.username,
    username: registry.username || "",
    password: "",
  };
}

export function RegistryEditDialog({
  open,
  onOpenChange,
  editingRegistry,
}: RegistryEditDialogProps) {
  const toast = useToast();
  const createRegistry = useCreateRegistry();
  const updateRegistry = useUpdateRegistry();

  const [form, setForm] = useState(() => getInitialForm(editingRegistry));

  const handleOpenChange = (newOpen: boolean) => {
    if (newOpen) {
      setForm(getInitialForm(editingRegistry));
    } else {
      setForm(emptyForm);
    }
    onOpenChange(newOpen);
  };

  const handleSave = () => {
    if (!form.name.trim() || !form.search_api_url.trim()) {
      toast.error("镜像仓库名称和 URL 不能为空");
      return;
    }

    const payload: RegistryCreate = {
      name: form.name.trim(),
      search_api_url: form.search_api_url.trim(),
      mirror_urls:
        form.enable_mirror && form.mirror_urls.length > 0
          ? form.mirror_urls.filter((u) => u.trim()).map((u) => u.trim())
          : null,
      mirror_url:
        form.enable_mirror && form.mirror_urls.length > 0
          ? form.mirror_urls.filter((u) => u.trim())[0]?.trim() || null
          : null,
      enable_mirror: form.enable_mirror,
      trust_ssl_self_signed: form.trust_ssl_self_signed,
      username: form.enable_auth && form.username.trim() ? form.username.trim() : null,
      password: form.enable_auth && form.password.trim() ? form.password.trim() : null,
    };

    if (editingRegistry) {
      if (!form.enable_auth) {
        payload.username = null;
        payload.password = null;
      } else if (!form.password.trim() && editingRegistry.username) {
        payload.password = undefined;
      }
      updateRegistry.mutate(
        { id: editingRegistry.id, data: payload },
        { onSuccess: () => onOpenChange(false) }
      );
    } else {
      createRegistry.mutate(payload, {
        onSuccess: () => onOpenChange(false),
      });
    }
  };

  const addMirrorUrl = () => {
    setForm((prev) => ({ ...prev, mirror_urls: [...prev.mirror_urls, ""] }));
  };

  const updateMirrorUrl = (index: number, value: string) => {
    setForm((prev) => {
      const urls = [...prev.mirror_urls];
      urls[index] = value;
      return { ...prev, mirror_urls: urls };
    });
  };

  const removeMirrorUrl = (index: number) => {
    setForm((prev) => ({
      ...prev,
      mirror_urls: prev.mirror_urls.filter((_, i) => i !== index),
    }));
  };

  const isPending = createRegistry.isPending || updateRegistry.isPending;
  const isEdit = !!editingRegistry;

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent
        className="w-[560px] p-0 gap-0 rounded shadow-lg border border-gray-300"
        style={{ maxWidth: 560 }}
      >
        <DialogHeader className="px-5 pt-5 pb-3.5 border-b border-gray-200">
          <DialogTitle className="text-base font-bold text-gray-900">
            {isEdit ? "编辑镜像仓库" : "新增镜像仓库"}
          </DialogTitle>
        </DialogHeader>

        <div className="px-5 py-4 max-h-[540px] overflow-auto">
          {/* 站点信息 */}
          <SectionTitle>站点信息</SectionTitle>

          <div className="space-y-3">
            <FormRow label="镜像仓库名称：">
              <input
                type="text"
                value={form.name}
                onChange={(e) =>
                  setForm((prev) => ({ ...prev, name: e.target.value }))
                }
                className="w-full h-8 px-3 text-sm border border-gray-300 rounded-sm bg-white text-gray-900 outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-200 transition-all"
                placeholder="如 Docker Hub"
              />
            </FormRow>

            <FormRow label="镜像仓库 URL：">
              <input
                type="text"
                value={form.search_api_url}
                onChange={(e) =>
                  setForm((prev) => ({ ...prev, search_api_url: e.target.value }))
                }
                className="w-full h-8 px-3 text-sm border border-gray-300 rounded-sm bg-white text-gray-900 outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-200 transition-all"
                placeholder="如 https://registry.hub.docker.com"
              />
            </FormRow>

            <FormRow label="">
              <label className="inline-flex items-center gap-2 cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={form.enable_mirror}
                  onChange={(e) =>
                    setForm((prev) => ({ ...prev, enable_mirror: e.target.checked }))
                  }
                  className="h-4 w-4 rounded border-gray-300 text-blue-600 accent-blue-600"
                />
                <span className="text-sm text-gray-800">启用镜像仓库镜像</span>
              </label>
            </FormRow>

            {form.enable_mirror && (
              <div className="flex items-start">
                <label className="w-[140px] text-sm text-gray-800 shrink-0 pt-1.5 leading-5">
                  镜像仓库镜像 URL：
                </label>
                <div className="flex-1 min-w-0 space-y-1.5">
                  {form.mirror_urls.map((url, index) => (
                    <div key={index} className="flex items-center gap-1.5">
                      <input
                        type="text"
                        value={url}
                        onChange={(e) => updateMirrorUrl(index, e.target.value)}
                        className="flex-1 h-8 px-2.5 text-sm border border-gray-300 rounded-sm bg-white text-gray-900 outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-200 transition-all"
                        placeholder="https://..."
                      />
                      <button
                        type="button"
                        onClick={() => removeMirrorUrl(index)}
                        className="h-8 w-8 flex items-center justify-center rounded-sm text-gray-400 hover:text-red-500 hover:bg-red-50 transition-colors shrink-0"
                        title="删除"
                      >
                        <Minus className="h-4 w-4" />
                      </button>
                    </div>
                  ))}
                  <button
                    type="button"
                    onClick={addMirrorUrl}
                    className="inline-flex items-center gap-1 px-2.5 py-1 text-sm text-gray-600 hover:text-gray-900 transition-colors"
                  >
                    <Plus className="h-4 w-4" />
                    新增
                  </button>
                </div>
              </div>
            )}

            <FormRow label="">
              <label className="inline-flex items-center gap-2 cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={form.trust_ssl_self_signed}
                  onChange={(e) =>
                    setForm((prev) => ({
                      ...prev,
                      trust_ssl_self_signed: e.target.checked,
                    }))
                  }
                  className="h-4 w-4 rounded border-gray-300 text-blue-600 accent-blue-600"
                />
                <span className="text-sm text-gray-800">
                  信任的 SSL 自我签署证书
                </span>
              </label>
            </FormRow>
          </div>

          {/* 登录信息 */}
          <SectionTitle>登录信息（可选）</SectionTitle>

          <div className="space-y-3">
            <FormRow label="">
              <label className="inline-flex items-center gap-2 cursor-pointer select-none">
                <input
                  type="checkbox"
                  checked={form.enable_auth}
                  onChange={(e) =>
                    setForm((prev) => ({ ...prev, enable_auth: e.target.checked }))
                  }
                  className="h-4 w-4 rounded border-gray-300 text-blue-600 accent-blue-600"
                />
                <span className="text-sm text-gray-800">启用认证</span>
              </label>
            </FormRow>

            {form.enable_auth && (
              <>
                <FormRow label="用户名：">
                  <input
                    type="text"
                    value={form.username}
                    onChange={(e) =>
                      setForm((prev) => ({ ...prev, username: e.target.value }))
                    }
                    className="w-full h-8 px-3 text-sm border border-gray-300 rounded-sm bg-white text-gray-900 outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-200 transition-all"
                    placeholder="认证用户名"
                  />
                </FormRow>

                <FormRow label="密码：">
                  <PasswordInput
                    value={form.password}
                    onChange={(e) =>
                      setForm((prev) => ({ ...prev, password: e.target.value }))
                    }
                    className="w-full h-8 px-3 text-sm border border-gray-300 rounded-sm bg-white text-gray-900 outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-200 transition-all"
                    placeholder={isEdit ? "留空则不修改" : "认证密码"}
                  />
                </FormRow>
              </>
            )}
          </div>
        </div>

        {/* 底部按钮 */}
        <div className="flex justify-end items-center gap-2.5 px-5 py-3.5 border-t border-gray-200 bg-gray-50">
          <button
            type="button"
            onClick={() => onOpenChange(false)}
            className="h-8 px-6 text-sm rounded-sm border border-gray-300 bg-white text-gray-800 hover:bg-gray-50 hover:border-gray-400 transition-colors"
          >
            取消
          </button>
          <button
            type="button"
            onClick={handleSave}
            disabled={isPending}
            className="h-8 px-6 text-sm rounded-sm bg-blue-600 text-white hover:bg-blue-500 disabled:bg-gray-300 disabled:text-gray-500 disabled:cursor-not-allowed transition-colors"
          >
            {isPending ? (
              <span className="inline-flex items-center gap-1.5">
                <Loader2 className="h-4 w-4 animate-spin" />
                保存中
              </span>
            ) : (
              "应用"
            )}
          </button>
        </div>
      </DialogContent>
    </Dialog>
  );
}

function SectionTitle({ children }: { children: React.ReactNode }) {
  return (
    <div className="text-sm text-gray-800 font-medium mt-5 mb-3 pb-2 border-b border-dashed border-gray-300">
      {children}
    </div>
  );
}

function FormRow({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}) {
  return (
    <div className="flex items-start">
      <label className="w-[140px] text-sm text-gray-800 shrink-0 pt-1.5 leading-5">
        {label}
      </label>
      <div className="flex-1 min-w-0">{children}</div>
    </div>
  );
}
