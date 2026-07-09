"use client";

import type { ButtonHTMLAttributes, ReactNode } from "react";

export default function IconButton({
  active,
  className = "",
  children,
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { active?: boolean; children: ReactNode }) {
  return (
    <button
      {...props}
      className={`inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-lg transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${
        active
          ? "bg-accent-soft text-accent"
          : "text-text-secondary hover:bg-surface-2 hover:text-text-primary"
      } ${className}`}
    >
      {children}
    </button>
  );
}
