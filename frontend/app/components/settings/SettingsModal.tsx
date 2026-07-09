"use client";

import { useEffect } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { Bell, Cpu, ListTodo, Mic, Moon, Sun, User, Wrench, X } from "lucide-react";
import { useTheme } from "@/hooks/useTheme";
import { useNotifications } from "@/hooks/useNotifications";
import { TONE_HINT, TONE_LABEL, useSettings, type Tone } from "@/hooks/useSettings";
import type { SettingsTab } from "@/hooks/useAppUI";
import TaskManager from "@/app/components/tasks/TaskManager";
import ModelSettings from "@/app/components/settings/ModelSettings";
import VoiceSettings from "@/app/components/voice/VoiceSettings";
import { VOICE_ENABLED } from "@/lib/voice";

const TONES: Tone[] = ["auto", "concise", "balanced", "detailed", "custom"];

const TABS: { key: SettingsTab; label: string; icon: React.ReactNode }[] = [
  { key: "general", label: "General", icon: <Wrench size={14} /> },
  { key: "models", label: "Models", icon: <Cpu size={14} /> },
  { key: "tasks", label: "Tasks", icon: <ListTodo size={14} /> },
  // Voice is behind a build flag (see lib/voice.ts). Filtered out rather than
  // deleted so flipping the flag restores the tab with no edit here.
  ...(VOICE_ENABLED ? [{ key: "voice" as const, label: "Voice", icon: <Mic size={14} /> }] : []),
  { key: "notifications", label: "Notifications", icon: <Bell size={14} /> },
  { key: "profile", label: "Profile", icon: <User size={14} /> },
];

export default function SettingsModal({
  open,
  tab,
  onTab,
  onClose,
}: {
  open: boolean;
  tab: SettingsTab;
  onTab: (t: SettingsTab) => void;
  onClose: () => void;
}) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  return (
    <AnimatePresence>
      {open && (
        <motion.div
          className="fixed inset-0 z-50 flex items-end justify-center bg-black/40 backdrop-blur-sm"
          onMouseDown={onClose}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.2 }}
        >
          <motion.div
            className="flex max-h-[90vh] w-full max-w-3xl flex-col rounded-t-3xl border border-border bg-surface-raised px-6 pb-8 pt-3 shadow-2xl"
            onMouseDown={(e) => e.stopPropagation()}
            initial={{ y: "100%" }}
            animate={{ y: 0 }}
            exit={{ y: "100%" }}
            transition={{ type: "spring", damping: 32, stiffness: 320 }}
          >
            <div className="mx-auto mb-3 h-1.5 w-10 rounded-full bg-border" />

            <div className="mb-4 flex items-center justify-between">
              <h2 className="text-base font-semibold text-text-primary">Settings</h2>
              <button
                onClick={onClose}
                aria-label="Close settings"
                className="flex h-7 w-7 items-center justify-center rounded-lg text-text-tertiary transition-colors hover:bg-surface-2 hover:text-text-primary"
              >
                <X size={16} />
              </button>
            </div>

            <div className="mb-5 flex gap-1 border-b border-border">
              {TABS.map((t) => (
                <button
                  key={t.key}
                  onClick={() => onTab(t.key)}
                  className={`-mb-px flex items-center gap-1.5 border-b-2 px-3 py-2 text-sm font-medium transition-colors ${
                    tab === t.key
                      ? "border-accent text-accent"
                      : "border-transparent text-text-secondary hover:text-text-primary"
                  }`}
                >
                  {t.icon}
                  {t.label}
                </button>
              ))}
            </div>

            {/* Fixed height so switching tabs doesn't resize the sheet — each
                tab scrolls within this constant area. */}
            <div className="h-[58vh] max-h-[520px] min-h-0 overflow-y-auto scroll-thin">
              {tab === "general" && <GeneralTab />}
              {tab === "models" && <ModelSettings />}
              {tab === "tasks" && <TaskManager />}
              {tab === "voice" && VOICE_ENABLED && <VoiceSettings />}
              {tab === "notifications" && <NotificationsTab />}
              {tab === "profile" && <ProfileTab />}
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}

