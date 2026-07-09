export const TICKET_STATUS_STYLES: Record<string, string> = {
  pending: "bg-surface-2 text-text-tertiary",
  in_review: "bg-orange-500/10 text-orange-600 dark:text-orange-400",
  needs_fix: "bg-danger-bg text-danger",
  ready_for_test: "bg-purple-500/10 text-purple-600 dark:text-purple-400",
  failed: "bg-danger-bg text-danger",
  passed: "bg-success-bg text-success",
};

export function ticketStatusLabel(status: string): string {
  return status.replace(/_/g, " ");
}
