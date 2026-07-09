"use client";

import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import {
  listNotifications,
  markNotificationRead,
  notificationsUrl,
  type AppNotification,
} from "@/lib/api";

type Permission = "default" | "granted" | "denied" | "unsupported";

interface NotificationsValue {
  notifications: AppNotification[];
  unread: number;
  permission: Permission;
  requestPermission: () => Promise<void>;
  markRead: (id: string) => void;
  markAllRead: () => void;
  refresh: () => void;
}

const Ctx = createContext<NotificationsValue | null>(null);

export function NotificationsProvider({ children }: { children: ReactNode }) {
  const [notifications, setNotifications] = useState<AppNotification[]>([]);
  const [unread, setUnread] = useState(0);
  const [permission, setPermission] = useState<Permission>("default");
  const seen = useRef<Set<string>>(new Set());
  const swReg = useRef<ServiceWorkerRegistration | null>(null);

  const refresh = useCallback(() => {
    listNotifications(50)
      .then((r) => {
        setNotifications(r.notifications);
        setUnread(r.unread);
        r.notifications.forEach((n) => seen.current.add(n.id));
      })
      .catch(() => {});
  }, []);

  // Initial load + permission state + service-worker registration (used for
  // more reliable OS notifications and future Web Push).
  useEffect(() => {
    refresh();
    if (typeof Notification === "undefined") setPermission("unsupported");
    else setPermission(Notification.permission as Permission);
    if ("serviceWorker" in navigator) {
      navigator.serviceWorker
        .register("/sw.js")
        .then((reg) => (swReg.current = reg))
        .catch(() => {});
    }
  }, [refresh]);

  // Live stream — a single global connection for the whole app.
  useEffect(() => {
    const source = new EventSource(notificationsUrl());
    const onNotification = (e: MessageEvent) => {
      try {
        const id = e.lastEventId || "";
        if (id && seen.current.has(id)) return;
        if (id) seen.current.add(id);
        const n = JSON.parse(e.data) as AppNotification;
        if (id && !n.id) n.id = id;
        setNotifications((prev) => [n, ...prev].slice(0, 100));
        setUnread((u) => u + 1);
        // Native OS/browser notification when the user has opted in. Prefer the
        // service worker (works across more browsers) and fall back to the
        // page-level Notification constructor.
        if (typeof Notification !== "undefined" && Notification.permission === "granted") {
          const opts = { body: n.body, tag: n.nid } as NotificationOptions;
          if (swReg.current) {
            swReg.current.showNotification(n.title, opts).catch(() => {
              try {
                new Notification(n.title, opts);
              } catch {
                /* ignore */
              }
            });
          } else {
            try {
              new Notification(n.title, opts);
            } catch {
              /* some browsers require a service worker — ignore */
            }
          }
        }
      } catch {
        /* ignore malformed frames */
      }
    };
    source.addEventListener("notification", onNotification);
    return () => {
      source.removeEventListener("notification", onNotification);
      source.close();
    };
  }, []);

  const requestPermission = useCallback(async () => {
    if (typeof Notification === "undefined") {
      setPermission("unsupported");
      return;
    }
    try {
      const result = await Notification.requestPermission();
      setPermission(result as Permission);
    } catch {
      setPermission("denied");
    }
  }, []);

  const markRead = useCallback((id: string) => {
    setNotifications((prev) => prev.map((n) => (n.id === id ? { ...n, read: true } : n)));
    setUnread((u) => Math.max(0, u - 1));
    markNotificationRead(id).catch(() => {});
  }, []);

  const markAllRead = useCallback(() => {
    setNotifications((prev) => prev.map((n) => ({ ...n, read: true })));
    setUnread(0);
    markNotificationRead("all").catch(() => {});
  }, []);

  return (
    <Ctx.Provider value={{ notifications, unread, permission, requestPermission, markRead, markAllRead, refresh }}>
      {children}
    </Ctx.Provider>
  );
}

export function useNotifications() {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useNotifications must be used within NotificationsProvider");
  return ctx;
}
