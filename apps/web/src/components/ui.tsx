import Link from "next/link";
import type { ComponentProps, ReactNode } from "react";
import { AlertTriangle, CheckCircle2, CircleDashed, Loader2, XCircle } from "lucide-react";
import { cn } from "@/lib/cn";
import type { Freshness, Health, TierId } from "@/lib/types";
import { TIERS } from "@/lib/types";

type Variant = "primary" | "secondary" | "ghost" | "danger";
type Size = "sm" | "md";

const base =
  "inline-flex items-center justify-center gap-1.5 whitespace-nowrap rounded-md font-medium transition-colors disabled:pointer-events-none disabled:opacity-50";
const variants: Record<Variant, string> = {
  primary: "bg-accent text-accent-fg hover:bg-accent-hover",
  secondary: "border border-border-strong bg-surface text-fg hover:bg-surface-2",
  ghost: "text-fg-muted hover:bg-surface-2 hover:text-fg",
  danger: "border border-border-strong bg-surface text-critical hover:bg-critical-soft",
};
const sizes: Record<Size, string> = { sm: "h-7 px-2.5 text-[12px]", md: "h-8 px-3 text-[13px]" };

export function buttonClass(variant: Variant = "secondary", size: Size = "md", className?: string) {
  return cn(base, variants[variant], sizes[size], className);
}

export function Button({
  variant = "secondary",
  size = "md",
  pending,
  className,
  children,
  ...props
}: ComponentProps<"button"> & { variant?: Variant; size?: Size; pending?: boolean }) {
  return (
    <button className={buttonClass(variant, size, className)} disabled={pending || props.disabled} {...props}>
      {pending && <Loader2 className="size-3.5 animate-spin" aria-hidden />}
      {children}
    </button>
  );
}

export function ButtonLink({
  variant = "secondary",
  size = "md",
  className,
  ...props
}: ComponentProps<typeof Link> & { variant?: Variant; size?: Size }) {
  return <Link className={buttonClass(variant, size, className)} {...props} />;
}

export function Panel({ className, ...props }: ComponentProps<"section">) {
  return <section className={cn("rounded-lg border border-border bg-surface", className)} {...props} />;
}

