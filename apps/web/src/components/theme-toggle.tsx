"use client";

import { Monitor, Moon, Sun } from "lucide-react";
import { useEffect, useState } from "react";
import { cn } from "@/lib/cn";

type Theme = "system" | "light" | "dark";
const KEY = "sc-theme";

// Runs before paint (see layout.tsx) so the stored theme applies without a flash.
export const themeScript = `try{var t=localStorage.getItem("${KEY}");if(t==="light"||t==="dark")document.documentElement.dataset.theme=t}catch(e){}`;

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>("system");

  useEffect(() => {
    try {
      const t = localStorage.getItem(KEY);
      if (t === "light" || t === "dark") setTheme(t);
    } catch {}
  }, []);

  function apply(t: Theme) {
    setTheme(t);
    const root = document.documentElement;
    if (t === "system") delete root.dataset.theme;
    else root.dataset.theme = t;
    try {
      if (t === "system") localStorage.removeItem(KEY);
      else localStorage.setItem(KEY, t);
    } catch {}
  }

  const opts: { id: Theme; icon: typeof Sun; label: string }[] = [
    { id: "system", icon: Monitor, label: "System theme" },
    { id: "light", icon: Sun, label: "Light theme" },
    { id: "dark", icon: Moon, label: "Dark theme" },
  ];

  return (
    <div className="inline-flex rounded-md border border-border p-0.5" role="radiogroup" aria-label="Theme">
      {opts.map(({ id, icon: Icon, label }) => (
        <button
          key={id}
          type="button"
          role="radio"
          aria-checked={theme === id}
          aria-label={label}
          title={label}
          onClick={() => apply(id)}
          className={cn(
            "grid size-6 place-items-center rounded text-fg-subtle hover:text-fg",
            theme === id && "bg-surface-2 text-fg",
          )}
        >
          <Icon className="size-3.5" aria-hidden />
        </button>
      ))}
    </div>
  );
}
