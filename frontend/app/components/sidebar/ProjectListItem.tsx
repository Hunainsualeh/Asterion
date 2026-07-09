"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { MoreHorizontal, Pencil, Pin, PinOff, Trash2 } from "lucide-react";
import type { ProjectSummary } from "@/lib/api";

const STATUS_DOT: Record<string, string> = {
  complete: "bg-success",
  error: "bg-danger",
};

export function statusDotClass(status: string, running: boolean): string {
  if (running) return "bg-accent animate-pulse";
  return STATUS_DOT[status] ?? "bg-text-tertiary";
}

export default function ProjectListItem({
  project,
  active,
  pinned,
  onRename,
  onDelete,
  onTogglePin,
}: {
  project: ProjectSummary;
  active: boolean;
  pinned: boolean;
  onRename: (pid: string, title: string) => void;
  onDelete: (pid: string) => void;
  onTogglePin: (pid: string) => void;
}) {
  const title = project.title || project.idea || "Untitled project";
  const [menuOpen, setMenuOpen] = useState(false);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(title);
  const wrapRef = useRef<HTMLLIElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!menuOpen) return;
    function onAway(e: MouseEvent) {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setMenuOpen(false);
    }
    document.addEventListener("mousedown", onAway);
    return () => document.removeEventListener("mousedown", onAway);
  }, [menuOpen]);

  useEffect(() => {
    if (editing) inputRef.current?.focus();
  }, [editing]);

  function commitRename() {
    const next = draft.trim();
    if (next && next !== title) onRename(project.project_id, next);
    setEditing(false);
  }

  if (editing) {
    return (
      <li className="px-1 py-0.5">
        <input
          ref={inputRef}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onBlur={commitRename}
          onKeyDown={(e) => {
            if (e.key === "Enter") commitRename();
            if (e.key === "Escape") {
              setDraft(title);
              setEditing(false);
            }
          }}
          className="w-full rounded-lg border border-accent/50 bg-surface px-2 py-1.5 text-sm text-text-primary focus:outline-none"
        />
      </li>
    );
  }

  return (
    <li ref={wrapRef} className="group/item relative">
      <Link
        href={`/pipeline/${project.project_id}`}
        className={`flex items-center gap-2 rounded-lg py-2 pl-2.5 pr-1 text-sm transition-colors ${
          active ? "bg-accent-soft text-accent" : "text-text-secondary hover:bg-surface hover:text-text-primary"
        }`}
      >
        <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${statusDotClass(project.status, project.running)}`} />
        <span className="min-w-0 flex-1 truncate font-medium">{title}</span>
        {pinned && <Pin size={11} className="shrink-0 text-text-tertiary" />}
        <button
          type="button"
          onClick={(e) => {
            e.preventDefault();
            setMenuOpen((v) => !v);
          }}
          aria-label="Project options"
          className={`flex h-6 w-6 shrink-0 items-center justify-center rounded-md text-text-tertiary transition-opacity hover:bg-surface-2 hover:text-text-primary ${
            menuOpen ? "opacity-100" : "opacity-0 group-hover/item:opacity-100"
          }`}
        >
          <MoreHorizontal size={15} />
        </button>
      </Link>

      {menuOpen && (
        <div className="absolute right-1 top-9 z-20 w-40 rounded-lg border border-border bg-surface-raised p-1 shadow-lg">
          <MenuItem
            icon={pinned ? <PinOff size={14} /> : <Pin size={14} />}
            label={pinned ? "Unpin" : "Pin"}
            onClick={() => {
              onTogglePin(project.project_id);
              setMenuOpen(false);
            }}
          />
          <MenuItem
            icon={<Pencil size={14} />}
            label="Rename"
            onClick={() => {
              setDraft(title);
              setEditing(true);
              setMenuOpen(false);
            }}
          />
          <MenuItem
            icon={<Trash2 size={14} />}
            label="Delete"
            danger
            onClick={() => {
              setMenuOpen(false);
              if (confirm(`Delete "${title}"? This can't be undone.`)) onDelete(project.project_id);
            }}
          />
        </div>
      )}
    </li>
  );
}

function MenuItem({
  icon,
  label,
  onClick,
  danger,
}: {
  icon: React.ReactNode;
  label: string;
  onClick: () => void;
  danger?: boolean;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`flex w-full items-center gap-2 rounded-md px-2 py-1.5 text-left text-sm transition-colors hover:bg-surface-2 ${
        danger ? "text-danger" : "text-text-primary"
      }`}
    >
      {icon}
      {label}
    </button>
  );
}
