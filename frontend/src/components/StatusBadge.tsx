type Tone = "signal" | "amber" | "danger" | "violet" | "neutral";

const TONE_CLASSES: Record<Tone, string> = {
  signal: "bg-signal-dim text-signal border-signal/30",
  amber: "bg-amber-dim text-amber border-amber/30",
  danger: "bg-danger-dim text-danger border-danger/30",
  violet: "bg-violet-dim text-violet border-violet/30",
  neutral: "bg-raised text-ink-secondary border-border-strong",
};

export function StatusBadge({ label, tone }: { label: string; tone: Tone }) {
  return (
    <span
      className={`inline-flex items-center rounded border px-2 py-0.5 text-xs font-medium ${TONE_CLASSES[tone]}`}
    >
      {label}
    </span>
  );
}
