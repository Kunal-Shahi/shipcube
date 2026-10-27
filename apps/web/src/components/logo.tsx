export function Logo({ className }: { className?: string }) {
  // A cube drawn as three faces; the front face carries the accent.
  return (
    <svg viewBox="0 0 20 20" className={className} aria-hidden>
      <path d="M10 1.5 17.5 5.75 10 10 2.5 5.75Z" fill="var(--fg)" />
      <path d="M2.5 5.75 10 10v8.5L2.5 14.25Z" fill="var(--fg-muted)" />
      <path d="M17.5 5.75 10 10v8.5l7.5-4.25Z" fill="var(--accent)" />
    </svg>
  );
}

export function Wordmark() {
  return (
    <span className="inline-flex items-center gap-2 text-[14px] font-semibold tracking-[-0.01em]">
      <Logo className="size-5" />
      ShipCube
    </span>
  );
}
