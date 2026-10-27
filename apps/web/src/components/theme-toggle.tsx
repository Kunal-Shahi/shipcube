"use client";

import { Monitor, Moon, Sun } from "lucide-react";
import { useEffect, useState } from "react";
import { Segmented } from "./segmented";

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

  return (
    <Segmented
      label="Theme"
      size="sm"
      className="bg-transparent"
      value={theme}
      onChange={apply}
      options={[
        { value: "system", title: "System theme", label: null, icon: <Monitor className="size-3.5" aria-hidden /> },
        { value: "light", title: "Light theme", label: null, icon: <Sun className="size-3.5" aria-hidden /> },
        { value: "dark", title: "Dark theme", label: null, icon: <Moon className="size-3.5" aria-hidden /> },
      ]}
    />
  );
}