export function PanelHeader({
  title,
  description,
  actions,
  className,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-wrap items-start justify-between gap-3 border-b border-border px-4 py-3", className)}>
      <div className="min-w-0">
        <h2 className="text-[14px] font-semibold leading-5">{title}</h2>
        {description && <p className="mt-0.5 text-fg-muted">{description}</p>}
      </div>
      {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
    </div>
  );
}

export function PageHeader({
  title,
  description,
  actions,
  eyebrow,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  eyebrow?: ReactNode;
}) {
  return (
    <header className="mb-6 flex flex-wrap items-end justify-between gap-4">
      <div className="min-w-0">
        {eyebrow && <div className="mb-1 text-[12px] text-fg-muted">{eyebrow}</div>}
        <h1 className="text-[20px] font-semibold leading-7 tracking-[-0.01em]">{title}</h1>
        {description && <p className="mt-1 max-w-[68ch] text-fg-muted">{description}</p>}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </header>
  );
}

type Tone = "good" | "warning" | "critical" | "neutral" | "accent";
const tones: Record<Tone, string> = {
  good: "bg-good-soft text-good",
  warning: "bg-warning-soft text-warning",
  critical: "bg-critical-soft text-critical",
  neutral: "bg-surface-2 text-fg-muted",
  accent: "bg-accent-soft text-accent",
};

export function Pill({ tone = "neutral", icon, children }: { tone?: Tone; icon?: ReactNode; children: ReactNode }) {
  return (
    <span className={cn("inline-flex h-5 items-center gap-1 rounded-full px-2 text-[12px] font-medium", tones[tone])}>
      {icon}
      {children}
    </span>
  );
}

const iconCls = "size-3.5";

export function HealthPill({ health }: { health: Health }) {
  switch (health) {
    case "healthy":
      return <Pill tone="good" icon={<CheckCircle2 className={iconCls} aria-hidden />}>Healthy</Pill>;
    case "degraded":
      return <Pill tone="warning" icon={<AlertTriangle className={iconCls} aria-hidden />}>Degraded</Pill>;
    case "down":
      return <Pill tone="critical" icon={<XCircle className={iconCls} aria-hidden />}>Down</Pill>;
    case "deploying":
      return <Pill tone="accent" icon={<Loader2 className={cn(iconCls, "animate-spin")} aria-hidden />}>Deploying</Pill>;
  }
}

export function FreshnessPill({ freshness }: { freshness: Freshness }) {
  if (freshness === "fresh") return <Pill tone="good" icon={<CheckCircle2 className={iconCls} aria-hidden />}>Fresh</Pill>;
  if (freshness === "stale") return <Pill tone="warning" icon={<AlertTriangle className={iconCls} aria-hidden />}>Stale</Pill>;
  return <Pill icon={<CircleDashed className={iconCls} aria-hidden />}>No data yet</Pill>;
}

export function TierBadge({ tier, long }: { tier: TierId; long?: boolean }) {
  return (
    <span className="inline-flex h-5 items-center rounded border border-border-strong px-1.5 font-mono text-[11.5px] text-fg-muted">
      {tier}
      {long && <span className="ml-1 font-sans">· {TIERS[tier].name}</span>}
    </span>
  );
}

export function Stat({
  label,
  value,
  note,
  status,
}: {
  label: ReactNode;
  value: ReactNode;
  note?: ReactNode;
  status?: ReactNode;
}) {
  return (
    <div className="flex min-w-0 flex-col gap-1 px-4 py-3">
      <div className="flex items-center justify-between gap-2 text-[12px] font-medium text-fg-muted">
        <span>{label}</span>
        {status}
      </div>
      <div className="num text-[22px] font-semibold leading-7 tracking-[-0.01em]">{value}</div>
      {note && <div className="text-[12px] text-fg-muted">{note}</div>}
    </div>
  );
}

export function StatRow({ children, className }: { children: ReactNode; className?: string }) {
  return (
    <Panel
      className={cn(
        "grid grid-cols-1 divide-y divide-border sm:grid-cols-2 sm:divide-y-0 lg:grid-cols-4 [&>*]:border-border sm:[&>*:nth-child(even)]:border-l lg:[&>*:not(:first-child)]:border-l",
        className,
      )}
    >
      {children}
    </Panel>
  );
}

export function Meter({ value, max, label }: { value: number; max: number; label?: string }) {
  const ratio = max > 0 ? Math.min(value / max, 1) : 0;
  const color = ratio >= 1 ? "bg-critical" : ratio >= 0.8 ? "bg-warning" : "bg-accent";
  return (
    <div
      role="meter"
      aria-valuemin={0}
      aria-valuemax={max}
      aria-valuenow={value}
      aria-label={label}
      className="h-1.5 w-full overflow-hidden rounded-full bg-surface-2"
    >
      <div className={cn("h-full rounded-full", color)} style={{ width: `${ratio * 100}%` }} />
    </div>
  );
}

export function EmptyState({
  title,
  body,
  action,
  className,
}: {
  title: string;
  body: ReactNode;
  action?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col items-start gap-2 px-6 py-10", className)}>
      <h3 className="text-[14px] font-semibold">{title}</h3>
      <div className="max-w-[60ch] text-fg-muted">{body}</div>
      {action && <div className="mt-2">{action}</div>}
    </div>
  );
}

export function Skeleton({ className }: { className?: string }) {
  return <div className={cn("skeleton h-3", className)} aria-hidden />;
}

export function TableSkeleton({ rows = 6, cols = 5 }: { rows?: number; cols?: number }) {
  return (
    <div aria-busy="true" aria-label="Loading">
      <div className="flex gap-6 border-b border-border px-4 py-2.5">
        {Array.from({ length: cols }, (_, i) => (
          <Skeleton key={i} className="h-2.5 flex-1" />
        ))}
      </div>
      {Array.from({ length: rows }, (_, r) => (
        <div key={r} className="flex h-10 items-center gap-6 border-b border-border px-4 last:border-0">
          {Array.from({ length: cols }, (_, i) => (
            <Skeleton key={i} className={cn("flex-1", i === 0 && "flex-[2]")} />
          ))}
        </div>
      ))}
    </div>
  );
}

export function Field({
  label,
  htmlFor,
  help,
  error,
  children,
}: {
  label: string;
  htmlFor: string;
  help?: ReactNode;
  error?: string | null;
  children: ReactNode;
}) {
  return (
    <div className="flex flex-col gap-1.5">
      <label htmlFor={htmlFor} className="text-[12px] font-medium text-fg">
        {label}
      </label>
      {children}
      {error ? (
        <p className="text-[12px] text-critical">{error}</p>
      ) : (
        help && <p className="text-[12px] text-fg-muted">{help}</p>
      )}
    </div>
  );
}

export const inputClass =
  "h-8 w-full min-w-0 rounded-md border border-border-strong bg-surface px-2.5 text-[13px] text-fg placeholder:text-fg-subtle focus:border-accent focus:outline-none focus:ring-2 focus:ring-accent/25";

export function Input({ className, ...props }: ComponentProps<"input">) {
  return <input className={cn(inputClass, className)} {...props} />;
}

export function Select({ className, ...props }: ComponentProps<"select">) {
  return <select className={cn(inputClass, "pr-7", className)} {...props} />;
}

export function Kbd({ children }: { children: ReactNode }) {
  return <code className="rounded bg-surface-2 px-1 py-px font-mono text-[12px]">{children}</code>;
}

export function DescList({ items }: { items: { label: ReactNode; value: ReactNode }[] }) {
  return (
    <dl className="grid grid-cols-[minmax(110px,max-content)_1fr] gap-x-6 gap-y-2">
      {items.map((it, i) => (
        <div key={i} className="contents">
          <dt className="text-fg-muted">{it.label}</dt>
          <dd className="min-w-0 break-words">{it.value}</dd>
        </div>
      ))}
    </dl>
  );
}
