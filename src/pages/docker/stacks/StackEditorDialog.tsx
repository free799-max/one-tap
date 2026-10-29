/**
 * Compose 项目统一编辑器弹窗
 *
 * 同时支持创建和编辑两种模式：
 * - 创建模式：输入项目名、描述，YAML 留空由用户填写，创建后立即部署。
 * - 编辑模式：项目名只读，拉取项目详情回填真实 YAML 与描述，保存后自动生成新版本并部署。
 *
 * 布局采用上下分屏：上方为基础信息，下方为 YAML 编辑器，无需 Tab 切换。
 */

import { useState } from "react";
import { Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
  useCreateComposeProject,
  useEditComposeProject,
  useComposeProject,
} from "@/hooks/useCompose";
import type { ComposeProject } from "@/hooks/useCompose";
import CodeMirror from "@uiw/react-codemirror";
import { yaml } from "@codemirror/lang-yaml";

interface StackEditorDialogProps {
  mode: "create" | "edit";
  project: ComposeProject | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onTaskCreated?: (taskId: string) => void;
}

export function StackEditorDialog({
  mode,
  project,
  open,
  onOpenChange,
  onTaskCreated,
}: StackEditorDialogProps) {
  // 编辑模式下拉取完整项目详情，确保拿到真实 YAML 内容
  const { data: detail, isLoading: isDetailLoading } = useComposeProject(
    mode === "edit" ? project?.id ?? null : null
  );

  // 使用 undefined 作为“未手动输入”的标记，优先显示详情数据，
  // 用户输入后显示用户输入值，避免在 effect 中调用 setState。
  const [projectNameInput, setProjectNameInput] = useState<string | undefined>(undefined);
  const [descriptionInput, setDescriptionInput] = useState<string | undefined>(undefined);
  const [contentInput, setContentInput] = useState<string | undefined>(undefined);

  const projectName =
    mode === "edit"
      ? (projectNameInput ?? detail?.project_name ?? project?.project_name ?? "")
      : (projectNameInput ?? "");
  const description =
    mode === "edit"
      ? (descriptionInput ?? detail?.description ?? project?.description ?? "")
      : (descriptionInput ?? "");
  const content =
    mode === "edit"
      ? (contentInput ?? detail?.current_version?.content ?? "")
      : (contentInput ?? "");

  const create = useCreateComposeProject();
  const edit = useEditComposeProject(project?.id ?? 0);
  const isPending = mode === "create" ? create.isPending : edit.isPending;

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();

    if (mode === "create") {
      create.mutate(
        {
          project_name: projectName,
          description: description || null,
          content,
        },
        {
          onSuccess: (response) => {
            onTaskCreated?.(response.task_id);
            onOpenChange(false);
          },
        }
      );
    } else {
      if (!project) return;
      const updateDescription =
        description !== (project.description || "") ? description || null : undefined;

      edit.mutate(
        {
          content,
          description: updateDescription,
        },
        {
          onSuccess: (response) => {
            onTaskCreated?.(response.task_id);
            onOpenChange(false);
          },
        }
      );
    }
  };

  const title = mode === "create" ? "创建编排项目" : `编辑 ${project?.project_name}`;
  const submitLabel = mode === "create" ? "创建并部署" : "保存并部署";
  const isLoading = isPending || (mode === "edit" && isDetailLoading);

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      key={`${mode}-${project?.id ?? "new"}-${open}`}
    >
      <DialogContent className="sm:max-w-4xl max-h-[85vh] overflow-hidden flex flex-col p-0">
        <form
          onSubmit={handleSubmit}
          className="flex flex-col flex-1 overflow-hidden"
        >
          <DialogHeader className="px-6 pt-5 pb-2 border-b shrink-0">
            <DialogTitle>{title}</DialogTitle>
          </DialogHeader>

          <div className="flex-1 overflow-y-auto px-6 py-4">
            <div className="flex flex-col h-full gap-4">
              {/* 基础信息 */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 shrink-0">
                <div className="space-y-3">
                  <Label htmlFor="projectName">项目名</Label>
                  <Input
                    id="projectName"
                    value={projectName}
                    onChange={(e) => setProjectNameInput(e.target.value)}
                    placeholder="如：nginx-stack"
                    required
                    readOnly={mode === "edit"}
                    className={mode === "edit" ? "bg-muted" : ""}
                    disabled={isDetailLoading}
                  />
                  {mode === "edit" && (
                    <p className="text-xs text-muted-foreground">
                      项目名不可修改，如需重命名请导出后新建项目。
                    </p>
                  )}
                </div>

                <div className="space-y-3">
                  <Label htmlFor="description">描述</Label>
                  <Input
                    id="description"
                    value={description}
                    onChange={(e) => setDescriptionInput(e.target.value)}
                    placeholder="项目用途描述（可选）"
                    disabled={isDetailLoading}
                  />
                </div>
              </div>

              {/* YAML 编辑器 */}
              <div className="flex flex-col flex-1 min-h-[360px]">
                <Label htmlFor="content" className="mb-3">
                  docker-compose.yml
                </Label>
                <div className="flex-1 border rounded-md overflow-hidden relative">
                  <CodeMirror
                    value={content}
                    height="100%"
                    minHeight="360px"
                    extensions={[yaml()]}
                    onChange={(value) => setContentInput(value)}
                    className="h-full text-sm"
                    basicSetup={{
                      lineNumbers: true,
                      highlightActiveLineGutter: true,
                      highlightActiveLine: true,
                      foldGutter: false,
                    }}
                    editable={!isDetailLoading}
                  />
                  {isDetailLoading && (
                    <div className="absolute inset-0 flex items-center justify-center bg-background/80">
                      <Loader2 className="h-6 w-6 animate-spin text-muted-foreground" />
                    </div>
                  )}
                </div>
              </div>
            </div>
          </div>

          <DialogFooter className="m-0 px-6 py-3 border-t shrink-0 items-center justify-center sm:flex-row">
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
              disabled={isPending}
            >
              取消
            </Button>
            <Button
              type="submit"
              disabled={
                isLoading ||
                (mode === "create" && !projectName.trim()) ||
                !content.trim()
              }
            >
              {isLoading && (
                <Loader2 className="h-4 w-4 mr-2 animate-spin" />
              )}
              {submitLabel}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  );
}
