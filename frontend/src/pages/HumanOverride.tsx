import { useState } from "react";
import { Layout } from "../components/Layout";
import { SignalDot } from "../components/SignalDot";
import { TableSkeleton, ErrorState, EmptyState } from "../components/States";
import { useApi } from "../lib/useApi";
import { overridesApi } from "../api/ai";
import { formatDateTime } from "../lib/format";
import { useToast } from "../context/ToastContext";

export function HumanOverride() {
  const { data, loading, error, refetch } = useApi(() => overridesApi.list());
  const { notify } = useToast();
  const [busyWaId, setBusyWaId] = useState<string | null>(null);

  const handleClear = async (waId: string) => {
    setBusyWaId(waId);
    try {
      await overridesApi.clear(waId);
      notify(`Human override cleared for ${waId}.`);
      refetch();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Failed to update.", "error");
    } finally {
      setBusyWaId(null);
    }
  };

  return (
    <Layout title="Human override">
      <p className="mb-4 text-sm text-ink-secondary">
        The AI pauses for a contact when you reply to them manually from WhatsApp, or when they explicitly ask to
        talk to a human/the owner - either way, it stays paused until the override window expires.
      </p>

      {loading && <TableSkeleton />}
      {error && <ErrorState message={error} onRetry={refetch} />}

      {data && data.items.length === 0 && (
        <EmptyState title="No active overrides" description="Contacts currently under manual (human) control will appear here." />
      )}

      {data && data.items.length > 0 && (
        <div className="space-y-2">
          {data.items.map((o) => (
            <div key={o.wa_id} className="flex items-center justify-between rounded border border-border bg-surface p-4">
              <div className="flex items-center gap-3">
                <SignalDot tone={o.active ? "live" : "idle"} pulse={o.active} />
                <div>
                  <p className="text-sm text-ink-primary">{o.display_name || o.wa_id}</p>
                  <p className="text-xs text-ink-muted">
                    {o.active ? "Human control active" : "Expired"}
                    {o.expires_at && ` · until ${formatDateTime(o.expires_at)}`}
                  </p>
                </div>
              </div>
              {o.active && (
                <button
                  disabled={busyWaId === o.wa_id}
                  onClick={() => handleClear(o.wa_id)}
                  className="rounded border border-border-strong px-3 py-1.5 text-xs text-ink-secondary hover:bg-raised hover:text-ink-primary disabled:opacity-50"
                >
                  Hand back to AI
                </button>
              )}
            </div>
          ))}
        </div>
      )}
    </Layout>
  );
}
