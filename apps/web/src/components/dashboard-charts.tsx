// Small charts for the Overview page. Plain SVG and CSS, rendered on the server: no hover layer,
// so each carries its numbers in text (a label, or an sr-only table) as well as in the picture.
import { cn } from "@/lib/cn";

/** A tiny line over time, scaled to its own range. `label` names it for screen readers. */
export function Sparkline({ values, label, className }: { values: number[]; label: string; className?: string }) {
  if (values.length < 2) return <span className="text-[12px] text-fg-subtle">No data yet</span>;
  const w = 120;
  const h = 28;
  const min = Math.min(...values);
  const max = Math.max(...values);
  const span = max - min || 1;
  const pts = values.map((v, i) => [(i / (values.length - 1)) * w, h - 2 - ((v - min) / span) * (h - 4)]);
  const line = pts.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join("");
  const [lx, ly] = pts[pts.length - 1];
  return (
    <svg viewBox={`0 0 ${w} ${h}`} className={cn("h-7 w-[120px] overflow-visible", className)} role="img" aria-label={label}>
      <path d={`${line}L${w},${h}L0,${h}Z`} fill="var(--mark, var(--accent))" opacity={0.12} />
      <path d={line} fill="none" stroke="var(--mark, var(--accent))" strokeWidth={1.5} strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={lx} cy={ly} r={2.5} fill="var(--mark, var(--accent))" />
    </svg>
  );
}

export type Slice = { label: string; value: number; color: string };

/** A ring split by share, with the total in the middle and a legend beside it. */
export function Donut({ slices, total, caption }: { slices: Slice[]; total: string; caption: string }) {
  const sum = slices.reduce((s, x) => s + x.value, 0);
  const r = 42;
  const c = 2 * Math.PI * r;
  let offset = 0;
  return (
    <figure className="flex flex-wrap items-center gap-6">
      <div className="relative size-[120px] shrink-0">
        <svg viewBox="0 0 100 100" className="size-full -rotate-90" aria-hidden>
          <circle cx={50} cy={50} r={r} fill="none" stroke="var(--surface-2)" strokeWidth={12} />
          {sum > 0 &&
            slices.map((s) => {
              const len = (s.value / sum) * c;
              const el = (
                <circle
                  key={s.label}
                  cx={50}
                  cy={50}
                  r={r}
                  fill="none"
                  stroke={s.color}
                  strokeWidth={12}
                  strokeDasharray={`${len} ${c - len}`}
                  strokeDashoffset={-offset}
                />
              );
              offset += len;
              return el;
            })}
        </svg>
        <div className="absolute inset-0 grid place-items-center text-center">
          <span className="num font-display text-[18px] font-semibold leading-none">{total}</span>
        </div>
      </div>
      <figcaption className="sr-only">{caption}</figcaption>
      <ul className="flex min-w-[140px] flex-1 flex-col gap-2">
        {slices.map((s) => (
          <li key={s.label} className="flex items-center gap-2 text-[13px]">
            <span className="size-2.5 shrink-0 rounded-sm" style={{ background: s.color }} aria-hidden />
            <span className="flex-1 text-fg-muted">{s.label}</span>
            <span className="num font-medium">{sum > 0 ? `${Math.round((s.value / sum) * 100)}%` : "0%"}</span>
          </li>
        ))}
      </ul>
    </figure>
  );
}

/** Horizontal bars, one per row, each split into parts that share the colours of `parts`. */
export function StackedBars({
  rows,
  parts,
  format,
  caption,
}: {
  rows: { label: string; values: number[] }[];
  parts: { label: string; color: string }[];
  format: (n: number) => string;
  caption: string;
}) {
  const max = Math.max(...rows.map((r) => r.values.reduce((s, v) => s + v, 0)), 0);
  return (
    <figure>
      <ul className="flex flex-col gap-3">
        {rows.map((r) => {
          const total = r.values.reduce((s, v) => s + v, 0);
          return (
            <li key={r.label} className="grid grid-cols-[minmax(0,140px)_minmax(0,1fr)_auto] items-center gap-3 text-[13px]">
              <span className="truncate">{r.label}</span>
              <span className="flex h-2.5 overflow-hidden rounded-full bg-surface-2" aria-hidden>
                {r.values.map((v, i) => (
                  <span key={i} style={{ width: `${max ? (v / max) * 100 : 0}%`, background: parts[i].color }} />
                ))}
              </span>
              <span className="num w-16 text-right font-medium">{format(total)}</span>
            </li>
          );
        })}
      </ul>
      <ul className="mt-4 flex flex-wrap gap-x-4 gap-y-1 text-[12px] text-fg-muted" aria-hidden>
        {parts.map((p) => (
          <li key={p.label} className="flex items-center gap-1.5">
            <span className="size-2.5 rounded-sm" style={{ background: p.color }} />
            {p.label}
          </li>
        ))}
      </ul>
      <div className="sr-only">
        <table>
          <caption>{caption}</caption>
          <thead>
            <tr>
              <th scope="col">Name</th>
              {parts.map((p) => (
                <th key={p.label} scope="col">{p.label}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.label}>
                <th scope="row">{r.label}</th>
                {r.values.map((v, i) => (
                  <td key={i}>{format(v)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </figure>
  );
}
