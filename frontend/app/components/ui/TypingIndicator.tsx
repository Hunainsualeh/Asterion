export default function TypingIndicator() {
  return (
    <span className="inline-flex items-center gap-1 py-1">
      <span className="typing-dot h-1.5 w-1.5 rounded-full bg-text-tertiary [animation-delay:0ms]" />
      <span className="typing-dot h-1.5 w-1.5 rounded-full bg-text-tertiary [animation-delay:150ms]" />
      <span className="typing-dot h-1.5 w-1.5 rounded-full bg-text-tertiary [animation-delay:300ms]" />
    </span>
  );
}
