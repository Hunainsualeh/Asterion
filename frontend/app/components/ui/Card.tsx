"use client";

import { ChevronDown } from "lucide-react";
import { useState, type ReactNode } from "react";

/** A rounded, elevated content card. With `title`, it becomes collapsible. */
export default function Card({
  title,
  subtitle,
  defaultOpen = true,
  accent,
  children,
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  defaultOpen?: boolean;
  /** Optional left accent bar color class, e.g. "bg-accent". */
  accent?: string;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);

  if (!title) {
    return (
      <div className="overflow-hidden rounded-2xl border border-border bg-surface-raised shadow-sm">{children}</div>
    );
  }

  return (
    <div className="relative overflow-hidden rounded-2xl border border-border bg-surface-raised shadow-sm">
      {accent && <span className={`absolute inset-y-0 left-0 w-1 ${accent}`} />}
      <button
        onClick={() => setOpen((v) => !v)}
        className="flex w-full items-center justify-between gap-3 px-5 py-3.5 text-left"
      >
        <span className="min-w-0">
          <span className="block text-sm font-semibold text-text-primary">{title}</span>
          {subtitle && <span className="mt-0.5 block text-xs text-text-tertiary">{subtitle}</span>}
        </span>
        <ChevronDown
          size={16}
          className={`shrink-0 text-text-tertiary transition-transform ${open ? "rotate-180" : ""}`}
        />
      </button>
      {open && <div className="border-t border-border-soft px-5 py-4">{children}</div>}
    </div>
  );
}
