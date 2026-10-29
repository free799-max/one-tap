/**
 * 存储配置 Tab
 *
 * 单卡片管理存储目录：「宿主机路径 ↔ 容器内路径」的对应关系。
 * 其中可标记一条为「应用数据目录」，用于存放应用配置等持久化小数据，
 * 自动按 应用/服务/挂载点 生成子目录。
 */

import { useState } from "react";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import { ConfirmDialog } from "@/components/ui/confirm-dialog";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  useStorageDirectories,
  useCreateStorageDirectory,
  useUpdateStorageDirectory,
  useDeleteStorageDirectory,
  type StorageDirectory,
} from "@/hooks/useSettings";
import { FolderTree, Plus, Pencil, Trash2 } from "lucide-react";

const INPUT_CLASS =
  "h-8 rounded-md border-input bg-white px-2 text-sm shadow-none";

interface DirFormState {
  name: string;
  host_path: string;
  mount_path: string;
  remark: string;
  enabled: boolean;
  is_app_data: boolean;
}

const EMPTY_DIR_FORM: DirFormState = {
  name: "",
  host_path: "",
  mount_path: "",
  remark: "",
  enabled: true,
  is_app_data: false,
};

/** 存储目录管理：宿主机路径 ↔ 容器内路径 对应关系的增删改查 */
function StorageDirectoriesCard() {
  const { data: directories, isLoading } = useStorageDirectories();
  const createDirectory = useCreateStorageDirectory();
  const updateDirectory = useUpdateStorageDirectory();
  const deleteDirectory = useDeleteStorageDirectory();

  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<StorageDirectory | null>(null);
  const [form, setForm] = useState<DirFormState>(EMPTY_DIR_FORM);
  const [deleting, setDeleting] = useState<StorageDirectory | null>(null);

  const openCreate = () => {
    setEditing(null);
    setForm(EMPTY_DIR_FORM);
    setDialogOpen(true);
  };

  const openEdit = (dir: StorageDirectory) => {
    setEditing(dir);
    setForm({
      name: dir.name,
      host_path: dir.host_path,
      mount_path: dir.mount_path,
      remark: dir.remark ?? "",
      enabled: dir.enabled,
      is_app_data: dir.is_app_data,
    });
    setDialogOpen(true);
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const payload = {
      name: form.name.trim(),
      host_path: form.host_path.trim(),
      mount_path: form.mount_path.trim(),
      remark: form.remark.trim() || null,
      enabled: form.enabled,
      // 保存为 true 时后端会自动清除其他记录的应用数据目录标记
      is_app_data: form.is_app_data,
    };
    if (editing) {
      updateDirectory.mutate(
        { id: editing.id, data: payload },
        { onSuccess: () => setDialogOpen(false) }
      );
    } else {
      createDirectory.mutate(payload, {
        onSuccess: () => setDialogOpen(false),
      });
    }
  };

  const toggleEnabled = (dir: StorageDirectory) => {
    updateDirectory.mutate({ id: dir.id, data: { enabled: !dir.enabled } });
  };

  // 应用数据目录永远置顶，其余按 sort_order、id 排序
  const sorted = [...(directories ?? [])].sort(
    (a, b) =>
      Number(b.is_app_data) - Number(a.is_app_data) ||
      a.sort_order - b.sort_order ||
      a.id - b.id
  );

  return (
    <Card className="rounded-xl">
      <CardHeader className="pb-2">
        <CardTitle className="text-base flex items-center gap-2">
          <FolderTree className="h-4 w-4 text-muted-foreground" />
          存储目录
        </CardTitle>
        <CardDescription className="text-xs">
          「宿主机路径 ↔ 容器内路径」的映射关系；标记为「应用数据目录」的一条用于存放应用配置等小数据，自动按
          应用/服务/挂载点 生成子目录。
        </CardDescription>
        <CardAction>
          <Button size="sm" className="h-8 rounded-md" onClick={openCreate}>
            <Plus className="h-4 w-4 mr-1" />
            新增目录
          </Button>
        </CardAction>
      </CardHeader>
      <CardContent>
        {isLoading ? (
          <div className="text-sm text-muted-foreground">加载中...</div>
        ) : sorted.length === 0 ? (
          <div className="flex flex-col items-center gap-3 rounded-lg border border-dashed py-10">
            <FolderTree className="h-8 w-8 text-muted-foreground/50" />
            <p className="text-sm text-muted-foreground">
              尚未配置存储目录
            </p>
            <Button
              size="sm"
              variant="outline"
              className="h-8 rounded-md"
              onClick={openCreate}
            >
              <Plus className="h-4 w-4 mr-1" />
              新增目录
            </Button>
          </div>
        ) : (
          <div className="divide-y">
            {sorted.map((dir) => (
              <div
                key={dir.id}
                className="flex items-center gap-4 rounded-md px-2 -mx-2 py-3 transition-colors hover:bg-muted/50 first:pt-2 last:pb-2"
              >
                <input
                  type="checkbox"
                  checked={dir.enabled}
                  onChange={() => toggleEnabled(dir)}
                  disabled={updateDirectory.isPending}
                  title={dir.enabled ? "点击禁用" : "点击启用"}
                  className="h-3.5 w-3.5 shrink-0 rounded border-input"
                />
                <div className="min-w-0 flex-1 space-y-0.5">
                  <div className="flex items-center gap-2">
                    <span className="text-sm font-medium">{dir.name}</span>
                    {dir.is_app_data && <Badge>应用数据</Badge>}
                    {dir.visible ? (
                      <Badge className="bg-emerald-500/10 text-emerald-700">
                        可见
                      </Badge>
                    ) : (
                      <Badge className="bg-amber-500/10 text-amber-700">
                        容器内不可见
                      </Badge>
                    )}
                    {!dir.enabled && (
                      <Badge variant="secondary">已禁用</Badge>
                    )}
                  </div>
                  <div className="flex items-center text-xs text-muted-foreground">
                    <span
                      className="font-mono truncate"
                      title={dir.host_path}
                    >
                      {dir.host_path}
                    </span>
                    <span className="mx-1.5 shrink-0">→</span>
                    <span
                      className="font-mono truncate"
                      title={dir.mount_path}
                    >
                      {dir.mount_path}
                    </span>
                  </div>
                  {dir.remark && (
                    <div className="text-xs text-muted-foreground">
                      {dir.remark}
                    </div>
                  )}
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <Button
                    type="button"
                    variant="secondary"
                    size="icon"
                    className="h-8 w-8 rounded-md"
                    onClick={() => openEdit(dir)}
                    title="编辑"
                  >
                    <Pencil className="h-4 w-4" />
                  </Button>
                  <Button
                    type="button"
                    variant="secondary"
                    size="icon"
                    className="h-8 w-8 rounded-md"
                    onClick={() => setDeleting(dir)}
                    title="删除"
                  >
                    <Trash2 className="h-4 w-4" />
                  </Button>
                </div>
              </div>
            ))}
          </div>
        )}
      </CardContent>

      {/* 新增/编辑弹窗 */}
      <Dialog open={dialogOpen} onOpenChange={setDialogOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>
              {editing ? "编辑存储目录" : "新增存储目录"}
            </DialogTitle>
          </DialogHeader>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-1.5">
              <Label htmlFor="sd-name" className="text-xs font-medium">
                名称
              </Label>
              <Input
                id="sd-name"
                value={form.name}
                onChange={(e) =>
                  setForm((prev) => ({ ...prev, name: e.target.value }))
                }
                placeholder="如 影视库"
                required
                className={INPUT_CLASS}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="sd-host-path" className="text-xs font-medium">
                宿主机路径
              </Label>
              <Input
                id="sd-host-path"
                value={form.host_path}
                onChange={(e) =>
                  setForm((prev) => ({ ...prev, host_path: e.target.value }))
                }
                placeholder="如 /mnt/data/media"
                required
                className={INPUT_CLASS}
              />
              <p className="text-xs text-muted-foreground">
                docker-compose 挂载的来源路径。
              </p>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="sd-mount-path" className="text-xs font-medium">
                容器内路径
              </Label>
              <Input
                id="sd-mount-path"
                value={form.mount_path}
                onChange={(e) =>
                  setForm((prev) => ({ ...prev, mount_path: e.target.value }))
                }
                placeholder="如 /media"
                required
                className={INPUT_CLASS}
              />
              <p className="text-xs text-muted-foreground">
                应用容器内看到的路径，需已挂载进 backend 容器。
              </p>
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="sd-remark" className="text-xs font-medium">
                备注
              </Label>
              <Input
                id="sd-remark"
                value={form.remark}
                onChange={(e) =>
                  setForm((prev) => ({ ...prev, remark: e.target.value }))
                }
                placeholder="可选"
                className={INPUT_CLASS}
              />
            </div>
            <div className="space-y-1.5">
              <div className="flex items-center gap-6">
                <div className="flex items-center gap-2">
                  <input
                    id="sd-enabled"
                    type="checkbox"
                    checked={form.enabled}
                    onChange={(e) =>
                      setForm((prev) => ({ ...prev, enabled: e.target.checked }))
                    }
                    className="h-3.5 w-3.5 rounded border-input"
                  />
                  <Label htmlFor="sd-enabled" className="text-[13px] font-medium">
                    启用（可在部署表单中选择）
                  </Label>
                </div>
                <div className="flex items-center gap-2">
                  <input
                    id="sd-is-app-data"
                    type="checkbox"
                    checked={form.is_app_data}
                    onChange={(e) =>
                      setForm((prev) => ({
                        ...prev,
                        is_app_data: e.target.checked,
                      }))
                    }
                    className="h-3.5 w-3.5 rounded border-input"
                  />
                  <Label
                    htmlFor="sd-is-app-data"
                    className="text-[13px] font-medium"
                  >
                    设为应用数据目录
                  </Label>
                </div>
              </div>
              <p className="text-xs text-muted-foreground">
                应用数据目录仅可有一个，保存后原应用数据目录自动转为普通存储目录。
              </p>
            </div>
            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                size="sm"
                onClick={() => setDialogOpen(false)}
              >
                取消
              </Button>
              <Button
                type="submit"
                size="sm"
                disabled={
                  createDirectory.isPending || updateDirectory.isPending
                }
              >
                {createDirectory.isPending || updateDirectory.isPending
                  ? "保存中..."
                  : "保存"}
              </Button>
            </DialogFooter>
          </form>
        </DialogContent>
      </Dialog>

      {/* 删除确认 */}
      <ConfirmDialog
        open={!!deleting}
        onOpenChange={(open) => {
          if (!open) setDeleting(null);
        }}
        title="删除存储目录"
        description={
          deleting
            ? `确定删除存储目录「${deleting.name}」（${deleting.mount_path}）吗？已部署应用中引用该路径的配置不受影响，但部署表单中将无法再选择该目录。`
            : undefined
        }
        confirmText="删除"
        destructive
        isPending={deleteDirectory.isPending}
        onConfirm={() => {
          if (!deleting) return;
          deleteDirectory.mutate(deleting.id, {
            onSuccess: () => setDeleting(null),
          });
        }}
      />
    </Card>
  );
}

export function StorageSettingsTab() {
  return <StorageDirectoriesCard />;
}
