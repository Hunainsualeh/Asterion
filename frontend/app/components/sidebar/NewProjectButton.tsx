import Link from "next/link";
import { Plus } from "lucide-react";

export default function NewProjectButton() {
  return (
    <Link
      href="/"
      className="flex w-full items-center gap-2 rounded-lg border border-border bg-surface px-3 py-2 text-sm font-medium text-text-primary transition-colors hover:border-accent/40 hover:bg-accent-soft"
    >
      <Plus size={16} /> New project
    </Link>
  );
}
