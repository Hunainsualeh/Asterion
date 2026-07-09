import Card from "@/app/components/ui/Card";
import type { Ticket } from "@/lib/api";
import { TICKET_STATUS_STYLES, ticketStatusLabel } from "@/lib/ticketStatus";

export default function TicketsCard({ tickets, currentIndex }: { tickets: Ticket[]; currentIndex?: number }) {
  if (tickets.length === 0) return null;

  return (
    <div className="ml-11">
      <Card title="Tasks" subtitle={`${tickets.length} planned`} defaultOpen={false} accent="bg-accent/60">
        <ul className="space-y-2">
          {tickets.map((t, i) => (
            <li
              key={t.id}
              className={`flex items-center justify-between rounded-xl border p-3 text-sm ${
                i === currentIndex ? "border-accent/40 bg-accent-soft/40" : "border-border-soft"
              }`}
            >
              <div className="min-w-0">
                <p className="truncate font-medium text-text-primary">{t.title}</p>
                <p className="text-xs text-text-tertiary">effort {t.effort}</p>
              </div>
              <span
                className={`ml-3 shrink-0 rounded-full px-2 py-0.5 text-xs font-medium ${TICKET_STATUS_STYLES[t.status] ?? TICKET_STATUS_STYLES.pending}`}
              >
                {ticketStatusLabel(t.status)}
              </span>
            </li>
          ))}
        </ul>
      </Card>
    </div>
  );
}
