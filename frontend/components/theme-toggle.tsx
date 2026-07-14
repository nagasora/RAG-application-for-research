"use client";

import { ComputerDesktopIcon, MoonIcon, SunIcon } from "@heroicons/react/24/outline";
import { useEffect, useState } from "react";

type Theme = "light" | "dark" | "system";

const STORAGE_KEY = "paperpilot.theme";
const OPTIONS: { value: Theme; label: string; Icon: typeof SunIcon }[] = [
  { value: "light", label: "ライトテーマ", Icon: SunIcon },
  { value: "dark", label: "ダークテーマ", Icon: MoonIcon },
  { value: "system", label: "端末の設定に合わせる", Icon: ComputerDesktopIcon },
];

function resolvedTheme(theme: Theme) {
  return theme === "system"
    ? (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light")
    : theme;
}

function applyTheme(theme: Theme) {
  const resolved = resolvedTheme(theme);
  const root = document.documentElement;
  root.dataset.theme = theme;
  root.dataset.resolvedTheme = resolved;
  root.style.colorScheme = resolved;
}

export function ThemeToggle() {
  const [theme, setTheme] = useState<Theme>("system");

  useEffect(() => {
    const stored = window.localStorage.getItem(STORAGE_KEY);
    const initial: Theme = stored === "light" || stored === "dark" || stored === "system" ? stored : "system";
    setTheme(initial);
    applyTheme(initial);
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = () => { if (theme === "system") applyTheme("system"); };
    media.addEventListener("change", onChange);
    return () => media.removeEventListener("change", onChange);
  }, [theme]);

  const chooseTheme = (next: Theme) => {
    window.localStorage.setItem(STORAGE_KEY, next);
    setTheme(next);
    applyTheme(next);
  };

  return <div role="group" aria-label="表示テーマ" className="grid grid-cols-3 gap-2">
    {OPTIONS.map(({ value, label, Icon }) => <button
      key={value}
      type="button"
      aria-label={label}
      aria-pressed={theme === value}
      title={label}
      onClick={() => chooseTheme(value)}
      className={`flex min-h-20 flex-col items-center justify-center gap-2 rounded-2xl border px-3 text-xs font-semibold transition focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-[#164f3b] ${theme === value ? "border-[#164f3b] bg-[#e7f0eb] text-[#164f3b]" : "border-[#d5d8d2] bg-white text-[#52605b] hover:bg-[#f3f6f3]"}`}
    ><Icon aria-hidden="true" className="h-5 w-5" /><span>{value === "light" ? "ライト" : value === "dark" ? "ダーク" : "端末に合わせる"}</span></button>)}
  </div>;
}
