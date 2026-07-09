"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import SettingsModal from "@/app/components/settings/SettingsModal";
import NotificationCenter from "@/app/components/notifications/NotificationCenter";

export type SettingsTab = "general" | "models" | "tasks" | "voice" | "notifications" | "profile";

const COLLAPSE_KEY = "asterion:sidebar-collapsed";

interface AppUIValue {
  sidebarCollapsed: boolean;
  toggleSidebar: () => void;
  setSidebarCollapsed: (v: boolean) => void;
  openSettings: (tab?: SettingsTab) => void;
  closeSettings: () => void;
  openNotifications: () => void;
  closeNotifications: () => void;
  toggleNotifications: () => void;
  /** Bumped whenever tasks change (e.g. the Task Agent edited them) so open
   * task views can re-fetch. */
  tasksVersion: number;
  bumpTasks: () => void;
  /** Bumped when projects change from outside the sidebar (e.g. a chat command
   * deleted one or all) so the sidebar list refreshes immediately. */
  projectsVersion: number;
  bumpProjects: () => void;
}

const Ctx = createContext<AppUIValue | null>(null);

export function AppUIProvider({ children }: { children: ReactNode }) {
  const [sidebarCollapsed, setCollapsedState] = useState(false);
  const [settings, setSettings] = useState<{ open: boolean; tab: SettingsTab }>({ open: false, tab: "general" });
  const [notifOpen, setNotifOpen] = useState(false);
  const [tasksVersion, setTasksVersion] = useState(0);
  const [projectsVersion, setProjectsVersion] = useState(0);

  useEffect(() => {
    setCollapsedState(localStorage.getItem(COLLAPSE_KEY) === "1");
  }, []);

  const setSidebarCollapsed = useCallback((v: boolean) => {
    setCollapsedState(v);
    localStorage.setItem(COLLAPSE_KEY, v ? "1" : "0");
  }, []);
  const toggleSidebar = useCallback(() => setSidebarCollapsed(!sidebarCollapsed), [sidebarCollapsed, setSidebarCollapsed]);

  const openSettings = useCallback((tab: SettingsTab = "general") => setSettings({ open: true, tab }), []);
  const closeSettings = useCallback(() => setSettings((s) => ({ ...s, open: false })), []);
  const openNotifications = useCallback(() => setNotifOpen(true), []);
  const closeNotifications = useCallback(() => setNotifOpen(false), []);
  const toggleNotifications = useCallback(() => setNotifOpen((v) => !v), []);
  const bumpTasks = useCallback(() => setTasksVersion((v) => v + 1), []);
  const bumpProjects = useCallback(() => setProjectsVersion((v) => v + 1), []);

  const value = useMemo(
    () => ({
      sidebarCollapsed,
      toggleSidebar,
      setSidebarCollapsed,
      openSettings,
      closeSettings,
      openNotifications,
      closeNotifications,
      toggleNotifications,
      tasksVersion,
      bumpTasks,
      projectsVersion,
      bumpProjects,
    }),
    [sidebarCollapsed, toggleSidebar, setSidebarCollapsed, openSettings, closeSettings, openNotifications,
     closeNotifications, toggleNotifications, tasksVersion, bumpTasks, projectsVersion, bumpProjects],
  );

  return (
    <Ctx.Provider value={value}>
      {children}
      {/* Global overlays — openable from the sidebar OR from a chat command. */}
      <SettingsModal
        open={settings.open}
        tab={settings.tab}
        onTab={(t) => setSettings((s) => ({ ...s, tab: t }))}
        onClose={closeSettings}
      />
      <NotificationCenter open={notifOpen} onClose={closeNotifications} />
    </Ctx.Provider>
  );
}

export function useAppUI() {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useAppUI must be used within AppUIProvider");
  return ctx;
}
