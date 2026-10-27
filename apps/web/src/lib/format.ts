import { MOCK_NOW } from "./mock-data";

const usd = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2 });
const int = new Intl.NumberFormat("en-US");
const compact = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });

export const money = (n: number) => usd.format(n);
export const count = (n: number) => int.format(n);
export const short = (n: number) => compact.format(n);
export const pct = (n: number, digits = 1) => `${n.toFixed(digits)}%`;

export function ago(iso: string, now = MOCK_NOW) {
  const s = Math.max(0, Math.round((now - new Date(iso).getTime()) / 1000));
  if (s < 60) return "just now";
  const m = Math.round(s / 60);
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 48) return `${h} h ago`;
  return `${Math.round(h / 24)} days ago`;
}

export function date(iso: string) {
  return new Date(iso).toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", timeZone: "UTC" });
}

export function time(iso: string) {
  return new Date(iso).toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
    timeZone: "UTC",
  });
}

export const RESTORE_DAYS = 7;

/** When a soft-deleted app stops being restorable, and how many days are left. */
export function restoreWindow(deletedAt: string, now = MOCK_NOW) {
  const until = new Date(new Date(deletedAt).getTime() + RESTORE_DAYS * 86_400_000);
  const daysLeft = Math.max(0, Math.ceil((until.getTime() - now) / 86_400_000));
  return { until: until.toISOString(), daysLeft };
}
