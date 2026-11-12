import { useAuth } from "../context/AuthContext";
import { ThemeToggle } from "./ThemeToggle";

export function Topbar({ title }: { title: string }) {
  const { signOut } = useAuth();
  return (
    <header className="flex h-14 items-center justify-between border-b border-border bg-canvas px-4 pl-14 md:pl-6">
      <h1 className="text-sm font-medium text-ink-primary">{title}</h1>
      <div className="flex items-center gap-2">
        <ThemeToggle />
        <button
          onClick={signOut}
          className="rounded border border-border-strong px-3 py-1.5 text-xs text-ink-secondary hover:bg-raised hover:text-ink-primary"
        >
          Sign out
        </button>
      </div>
    </header>
  );
}
