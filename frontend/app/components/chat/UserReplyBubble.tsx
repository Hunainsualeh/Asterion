import type { GateAction } from "@/hooks/usePipelineTimeline";

function phrase(action: GateAction, feedback: string): string {
  switch (action) {
    case "approve":
      return feedback ? `Approved — ${feedback}` : "Approved";
    case "reject":
      return `Sent back for changes: ${feedback}`;
    case "pass":
      return feedback ? `It works — ${feedback}` : "It works!";
    case "fail":
      return `Didn't work: ${feedback}`;
    case "clarify":
    default:
      return feedback;
  }
}

export default function UserReplyBubble({ action, feedback }: { action: GateAction; feedback: string }) {
  return (
    <div className="animate-in flex justify-end">
      <div className="max-w-xl rounded-2xl rounded-tr-sm bg-bubble-user-bg px-4 py-2.5 text-sm leading-relaxed text-bubble-user-text shadow-sm">
        <p className="whitespace-pre-line">{phrase(action, feedback)}</p>
      </div>
    </div>
  );
}
