"use client";

import { Search } from "lucide-react";
import { useMemo, useState } from "react";
import { EmptyState, Input, Select } from "@/components/ui";
import { cn } from "@/lib/cn";
import { time } from "@/lib/format";
import type { LogLine } from "@/lib/types";

const LEVEL_CLS = { info: "text-fg-subtle", warn: "text-warning", error: "text-critical" } as const;

export function LogViewer({ logs, streaming }: { logs: LogLine[]; streaming: boolean }) {
  const [level, setLevel] = useState("all");
  const [source, setSource] = useState("all");
  const [q, setQ] = useState("");

  const shown = useMemo(
    () =>
      logs
        .filter((l) => level === "all" || (level === "warn" ? l.level !== "info" : l.level === level))
        .filter((l) => source === "all" || l.source === source)
        .filter((l) => !q || l.message.toLowerCase().includes(q.toLowerCase()))
        .slice()
        .reverse(),
    [logs, level, source, q],
  );

  return (
    <div>
      <div className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-2.5">
        <div className="relative min-w-[200px] flex-1">
          <Search className="pointer-events-none absolute top-1/2 left-2.5 size-3.5 -translate-y-1/2 text-fg-subtle" aria-hidden />
          <label htmlFor="log-q" className="sr-only">Search logs</label>
          <Input id="log-q" placeholder="Search messages" value={q} onChange={(e) => setQ(e.target.value)} className="pl-8" />
        </div>
        <label htmlFor="log-level" className="sr-only">Level</label>
        <Select id="log-level" value={level} onChange={(e) => setLevel(e.target.value)} className="w-[140px]">
          <option value="all">All levels</option>
          <option value="warn">Warnings and errors</option>
          <option value="error">Errors only</option>
        </Select>
        <label htmlFor="log-source" className="sr-only">Source</label>
        <Select id="log-source" value={source} onChange={(e) => setSource(e.target.value)} className="w-[130px]">
          <option value="all">All sources</option>
          <option value="build">Build</option>
          <option value="deploy">Deploy</option>
          <option value="runtime">Runtime</option>
          <option value="gateway">Gateway</option>
        </Select>
        {streaming && (
          <span className="ml-auto flex items-center gap-1.5 text-[12px] text-fg-muted">
            <span className="size-1.5 animate-pulse rounded-full bg-accent" aria-hidden /> Streaming
          </span>
        )}
      </div>
      {logs.length === 0 ? (
        <EmptyState title="No logs yet" body="Build, deploy and request logs appear here as soon as your agent deploys the app." />
      ) : shown.length === 0 ? (
        <EmptyState title="No matching lines" body="Nothing matches these filters. Clear the search or choose All levels." />
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[720px] font-mono text-[12px] leading-5">
            <tbody>
              {shown.map((l) => (
                <tr key={l.id} className={cn("border-b border-border last:border-0 align-top", l.level === "error" && "bg-critical-soft/60")}>
                  <td className="num w-[150px] px-4 py-1.5 whitespace-nowrap text-fg-subtle">{time(l.ts)}</td>
                  <td className={cn("w-[52px] py-1.5 uppercase", LEVEL_CLS[l.level])}>{l.level}</td>
                  <td className="w-[72px] py-1.5 text-fg-muted">{l.source}</td>
                  <td className="py-1.5 pr-4 break-words">{l.message}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      <div className="border-t border-border px-4 py-2 text-[12px] text-fg-muted">
        <span className="num">{shown.length}</span> of <span className="num">{logs.length}</span> lines · times in UTC · newest first
      </div>
    </div>
  );
}
