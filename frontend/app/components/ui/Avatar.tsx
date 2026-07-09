import { agentAvatarClass, agentIcon } from "@/app/components/agentTheme";

export default function Avatar({ agent, size = 32 }: { agent: string; size?: number }) {
  const Icon = agentIcon(agent);
  return (
    <span
      className={`flex shrink-0 items-center justify-center rounded-full ${agentAvatarClass(agent)}`}
      style={{ width: size, height: size }}
    >
      <Icon size={Math.round(size * 0.55)} strokeWidth={2.25} />
    </span>
  );
}
