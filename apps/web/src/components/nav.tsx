"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { Boxes, Cable, CreditCard, Cloud, Database, Inbox, KeyRound, Sparkles } from "lucide-react";
import { cn } from "@/lib/cn";

const SECTIONS = [
  {
    label: null,
    items: [
      { href: "/apps", label: "Apps", icon: Boxes },
      { href: "/approvals", label: "Approvals", icon: Inbox, badge: "approvals" as const },
      { href: "/data", label: "Data", icon: Database },
      { href: "/setup", label: "Connect your agent", icon: Cable },
    ],
  },
  {
    label: "Workspace",
    items: [
      { href: "/settings/tokens", label: "API tokens", icon: KeyRound },
      { href: "/settings/ai", label: "AI providers", icon: Sparkles },
      { href: "/settings/gcp", label: "GCP project", icon: Cloud },
      { href: "/billing", label: "Billing", icon: CreditCard },
    ],
  },
];

export function Nav({ pendingApprovals = 0 }: { pendingApprovals?: number }) {
  const pathname = usePathname();
  return (
    <nav aria-label="Main" className="-mx-4 flex gap-1 overflow-x-auto px-4 [scrollbar-width:none] md:mx-0 md:flex-col md:gap-5 md:overflow-visible md:px-0">
      {SECTIONS.map((section, i) => (
        <div key={i} className="flex shrink-0 gap-1 md:flex-col md:gap-0.5">
          {section.label && (
            <div className="hidden px-2 pb-1 text-[12px] font-medium text-fg-subtle md:block">{section.label}</div>
          )}
          {section.items.map(({ href, label, icon: Icon, ...rest }) => {
            const badge = "badge" in rest && pendingApprovals > 0 ? pendingApprovals : null;
            const active = pathname === href || pathname.startsWith(href + "/");
            return (
              <Link
                key={href}
                href={href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "flex h-8 shrink-0 items-center gap-2 rounded-md px-2 text-[13px]",
                  active ? "bg-surface-2 font-medium text-fg" : "text-fg-muted hover:bg-surface-2 hover:text-fg",
                )}
              >
                <Icon className={cn("size-4", active ? "text-accent" : "text-fg-subtle")} aria-hidden />
                {label}
                {badge !== null && (
                  <span className="num ml-auto rounded-full bg-accent px-1.5 text-[11px] leading-[18px] font-medium text-accent-fg" aria-label={`${badge} waiting`}>
                    {badge}
                  </span>
                )}
              </Link>
            );
          })}
        </div>
      ))}
    </nav>
  );
}
