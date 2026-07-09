"use client";

import { useEffect } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { AlarmClock, Bell, BellRing, CheckCheck, Clock, TriangleAlert, X } from "lucide-react";
import { useNotifications } from "@/hooks/useNotifications";
import type { AppNotification } from "@/lib/api";

function iconFor(kind: AppNotification["kind"]) {
  if (kind === "reminder") return <AlarmClock size={15} />;
  if (kind === "missed") return <TriangleAlert size={15} />;
  return <Bell size={15} />;
}

function timeAgo(ts: number): string {
  const s = Math.max(0, Math.floor(Date.now() / 1000 - ts));
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

export default function NotificationCenter({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { notifications, unread, permission, requestPermission, markRead, markAllRead } = useNotifications();

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  return (
    <AnimatePresence>
      {open && (
        <>
          <motion.div
            className="fixed inset-0 z-40 bg-black/30 backdrop-blur-[1px]"
            onClick={onClose}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
          />
          <motion.aside
            className="fixed inset-y-0 right-0 z-50 flex h-full w-full max-w-sm flex-col border-l border-border bg-surface shadow-2xl"
            initial={{ x: "100%" }}
            animate={{ x: 0 }}
            exit={{ x: "100%" }}
            transition={{ type: "spring", damping: 32, stiffness: 320 }}
          >
            <div className="flex items-center gap-2 border-b border-border px-4 py-3.5">
              <BellRing size={16} className="text-accent" />
              <span className="flex-1 text-sm font-semibold text-text-primary">
                Notifications {unread > 0 && <span className="text-text-tertiary">· {unread} new</span>}
              </span>
              {notifications.some((n) => !n.read) && (
                <button
                  onClick={markAllRead}
                  className="flex items-center gap-1 rounded-lg px-2 py-1 text-xs text-text-secondary transition-colors hover:bg-surface-2 hover:text-text-primary"
                >
                  <CheckCheck size={13} /> Mark all read
                </button>
              )}
              <button
                onClick={onClose}
                aria-label="Close notifications"
                className="flex h-7 w-7 items-center justify-center rounded-lg text-text-tertiary transition-colors hover:bg-surface-2 hover:text-text-primary"
              >
                <X size={16} />
              </button>
            </div>

            {permission !== "granted" && permission !== "unsupported" && (
              <button
                onClick={requestPermission}
                className="flex items-center gap-2 border-b border-border bg-accent-soft px-4 py-2.5 text-left text-xs text-accent transition-colors hover:bg-accent-soft/70"
              >
                <BellRing size={14} />
                Enable browser notifications so reminders reach you even on another tab.
              </button>
            )}

            <div className="flex-1 overflow-y-auto scroll-thin">
              {notifications.length === 0 ? (
                <div className="flex h-full flex-col items-center justify-center gap-2 p-6 text-center text-text-tertiary">
                  <Clock size={22} />
                  <p className="text-sm">No notifications yet.</p>
                  <p className="text-xs">Reminders and task alerts will show up here.</p>
                </div>
              ) : (
                <ul className="divide-y divide-border-soft">
                  {notifications.map((n) => (
                    <li key={n.id}>
                      <button
                        onClick={() => markRead(n.id)}
                        className={`flex w-full items-start gap-3 px-4 py-3 text-left transition-colors hover:bg-surface-2 ${
                          n.read ? "opacity-60" : ""
                        }`}
                      >
                        <span
                          className={`mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg ${
                            n.tone === "warning"
                              ? "bg-warning-bg text-warning"
                              : n.tone === "error"
                                ? "bg-danger-bg text-danger"
                                : "bg-accent-soft text-accent"
                          }`}
                        >
                          {iconFor(n.kind)}
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className="flex items-center gap-2">
                            <span className="min-w-0 flex-1 truncate text-sm font-medium text-text-primary">{n.title}</span>
                            {!n.read && <span className="h-2 w-2 shrink-0 rounded-full bg-accent" />}
                          </span>
                          {n.body && <span className="mt-0.5 block text-xs text-text-secondary">{n.body}</span>}
                          <span className="mt-1 block text-[11px] text-text-tertiary">{timeAgo(n.ts)}</span>
                        </span>
                      </button>
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </motion.aside>
        </>
      )}
    </AnimatePresence>
  );
}
