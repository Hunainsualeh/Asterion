import { AlertTriangle } from "lucide-react";
import Avatar from "@/app/components/ui/Avatar";
import Button from "@/app/components/ui/Button";
import type { ErrorItem } from "@/lib/timeline";

function timeLabel(ts: number): string {
  return new Date(ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export default function ErrorCard({
  item,
  onRetry,
  busy,
}: {
  item: ErrorItem;
  onRetry: () => void;
  busy: boolean;
}) {
  return (
    <div className="animate-in flex items-start gap-3">
      <Avatar agent="system" />
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-2">
          <span className="text-xs font-semibold text-text-secondary">Assistant</span>
          <span className="text-[11px] text-text-tertiary">{timeLabel(item.ts)}</span>
        </div>
        <div className="mt-1 max-w-xl rounded-2xl rounded-tl-sm border border-danger-border bg-danger-bg px-4 py-3 text-sm leading-relaxed text-text-primary shadow-sm">
          <div className="flex items-start gap-2">
            <AlertTriangle size={16} className="mt-0.5 shrink-0 text-danger" />
            <div>
              <p className="font-medium text-danger">{item.title}</p>
              {item.explanation && <p className="mt-1 text-text-secondary">{item.explanation}</p>}
            </div>
          </div>
          {item.suggestion && <p className="mt-2 text-text-secondary">{item.suggestion}</p>}
          <div className="mt-3 flex items-center gap-3">
            {item.retryable && (
              <Button variant="danger" size="sm" onClick={onRetry} disabled={busy}>
                {busy ? "Retrying..." : "Try again"}
              </Button>
            )}
            {item.reference && <span className="text-[11px] text-text-tertiary">Ref: {item.reference}</span>}
          </div>
        </div>
      </div>
    </div>
  );
}