function GeneralTab() {
  const { theme, toggle } = useTheme();
  const { tone, setTone, custom, setCustom } = useSettings();
  const isDark = theme === "dark";
  return (
    <>
      <section className="mb-5">
        <p className="mb-2 text-xs font-medium uppercase tracking-wider text-text-tertiary">Appearance</p>
        <button
          onClick={toggle}
          className="flex w-full items-center justify-between rounded-xl border border-border px-3 py-2.5 text-sm text-text-primary transition-colors hover:bg-surface-2"
        >
          <span className="flex items-center gap-2">
            {isDark ? <Moon size={15} /> : <Sun size={15} />}
            {isDark ? "Dark mode" : "Light mode"}
          </span>
          <span className="flex h-5 w-9 items-center rounded-full bg-border p-0.5">
            <span
              className={`h-4 w-4 rounded-full bg-surface shadow transition-transform ${isDark ? "translate-x-4" : "translate-x-0"}`}
            />
          </span>
        </button>
      </section>

      <section>
        <p className="mb-2 text-xs font-medium uppercase tracking-wider text-text-tertiary">Response style</p>
        <div className="grid grid-cols-2 gap-2">
          {TONES.map((t) => (
            <button
              key={t}
              onClick={() => setTone(t)}
              className={`flex flex-col items-start rounded-xl border px-3 py-2 text-left transition-colors ${
                tone === t ? "border-accent bg-accent-soft text-accent" : "border-border text-text-primary hover:bg-surface-2"
              }`}
            >
              <span className="text-sm font-medium">{TONE_LABEL[t]}</span>
              <span className="text-[11px] text-text-tertiary">{TONE_HINT[t]}</span>
            </button>
          ))}
        </div>
        {tone === "custom" && (
          <textarea
            value={custom}
            onChange={(e) => setCustom(e.target.value)}
            rows={3}
            placeholder="e.g. Answer like a senior engineer — terse, code-first, no hedging."
            className="mt-2 w-full resize-none rounded-xl border border-border bg-surface-2 px-3 py-2 text-sm text-text-primary placeholder:text-text-tertiary focus:border-accent/50 focus:outline-none"
          />
        )}
        <p className="mt-2 text-[11px] text-text-tertiary">Applied to new messages you send.</p>
      </section>
    </>
  );
}

function NotificationsTab() {
  const { permission, requestPermission } = useNotifications();
  const granted = permission === "granted";
  const unsupported = permission === "unsupported";
  return (
    <section className="flex flex-col gap-4">
      <div>
        <p className="mb-2 text-xs font-medium uppercase tracking-wider text-text-tertiary">Browser notifications</p>
        <div className="flex items-center justify-between rounded-xl border border-border px-3 py-3">
          <div className="min-w-0">
            <p className="text-sm font-medium text-text-primary">Desktop alerts</p>
            <p className="text-xs text-text-secondary">
              {unsupported
                ? "This browser doesn't support notifications."
                : granted
                  ? "On — reminders pop up even when this tab is in the background."
                  : "Off — turn on to get reminder pop-ups outside this tab."}
            </p>
          </div>
          {!unsupported && (
            <button
              onClick={requestPermission}
              disabled={granted}
              className="shrink-0 rounded-lg bg-accent px-3 py-1.5 text-xs font-medium text-accent-foreground transition-colors hover:bg-accent-hover disabled:opacity-50"
            >
              {granted ? "Enabled" : "Enable"}
            </button>
          )}
        </div>
      </div>
      <p className="text-xs text-text-tertiary">
        In-app notifications always work — open the bell in the sidebar to see reminders and missed tasks. Reminders are
        delivered by the assistant&apos;s scheduler, so they fire whether or not a chat is open.
      </p>
    </section>
  );
}

function ProfileTab() {
  return (
    <section className="flex flex-col gap-3">
      <div className="flex items-center gap-3 rounded-xl border border-border px-3 py-3">
        <div className="flex h-10 w-10 items-center justify-center rounded-full bg-accent text-accent-foreground">
          <User size={18} />
        </div>
        <div>
          <p className="text-sm font-medium text-text-primary">Local user</p>
          <p className="text-xs text-text-secondary">This is a single-user workspace.</p>
        </div>
      </div>
      <p className="text-xs text-text-tertiary">
        Accounts and sign-in aren&apos;t enabled yet. Your tasks, reminders and preferences are stored locally under a
        single profile — multi-user support is planned.
      </p>
    </section>
  );
}
