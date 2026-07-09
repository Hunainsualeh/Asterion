import {
  Bot,
  Bug,
  ClipboardCheck,
  Code2,
  Compass,
  Eye,
  ListChecks,
  NotebookText,
  Palette,
  Ruler,
  Search,
  User,
  type LucideIcon,
} from "lucide-react";

export const AGENT_LABELS: Record<string, string> = {
  scope: "Scope Discovery",
  architect: "Architecture Designer",
  planner: "Project Planner",
  developer: "Software Engineer",
  reviewer: "Code Reviewer",
  debugger: "Debugger",
  docs: "Docs",
  security: "Security Scan",
  test: "Test Runner",
  supervisor: "Supervisor",
  orchestrator: "Orchestrator",
  research: "Researcher",
  analyze: "Analyst",
  summarize: "Synthesizer",
  answer: "Assistant",
  weather: "Weather",
  code: "Software Engineer",
  code_plan: "Tech Lead",
  code_review: "QA Engineer",
  designer: "UI/UX Designer",
  sandbox: "Sandbox",
  human: "You",
  system: "Assistant",
};

const AGENT_ICONS: Record<string, LucideIcon> = {
  scope: Compass,
  architect: Ruler,
  planner: ListChecks,
  developer: Code2,
  reviewer: Eye,
  debugger: Bug,
  docs: NotebookText,
  research: Search,
  code: Code2,
  code_plan: ListChecks,
  code_review: ClipboardCheck,
  designer: Palette,
  human: User,
  system: Bot,
};

/** Avatar background + icon color, per agent. Kept separate from the message
 * "tone" (info/waiting/success/error), which is about the event, not who sent it. */
const AGENT_AVATAR_CLASSES: Record<string, string> = {
  scope: "bg-sky-500/15 text-sky-600 dark:text-sky-300",
  architect: "bg-blue-500/15 text-blue-600 dark:text-blue-300",
  planner: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-300",
  developer: "bg-orange-500/15 text-orange-600 dark:text-orange-300",
  reviewer: "bg-purple-500/15 text-purple-600 dark:text-purple-300",
  debugger: "bg-rose-500/15 text-rose-600 dark:text-rose-300",
  docs: "bg-zinc-500/15 text-zinc-600 dark:text-zinc-300",
  research: "bg-sky-500/15 text-sky-600 dark:text-sky-300",
  code: "bg-orange-500/15 text-orange-600 dark:text-orange-300",
  code_plan: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-300",
  code_review: "bg-purple-500/15 text-purple-600 dark:text-purple-300",
  designer: "bg-pink-500/15 text-pink-600 dark:text-pink-300",
  human: "bg-accent/15 text-accent",
  system: "bg-accent/15 text-accent",
};

export function agentLabel(agent: string): string {
  return AGENT_LABELS[agent] ?? "Assistant";
}

export function agentIcon(agent: string): LucideIcon {
  return AGENT_ICONS[agent] ?? Bot;
}

export function agentAvatarClass(agent: string): string {
  return AGENT_AVATAR_CLASSES[agent] ?? AGENT_AVATAR_CLASSES.system;
}
