"use client";

import { Moon, Sun } from "lucide-react";
import { useTheme } from "@/hooks/useTheme";

export default function ThemeToggle() {
  const { theme, toggle } = useTheme();
  const isDark = theme === "dark";

  return (
    <button
      onClick={toggle}
      className="flex w-full items-center justify-between rounded-lg px-2.5 py-2 text-sm text-text-secondary transition-colors hover:bg-surface hover:text-text-primary"
    >
      <span className="flex items-center gap-2">
        {isDark ? <Moon size={14} /> : <Sun size={14} />}
        {isDark ? "Dark mode" : "Light mode"}
      </span>
      <span className="flex h-5 w-9 items-center rounded-full bg-border p-0.5">
        <span
          className={`h-4 w-4 rounded-full bg-surface shadow transition-transform ${isDark ? "translate-x-4" : "translate-x-0"}`}
        />
      </span>
    </button>
  );
}
