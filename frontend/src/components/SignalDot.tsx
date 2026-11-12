type Tone = "live" | "idle" | "warn" | "off";

const TONE_CLASSES: Record<Tone, string> = {
  live: "bg-signal",
  idle: "bg-ink-muted",
  warn: "bg-amber",
  off: "bg-danger",
};

export function SignalDot({ tone, pulse = false }: { tone: Tone; pulse?: boolean }) {
  return (
    <span
      className={`inline-block h-2 w-2 rounded-full ${TONE_CLASSES[tone]} ${
        pulse && tone === "live" ? "signal-dot-live" : ""
      }`}
    />
  );
}
