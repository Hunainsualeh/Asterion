"use client";

import type { ButtonHTMLAttributes } from "react";

type Variant = "primary" | "success" | "danger" | "ghost" | "subtle";
type Size = "sm" | "md";

const VARIANT_CLASSES: Record<Variant, string> = {
  primary: "bg-accent text-accent-foreground shadow-sm hover:bg-accent-hover",
  success: "bg-success text-white shadow-sm hover:brightness-110",
  danger: "bg-danger text-white shadow-sm hover:brightness-110",
  ghost: "border border-border bg-surface text-text-primary hover:bg-surface-2",
  subtle: "bg-surface-2 text-text-secondary hover:bg-border-soft hover:text-text-primary",
};

const SIZE_CLASSES: Record<Size, string> = {
  sm: "px-3 py-1.5 text-xs",
  md: "px-4 py-2 text-sm",
};

export default function Button({
  variant = "primary",
  size = "md",
  className = "",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: Size }) {
  return (
    <button
      {...props}
      className={`inline-flex items-center justify-center gap-1.5 rounded-xl font-medium transition-colors disabled:cursor-not-allowed disabled:opacity-40 ${SIZE_CLASSES[size]} ${VARIANT_CLASSES[variant]} ${className}`}
    />
  );
}
