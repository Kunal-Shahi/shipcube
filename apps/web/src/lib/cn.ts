import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

// Later classes win, so callers can override a component's width or padding.
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
