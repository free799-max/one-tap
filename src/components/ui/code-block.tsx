/**
 * 只读代码块组件
 *
 * 用于展示 YAML、JSON 等文本内容，支持滚动与复制。
 */

import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";
import { yaml } from "@codemirror/lang-yaml";
import CodeMirror from "@uiw/react-codemirror";
import { Check, Copy } from "lucide-react";
import { useState } from "react";

interface CodeBlockProps {
  code: string;
  className?: string;
  showCopy?: boolean;
  emptyText?: string;
  language?: string;
}

export function CodeBlock({
  code,
  className,
  showCopy = true,
  emptyText = "暂无内容",
  language,
}: CodeBlockProps) {
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState(false);

  const handleCopy = async () => {
    if (!code) return;
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setCopyError(false);
      setTimeout(() => setCopied(false), 1500);
    } catch {
      setCopyError(true);
      setTimeout(() => setCopyError(false), 2000);
    }
  };

  const isEmpty = !code;

  return (
    <div
      className={cn(
        "relative flex flex-col overflow-hidden rounded-lg border bg-muted/50",
        className
      )}
    >
      {showCopy && (
        <div className="flex items-center justify-end border-b bg-muted px-2 py-1">
          <Button
            variant="ghost"
            size="sm"
            className="h-7 gap-1 px-2 text-xs"
            onClick={handleCopy}
            disabled={isEmpty}
          >
            {copyError ? (
              <>
                <Copy className="h-3.5 w-3.5" />
                复制失败
              </>
            ) : copied ? (
              <>
                <Check className="h-3.5 w-3.5" />
                已复制
              </>
            ) : (
              <>
                <Copy className="h-3.5 w-3.5" />
                复制
              </>
            )}
          </Button>
        </div>
      )}
      <div
        className={cn(
          "relative",
          language === "yaml" ? "overflow-visible" : "flex-1 overflow-auto"
        )}
      >
        {isEmpty ? (
          <div className="flex h-full items-center justify-center p-4 text-sm text-muted-foreground">
            {emptyText}
          </div>
        ) : language === "yaml" ? (
          <div
            className="[&_.cm-editor]:!bg-transparent [&_.cm-gutters]:!bg-muted/50 [&_.cm-gutters]:!border-r-border [&_.cm-content]:!py-3 [&_.cm-line]:!px-1"
          >
            <CodeMirror
              value={code}
              height="auto"
              extensions={[yaml()]}
              editable={false}
              readOnly={true}
              theme="light"
              basicSetup={{
                lineNumbers: true,
                highlightActiveLineGutter: false,
                highlightActiveLine: false,
                foldGutter: false,
              }}
              onChange={() => {}}
            />
          </div>
        ) : (
          <pre className="min-w-full p-4 text-xs leading-relaxed">
            <code className="font-mono whitespace-pre text-foreground">{code}</code>
          </pre>
        )}
      </div>
    </div>
  );
}
