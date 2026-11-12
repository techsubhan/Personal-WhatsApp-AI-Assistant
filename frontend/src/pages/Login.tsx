import { FormEvent, useState } from "react";
import { useAuth } from "../context/AuthContext";
import { overviewApi } from "../api/overview";
import { setAdminKey } from "../api/client";

export function Login() {
  const { signIn } = useAuth();
  const [key, setKey] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault();
    if (!key.trim()) {
      setError("Enter your admin key.");
      return;
    }
    setChecking(true);
    setError(null);
    // Verify the key by calling a protected endpoint before committing to it.
    setAdminKey(key.trim());
    try {
      await overviewApi.stats();
      signIn(key.trim());
    } catch {
      setError("That key wasn't accepted. Check INTERNAL_API_KEY in your .env.");
    } finally {
      setChecking(false);
    }
  };

  return (
    <div className="flex min-h-screen items-center justify-center bg-canvas px-4">
      <div className="w-full max-w-sm">
        <div className="mb-6 flex items-center gap-2">
          <div className="flex h-8 w-8 items-center justify-center rounded bg-signal-dim text-signal">
            <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
              <path d="M21 12a8 8 0 1 1-3.6-6.7L21 4l-1 4.3A8 8 0 0 1 21 12Z" />
            </svg>
          </div>
          <span className="text-sm font-medium text-ink-primary">WhatsApp AI Assistant</span>
        </div>

        <form onSubmit={handleSubmit} className="rounded border border-border bg-surface p-5">
          <h1 className="text-base font-medium text-ink-primary">Admin sign in</h1>
          <p className="mt-1 text-sm text-ink-secondary">
            Enter the INTERNAL_API_KEY value from your backend's .env file.
          </p>
          <input
            type="password"
            value={key}
            onChange={(e) => setKey(e.target.value)}
            placeholder="Admin key"
            autoFocus
            className="mt-4 w-full rounded border border-border-strong bg-canvas px-3 py-2 text-sm text-ink-primary placeholder:text-ink-muted focus:border-violet focus:outline-none"
          />
          {error && <p className="mt-2 text-sm text-danger">{error}</p>}
          <button
            type="submit"
            disabled={checking}
            className="mt-4 w-full rounded bg-signal py-2 text-sm font-medium text-canvas hover:bg-signal/90 disabled:opacity-50"
          >
            {checking ? "Checking…" : "Sign in"}
          </button>
        </form>
      </div>
    </div>
  );
}
