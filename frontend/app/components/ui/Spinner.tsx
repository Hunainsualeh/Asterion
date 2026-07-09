export default function Spinner({ size = 14 }: { size?: number }) {
  return (
    <span
      className="inline-block shrink-0 animate-spin rounded-full border-2 border-border border-t-accent"
      style={{ width: size, height: size }}
    />
  );
}
