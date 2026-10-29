/**
 * Compose 版本管理弹窗
 *
 * 展示版本列表，支持切换到指定历史版本，并与当前运行版本进行 YAML 比对。
 */

import { useState } from "react";
import { Loader2, Undo2, History, Check, GitCompare, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/ui/dialog";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { useComposeProject, useComposeVersions, useRollbackComposeVersion } from "@/hooks/useCompose";
import { diffLines } from "diff";

/** YAML 行差异条目 */
interface DiffLine {
  type: "added" | "removed" | "unchanged";
  text: string;
}

/**
 * 计算两个 YAML 文本的行级差异。
 */
function computeYamlDiff(left: string, right: string): DiffLine[] {
  const changes = diffLines(left, right, { newlineIsToken: true });
  const lines: DiffLine[] = [];
  changes.forEach((change) => {
    const type = change.added ? "added" : change.removed ? "removed" : "unchanged";
    const parts = change.value.split("\n");
    parts.forEach((part, index) => {
      // diffLines 通常把末尾换行符单独作为一个 token，过滤掉空的无意义换行差异
      if (index === parts.length - 1 && part === "") {
        return;
      }
      lines.push({ type, text: part });
    });
  });
  return lines;
}

/**
 * 渲染 YAML 差异面板。
 */
function YamlDiffPanel({
  currentVersionNumber,
  targetVersionNumber,
  currentContent,
  targetContent,
  onClose,
}: {
  currentVersionNumber: number;
  targetVersionNumber: number;
  currentContent: string;
  targetContent: string;
  onClose: () => void;
}) {
  const diffLines = computeYamlDiff(currentContent, targetContent);
  const hasDiff = diffLines.some((line) => line.type !== "unchanged");

  return (
    <div className="rounded-md border mt-4">
      <div className="flex items-center justify-between px-3 py-2 border-b bg-muted/50">
        <div className="text-sm font-medium">
          YAML 比对：<span className="font-mono text-primary">v{currentVersionNumber}</span>
          <span className="text-muted-foreground mx-1.5">→</span>
          <span className="font-mono text-primary">v{targetVersionNumber}</span>
        </div>
        <Button variant="ghost" size="icon-xs" onClick={onClose} title="关闭比对">
          <X className="h-3.5 w-3.5" />
        </Button>
      </div>

      {!hasDiff ? (
        <div className="p-6 text-center text-sm text-muted-foreground">
          两个版本 YAML 内容一致
        </div>
      ) : (
        <div className="max-h-[320px] overflow-auto text-xs font-mono">
          <div className="grid grid-cols-[auto_1fr]">
            {diffLines.map((line, index) => {
              const bgClass =
                line.type === "added"
                  ? "bg-green-50 text-green-800 dark:bg-green-950/30 dark:text-green-400"
                  : line.type === "removed"
                  ? "bg-red-50 text-red-800 dark:bg-red-950/30 dark:text-red-400"
                  : "text-foreground";
              const marker =
                line.type === "added" ? "+" : line.type === "removed" ? "-" : " ";
              return (
                <div key={index} className={`contents ${bgClass}`}>
                  <span className="select-none px-2 py-0.5 text-right text-muted-foreground w-8 shrink-0">
                    {marker}
                  </span>
                  <span className="px-2 py-0.5 whitespace-pre break-all">
                    {line.text || " "}
                  </span>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

export function StackVersionDialog({
  projectId,
  open,
  onOpenChange,
}: {
  projectId: number | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}) {
  const [compareVersionId, setCompareVersionId] = useState<number | null>(null);

  const { data: project } = useComposeProject(projectId);
  const { data: versions } = useComposeVersions(projectId);
  const rollback = useRollbackComposeVersion(projectId ?? 0);

  const currentVersion = (versions || []).find((v) => v.is_current);
  const compareVersion = (versions || []).find((v) => v.id === compareVersionId);

  const handleOpenChange = (open: boolean) => {
    if (!open) {
      setCompareVersionId(null);
    }
    onOpenChange(open);
  };

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-4xl max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <History className="h-4 w-4" />
            版本管理
          </DialogTitle>
          <DialogDescription>
            {project?.project_name} 的历史版本记录，点击“切换”可应用对应版本。
          </DialogDescription>
        </DialogHeader>

        <div className="rounded-md border">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>版本</TableHead>
                <TableHead>说明</TableHead>
                <TableHead>创建时间</TableHead>
                <TableHead className="text-right">操作</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {(versions || []).map((version) => (
                <TableRow
                  key={version.id}
                  className={version.is_current ? "bg-primary/5" : undefined}
                >
                  <TableCell>
                    <div className="flex items-center gap-2">
                      <span className="font-mono">v{version.version_number}</span>
                      {version.is_current && (
                        <span className="text-[10px] text-green-600 flex items-center">
                          <Check className="h-3 w-3 mr-0.5" />
                          当前
                        </span>
                      )}
                    </div>
                  </TableCell>
                  <TableCell className="text-muted-foreground">
                    {version.comment || "-"}
                  </TableCell>
                  <TableCell className="text-muted-foreground text-xs">
                    {new Date(version.created_at).toLocaleString()}
                  </TableCell>
                  <TableCell className="text-right">
                    <div className="inline-flex items-center gap-0.5">
                      {version.is_current ? (
                        <Button
                          variant="ghost"
                          size="icon-sm"
                          disabled
                          className="text-muted-foreground cursor-default"
                          title="当前版本"
                        >
                          <Check className="h-4 w-4" />
                        </Button>
                      ) : (
                        <>
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            className={
                              compareVersionId === version.id
                                ? "text-primary bg-primary/10"
                                : "text-primary hover:text-primary hover:bg-primary/10"
                            }
                            onClick={() => setCompareVersionId(version.id)}
                            title="与当前版本比对 YAML"
                          >
                            <GitCompare className="h-4 w-4" />
                          </Button>
                          <Button
                            variant="ghost"
                            size="icon-sm"
                            className="text-primary hover:text-primary hover:bg-primary/10"
                            onClick={() => rollback.mutate(version.id)}
                            disabled={rollback.isPending}
                            title="切换到此版本"
                          >
                            {rollback.isPending ? (
                              <Loader2 className="h-4 w-4 animate-spin" />
                            ) : (
                              <Undo2 className="h-4 w-4" />
                            )}
                          </Button>
                        </>
                      )}
                    </div>
                  </TableCell>
                </TableRow>
              ))}
              {(!versions || versions.length === 0) && (
                <TableRow>
                  <TableCell
                    colSpan={4}
                    className="text-center text-muted-foreground py-4"
                  >
                    暂无版本
                  </TableCell>
                </TableRow>
              )}
            </TableBody>
          </Table>
        </div>

        {currentVersion && compareVersion && (
          <YamlDiffPanel
            currentVersionNumber={currentVersion.version_number}
            targetVersionNumber={compareVersion.version_number}
            currentContent={currentVersion.content}
            targetContent={compareVersion.content}
            onClose={() => setCompareVersionId(null)}
          />
        )}
      </DialogContent>
    </Dialog>
  );
}
