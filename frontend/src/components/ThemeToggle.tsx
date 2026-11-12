import { useTheme } from "../context/ThemeContext";

const OPTIONS: { value: "light" | "dark" | "system"; label: string; icon: string }[] = [
  { value: "light", label: "Light", icon: "sun" },
  { value: "dark", label: "Dark", icon: "moon" },
  { value: "system", label: "System", icon: "system" },
];

function Icon({ name }: { name: string }) {
  const common = "h-3.5 w-3.5";
  if (name === "sun")
    return (
      <svg className={common} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
        <circle cx="12" cy="12" r="4" />
        <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
      </svg>
    );
  if (name === "moon")
    return (
      <svg className={common} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
        <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8Z" />
      </svg>
    );
  return (
    <svg className={common} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
      <rect x="3" y="4" width="18" height="12" rx="1.5" />
      <path d="M8 20h8M12 16v4" />
    </svg>
  );
}

export function ThemeToggle() {
  const { mode, setMode } = useTheme();
  return (
    <div className="flex items-center rounded border border-border-strong bg-surface p-0.5">
      {OPTIONS.map((opt) => (
        <button
          key={opt.value}
          onClick={() => setMode(opt.value)}
          aria-label={`Use ${opt.label.toLowerCase()} theme`}
          aria-pressed={mode === opt.value}
          className={`flex h-7 w-7 items-center justify-center rounded transition-colors ${
            mode === opt.value ? "bg-raised text-ink-primary" : "text-ink-muted hover:text-ink-secondary"
          }`}
        >
          <Icon name={opt.icon} />
        </button>
      ))}
    </div>
  );
}
