"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { Bell, Plus, Settings } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { deleteProject, listProjects, renameProject, type ProjectSummary } from "@/lib/api";
import { useAppUI } from "@/hooks/useAppUI";
import { useNotifications } from "@/hooks/useNotifications";
import VoiceButton from "@/app/components/voice/VoiceButton";
import NewProjectButton from "./NewProjectButton";
import ProjectListItem from "./ProjectListItem";

const PIN_KEY = "asterion:pinned-projects";

export default function Sidebar() {
  const pathname = usePathname();
  const router = useRouter();
  const { sidebarCollapsed: collapsed, toggleSidebar, openSettings, openNotifications, projectsVersion } = useAppUI();
  const { unread } = useNotifications();
  const [projects, setProjects] = useState<ProjectSummary[]>([]);
  const [pinned, setPinned] = useState<string[]>([]);

  useEffect(() => {
    try {
      setPinned(JSON.parse(localStorage.getItem(PIN_KEY) ?? "[]"));
    } catch {
      setPinned([]);
    }
  }, []);

  const load = useCallback(
    () =>
      listProjects()
        .then((next) => setProjects((prev) => (JSON.stringify(prev) === JSON.stringify(next) ? prev : next)))
        .catch(() => {}),
    [],
  );

  useEffect(() => {
    let interval: ReturnType<typeof setInterval> | null = null;
    function start() {
      if (interval) return;
      load();
      interval = setInterval(load, 20000);
    }
    function stop() {
      if (!interval) return;
      clearInterval(interval);
      interval = null;
    }
    function onVis() {
      if (document.hidden) stop();
      else start();
    }
    if (!document.hidden) start();
    document.addEventListener("visibilitychange", onVis);
    return () => {
      stop();
      document.removeEventListener("visibilitychange", onVis);
    };
  }, [load]);

  // Refresh immediately when a chat command changes the project list.
  useEffect(() => {
    if (projectsVersion) load();
  }, [projectsVersion, load]);

  function persistPins(next: string[]) {
    setPinned(next);
    localStorage.setItem(PIN_KEY, JSON.stringify(next));
  }
  function togglePin(pid: string) {
    persistPins(pinned.includes(pid) ? pinned.filter((p) => p !== pid) : [pid, ...pinned]);
  }
  async function handleRename(pid: string, title: string) {
    setProjects((prev) => prev.map((p) => (p.project_id === pid ? { ...p, title } : p)));
    try {
      await renameProject(pid, title);
    } catch {
      load();
    }
  }
  async function handleDelete(pid: string) {
    setProjects((prev) => prev.filter((p) => p.project_id !== pid));
    if (pinned.includes(pid)) persistPins(pinned.filter((p) => p !== pid));
    if (pathname === `/pipeline/${pid}`) router.push("/");
    try {
      await deleteProject(pid);
    } catch {
      load();
    }
  }

  const pinnedProjects = pinned
    .map((pid) => projects.find((p) => p.project_id === pid))
    .filter((p): p is ProjectSummary => Boolean(p));
  const otherProjects = projects.filter((p) => !pinned.includes(p.project_id));

  const bell = (
    <button
      onClick={openNotifications}
      aria-label="Notifications"
      className="relative flex h-8 w-8 items-center justify-center rounded-lg text-text-secondary transition-colors hover:bg-surface hover:text-text-primary"
    >
      <Bell size={15} />
      {unread > 0 && (
        <span className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-accent px-1 text-[10px] font-semibold text-accent-foreground">
          {unread > 9 ? "9+" : unread}
        </span>
      )}
    </button>
  );

  if (collapsed) {
    return (
      <aside className="flex h-full w-14 shrink-0 flex-col items-center border-r border-border bg-surface-2/60 py-4">
        <button
          onClick={toggleSidebar}
          aria-label="Expand sidebar"
          title="Expand sidebar"
          className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent text-sm font-bold text-accent-foreground shadow-sm transition-transform hover:scale-105"
        >
          A
        </button>
        <Link
          href="/"
          title="New project"
          className="mt-4 flex h-8 w-8 items-center justify-center rounded-lg border border-border bg-surface text-text-primary transition-colors hover:border-accent/40 hover:bg-accent-soft"
        >
          <Plus size={16} />
        </Link>
        <div className="mt-3">
          <VoiceButton size={15} className="h-8 w-8" />
        </div>
        <div className="mt-3">{bell}</div>
        <div className="flex-1" />
        <button
          onClick={() => openSettings("general")}
          aria-label="Settings"
          className="mt-3 flex h-8 w-8 items-center justify-center rounded-lg text-text-secondary transition-colors hover:bg-surface hover:text-text-primary"
        >
          <Settings size={15} />
        </button>
      </aside>
    );
  }

  return (
    <aside className="flex h-full w-64 shrink-0 flex-col border-r border-border bg-surface-2/60">
      <div className="flex items-center gap-2 px-4 py-4">
        <button
          onClick={toggleSidebar}
          aria-label="Collapse sidebar"
          title="Collapse sidebar"
          className="flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-accent text-sm font-bold text-accent-foreground shadow-sm transition-transform hover:scale-105"
        >
          A
        </button>
        <span className="flex-1 text-sm font-semibold tracking-tight text-text-primary">Asterion</span>
        <VoiceButton size={15} className="h-8 w-8" />
        {bell}
      </div>

      <div className="px-3">
        <NewProjectButton />
      </div>

      <div className="mt-4 flex-1 space-y-4 overflow-y-auto scroll-thin px-3 pb-2">
        {pinnedProjects.length > 0 && (
          <Section label="Pinned">
            {pinnedProjects.map((p) => (
              <ProjectListItem
                key={p.project_id}
                project={p}
                active={pathname === `/pipeline/${p.project_id}`}
                pinned
                onRename={handleRename}
                onDelete={handleDelete}
                onTogglePin={togglePin}
              />
            ))}
          </Section>
        )}
        <Section label="Projects">
          {otherProjects.map((p) => (
            <ProjectListItem
              key={p.project_id}
              project={p}
              active={pathname === `/pipeline/${p.project_id}`}
              pinned={false}
              onRename={handleRename}
              onDelete={handleDelete}
              onTogglePin={togglePin}
            />
          ))}
          {projects.length === 0 && <li className="px-2.5 py-2 text-sm text-text-tertiary">No projects yet</li>}
        </Section>
      </div>

      <div className="border-t border-border p-3">
        <button
          onClick={() => openSettings("general")}
          className="flex w-full items-center gap-2 rounded-lg px-2.5 py-2 text-sm text-text-secondary transition-colors hover:bg-surface hover:text-text-primary"
        >
          <Settings size={15} /> Settings
        </button>
      </div>
    </aside>
  );
}

function Section({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div>
      <p className="px-1 pb-1.5 text-[11px] font-medium uppercase tracking-wider text-text-tertiary">{label}</p>
      <ul className="flex flex-col gap-0.5">{children}</ul>
    </div>
  );
}
