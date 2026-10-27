"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { cn } from "@/lib/cn";

export function RouteTabs({ tabs }: { tabs: { href: string; label: string; count?: number; also?: string }[] }) {
  const pathname = usePathname();
  return (
    <nav aria-label="Sections" className="-mb-px flex gap-5 overflow-x-auto border-b border-border">
      {tabs.map((t, i) => {
        const active = (i === 0 ? pathname === t.href : pathname.startsWith(t.href)) || (!!t.also && pathname.startsWith(t.also));
        return (
          <Link
            key={t.href}
            href={t.href}
            aria-current={active ? "page" : undefined}
            className={cn(
              "flex h-9 shrink-0 items-center gap-1.5 border-b-2 text-[13px]",
              active ? "border-accent font-medium text-fg" : "border-transparent text-fg-muted hover:text-fg",
            )}
          >
            {t.label}
            {t.count !== undefined && <span className="num text-[12px] text-fg-subtle">{t.count}</span>}
          </Link>
        );
      })}
    </nav>
  );
}
