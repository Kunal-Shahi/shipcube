"use client";

import { Check, Copy } from "lucide-react";
import { useState } from "react";
import { cn } from "@/lib/cn";

export function CopyButton({ text, label = "Copy", className }: { text: string; label?: string; className?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      type="button"
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        } catch {
          setCopied(false);
        }
      }}
      className={cn(
        "inline-flex h-6 items-center gap-1 rounded px-1.5 text-[12px] text-fg-muted hover:bg-surface hover:text-fg",
        className,
      )}
      aria-label={copied ? "Copied" : label}
    >
      {copied ? <Check className="size-3.5 text-good" aria-hidden /> : <Copy className="size-3.5" aria-hidden />}
      {copied ? "Copied" : label}
    </button>
  );
}

export function CodeBlock({ code, label, lang }: { code: string; label?: string; lang?: string }) {
  return (
    <div className="overflow-hidden rounded-md border border-border bg-surface-2">
      <div className="flex items-center justify-between border-b border-border px-3 py-1">
        <span className="font-mono text-[11.5px] text-fg-subtle">{label ?? lang}</span>
        <CopyButton text={code} />
      </div>
      <pre className="overflow-x-auto px-3 py-2.5 font-mono text-[12.5px] leading-5">
        <code>{code}</code>
      </pre>
    </div>
  );
}
