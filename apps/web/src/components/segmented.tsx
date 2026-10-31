"use client";

import { useRef, type ReactNode } from "react";
import { cn } from "@/lib/cn";

// A radio group drawn as a segmented control (WAI-ARIA radio group pattern): one tab stop,
// arrow keys move the selection, Home/End jump to the ends.

export function Segmented<T extends string>({
  label,
  value,
  onChange,
  options,
  size = "md",
  className,
}: {
  label: string;
  value: T;
  onChange: (v: T) => void;
  options: { value: T; label: ReactNode; icon?: ReactNode; title?: string }[];
  size?: "sm" | "md";
  className?: string;
}) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);

  function onKeyDown(e: React.KeyboardEvent, i: number) {
    const last = options.length - 1;
    const next =
      e.key === "ArrowRight" || e.key === "ArrowDown" ? (i === last ? 0 : i + 1)
      : e.key === "ArrowLeft" || e.key === "ArrowUp" ? (i === 0 ? last : i - 1)
      : e.key === "Home" ? 0
      : e.key === "End" ? last
      : null;
    if (next === null) return;
    e.preventDefault();
    onChange(options[next].value);
    refs.current[next]?.focus();
  }

  return (
    <div role="radiogroup" aria-label={label} className={cn("inline-flex w-fit rounded-md border border-border bg-surface-2 p-0.5", className)}>
      {options.map((o, i) => {
        const checked = o.value === value;
        return (
          <button
            key={o.value}
            ref={(el) => {
              refs.current[i] = el;
            }}
            type="button"
            role="radio"
            aria-checked={checked}
            aria-label={o.title}
            title={o.title}
            tabIndex={checked ? 0 : -1}
            onClick={() => onChange(o.value)}
            onKeyDown={(e) => onKeyDown(e, i)}
            className={cn(
              "flex items-center gap-1.5 rounded text-[12px]",
              size === "sm" ? "size-6 justify-center" : "h-7 px-2.5",
              checked ? "bg-surface font-medium text-fg shadow-sm ring-1 ring-input-border" : "text-fg-muted hover:text-fg",
            )}
          >
            {o.icon}
            {o.label}
          </button>
        );
      })}
    </div>
  );
}
