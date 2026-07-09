"use client";

import { useState } from "react";
import { Check, HelpCircle } from "lucide-react";
import Avatar from "@/app/components/ui/Avatar";
import { agentLabel } from "@/app/components/agentTheme";
import type { ClarifyItem } from "@/lib/timeline";

/** Pull inline options out of a question — either parenthetical
 * ("... (database, API, file)?") or a trailing "or" list
 * ("Web page, desktop, or command-line?"). Falls back to free-text only when
 * there's nothing cleanly selectable. */
function parseQuestion(q: string): { text: string; options: string[] } {
  const clean = (s: string) => s.trim().replace(/^["'`]+|["'`]+$/g, "");
  const dedupeShort = (parts: string[]) =>
    Array.from(new Set(parts.map(clean).filter((p) => p.length > 0 && p.length <= 24)));

  // 1) Parenthetical: "... (database, API, file)?"
  const paren = q.match(/\(([^)]+)\)/);
  if (paren) {
    const options = dedupeShort(paren[1].split(/,|\/|\bor\b/i));
    if (options.length >= 2 && options.length <= 6) {
      const text = q
        .replace(paren[0], "")
        .replace(/\s{2,}/g, " ")
        .replace(/\s+([?:.])/g, "$1")
        .trim();
      return { text: text || q, options };
    }
  }

  // 2) Trailing "or" list: "Web page, desktop, or command-line?"
  const stripped = q.replace(/[?.!]+\s*$/, "");
  if (/\bor\b/i.test(stripped)) {
    const parts = stripped
      .split(/,|\bor\b/i)
      .map(clean)
      .filter(Boolean);
    if (parts.length >= 2 && parts.length <= 5 && parts.every((p) => p.split(/\s+/).length <= 3 && p.length <= 24)) {
      return { text: q, options: dedupeShort(parts) };
    }
  }

  return { text: q, options: [] };
}

export default function ClarifyCard({
  item,
  onSend,
  busy,
}: {
  item: ClarifyItem;
  onSend: (text: string) => Promise<boolean>;
  busy: boolean;
}) {
  const parsed = item.questions.map(parseQuestion);
  const [answers, setAnswers] = useState<Record<number, string>>({});
  const [customFor, setCustomFor] = useState<Record<number, boolean>>({});
  const [sending, setSending] = useState(false);

  const allAnswered = parsed.every((_, i) => (answers[i] ?? "").trim().length > 0);

  function choose(i: number, value: string) {
    setAnswers((a) => ({ ...a, [i]: value }));
    setCustomFor((c) => ({ ...c, [i]: false }));
  }

  async function send(text: string) {
    if (sending || busy) return;
    setSending(true);
    const ok = await onSend(text);
    if (!ok) setSending(false); // on success the card goes static via a refresh
  }

  function submitAnswers() {
    const composed = parsed
      .map((p, i) => `- ${p.text} → ${(answers[i] ?? "").trim() || "(you decide)"}`)
      .join("\n");
    send(composed);
  }

  // Static (already answered / not the live one): just show the questions.
  if (!item.live) {
    return (
      <div className="animate-in flex items-start gap-3">
        <Avatar agent={item.agent} />
        <div className="min-w-0 flex-1">
          <span className="text-xs font-semibold text-text-secondary">{agentLabel(item.agent)}</span>
          <div className="mt-1 max-w-xl rounded-2xl rounded-tl-sm border border-bubble-assistant-border bg-bubble-assistant-bg px-4 py-3 text-sm text-text-secondary shadow-sm">
            {item.intro && <p className="mb-1.5 font-medium text-text-primary">{item.intro}</p>}
            <ul className="space-y-1">
              {parsed.map((p, i) => (
                <li key={i} className="flex gap-2">
                  <span className="text-text-tertiary">•</span>
                  <span>{p.text}</span>
                </li>
              ))}
            </ul>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="animate-in flex items-start gap-3">
      <Avatar agent={item.agent} />
      <div className="min-w-0 flex-1">
        <span className="text-xs font-semibold text-text-secondary">{agentLabel(item.agent)}</span>
        <div className="mt-1 max-w-xl rounded-2xl rounded-tl-sm border border-warning-border/60 bg-bubble-assistant-bg px-4 py-3 shadow-sm">
          <div className="mb-3 flex items-start gap-2 text-sm text-text-primary">
            <HelpCircle size={16} className="mt-0.5 shrink-0 text-warning" />
            <p className="font-medium">{item.intro || "A couple of quick questions:"}</p>
          </div>

          <div className="space-y-3.5">
            {parsed.map((p, i) => {
              const selected = answers[i] ?? "";
              const isCustom = customFor[i];
              return (
                <div key={i}>
                  <p className="mb-1.5 text-sm text-text-secondary">{p.text}</p>
                  {p.options.length > 0 && (
                    <div className="flex flex-wrap gap-1.5">
                      {p.options.map((opt) => {
                        const active = !isCustom && selected === opt;
                        return (
                          <button
                            key={opt}
                            type="button"
                            onClick={() => choose(i, opt)}
                            className={`inline-flex items-center gap-1 rounded-full border px-3 py-1 text-xs font-medium transition-colors ${
                              active
                                ? "border-accent bg-accent text-accent-foreground"
                                : "border-border bg-surface text-text-secondary hover:border-accent/40 hover:text-text-primary"
                            }`}
                          >
                            {active && <Check size={12} />}
                            {opt}
                          </button>
                        );
                      })}
                      <button
                        type="button"
                        onClick={() => setCustomFor((c) => ({ ...c, [i]: !c[i] }))}
                        className={`rounded-full border px-3 py-1 text-xs font-medium transition-colors ${
                          isCustom
                            ? "border-accent text-accent"
                            : "border-dashed border-border text-text-tertiary hover:border-accent/40 hover:text-text-primary"
                        }`}
                      >
                        Custom
                      </button>
                    </div>
                  )}
                  {(isCustom || p.options.length === 0) && (
                    <input
                      value={selected}
                      onChange={(e) => setAnswers((a) => ({ ...a, [i]: e.target.value }))}
                      placeholder="Type your answer…"
                      className="mt-1.5 w-full rounded-lg border border-border bg-surface px-3 py-1.5 text-sm text-text-primary placeholder:text-text-tertiary focus:border-accent/50 focus:outline-none"
                    />
                  )}
                </div>
              );
            })}
          </div>

          <div className="mt-4 flex items-center gap-2">
            <button
              type="button"
              onClick={submitAnswers}
              disabled={!allAnswered || sending || busy}
              className="rounded-full bg-accent px-4 py-1.5 text-xs font-semibold text-accent-foreground transition-colors hover:bg-accent/90 disabled:cursor-not-allowed disabled:opacity-50"
            >
              {sending ? "Sending…" : "Send answers"}
            </button>
            <button
              type="button"
              onClick={() => send("you decide")}
              disabled={sending || busy}
              className="rounded-full border border-border px-4 py-1.5 text-xs font-medium text-text-secondary transition-colors hover:text-text-primary disabled:opacity-50"
            >
              You decide
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
