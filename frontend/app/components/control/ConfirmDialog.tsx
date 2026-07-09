"use client";

import { AnimatePresence, motion } from "framer-motion";
import { AlertTriangle } from "lucide-react";

export default function ConfirmDialog({
  open,
  title,
  body,
  confirmLabel = "Confirm",
  destructive = true,
  onConfirm,
  onCancel,
}: {
  open: boolean;
  title: string;
  body?: string;
  confirmLabel?: string;
  destructive?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <AnimatePresence>
      {open && (
        <motion.div
          className="fixed inset-0 z-[70] flex items-center justify-center bg-black/45 p-4"
          onMouseDown={onCancel}
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
        >
          <motion.div
            className="w-full max-w-sm rounded-2xl border border-border bg-surface-raised p-5 shadow-2xl"
            onMouseDown={(e) => e.stopPropagation()}
            initial={{ scale: 0.96, opacity: 0 }}
            animate={{ scale: 1, opacity: 1 }}
            exit={{ scale: 0.96, opacity: 0 }}
          >
            <div className="mb-3 flex items-start gap-3">
              <span
                className={`flex h-9 w-9 shrink-0 items-center justify-center rounded-full ${
                  destructive ? "bg-danger-bg text-danger" : "bg-accent-soft text-accent"
                }`}
              >
                <AlertTriangle size={17} />
              </span>
              <div>
                <h3 className="text-sm font-semibold text-text-primary">{title}</h3>
                {body && <p className="mt-1 text-sm text-text-secondary">{body}</p>}
              </div>
            </div>
            <div className="mt-4 flex justify-end gap-2">
              <button
                onClick={onCancel}
                className="rounded-lg px-3 py-2 text-sm text-text-secondary transition-colors hover:bg-surface-2"
              >
                Cancel
              </button>
              <button
                onClick={onConfirm}
                className={`rounded-lg px-4 py-2 text-sm font-medium text-white transition-colors ${
                  destructive ? "bg-danger hover:opacity-90" : "bg-accent hover:bg-accent-hover"
                }`}
              >
                {confirmLabel}
              </button>
            </div>
          </motion.div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
