import { useState } from "react";
import { Layout } from "../components/Layout";
import { StatusBadge } from "../components/StatusBadge";
import { TableSkeleton, ErrorState, EmptyState } from "../components/States";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { useApi } from "../lib/useApi";
import { scheduledMessagesApi } from "../api/scheduledMessages";
import { formatDateTime } from "../lib/format";
import { useToast } from "../context/ToastContext";
import type { ScheduledMessageItem } from "../api/types";

const TONE: Record<ScheduledMessageItem["status"], "amber" | "signal" | "danger" | "neutral"> = {
  pending: "amber",
  sent: "signal",
  failed: "danger",
  cancelled: "neutral",
};

export function ScheduledMessages() {
  const [statusFilter, setStatusFilter] = useState("");
  const { data, loading, error, refetch } = useApi(() => scheduledMessagesApi.list(statusFilter || undefined), [
    statusFilter,
  ]);
  const { notify } = useToast();

  const [showForm, setShowForm] = useState(false);
  const [waId, setWaId] = useState("");
  const [messageText, setMessageText] = useState("");
  const [scheduledAt, setScheduledAt] = useState("");
  const [tz, setTz] = useState(Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC");
  const [creating, setCreating] = useState(false);
  const [confirmCancelId, setConfirmCancelId] = useState<number | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);

  const handleCreate = async () => {
    if (!waId.trim() || !messageText.trim() || !scheduledAt) {
      notify("Contact number, message, and time are all required.", "error");
      return;
    }
    setCreating(true);
    try {
      await scheduledMessagesApi.create({
        contact_wa_id: waId.trim(),
        message_text: messageText.trim(),
        scheduled_at: new Date(scheduledAt).toISOString(),
        timezone: tz,
      });
      notify("Message scheduled.");
      setWaId("");
      setMessageText("");
      setScheduledAt("");
      setShowForm(false);
      refetch();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Failed to schedule.", "error");
    } finally {
      setCreating(false);
    }
  };

  const handleCancel = async () => {
    if (confirmCancelId === null) return;
    setBusyId(confirmCancelId);
    try {
      await scheduledMessagesApi.cancel(confirmCancelId);
      notify("Scheduled message cancelled.");
      refetch();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Failed to cancel.", "error");
    } finally {
      setBusyId(null);
      setConfirmCancelId(null);
    }
  };

  const handleRetry = async (id: number) => {
    setBusyId(id);
    try {
      await scheduledMessagesApi.retry(id);
      notify("Message re-queued to send now.");
      refetch();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Failed to retry.", "error");
    } finally {
      setBusyId(null);
    }
  };

  return (
    <Layout title="Scheduled messages">
      <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="rounded border border-border-strong bg-surface px-3 py-2 text-sm text-ink-primary focus:border-violet focus:outline-none"
        >
          <option value="">All statuses</option>
          <option value="pending">Pending</option>
          <option value="sent">Sent</option>
          <option value="failed">Failed</option>
          <option value="cancelled">Cancelled</option>
        </select>
        <button
          onClick={() => setShowForm((s) => !s)}
          className="rounded bg-signal px-3 py-1.5 text-xs font-medium text-canvas hover:bg-signal/90"
        >
          {showForm ? "Close" : "Schedule a message"}
        </button>
      </div>

      {showForm && (
        <div className="mb-4 rounded border border-border bg-surface p-4">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <label className="block">
              <span className="text-xs text-ink-secondary">Contact WhatsApp number</span>
              <input
                value={waId}
                onChange={(e) => setWaId(e.target.value)}
                placeholder="e.g. 923001234567"
                className="mt-1 w-full rounded border border-border-strong bg-canvas px-3 py-2 text-sm text-ink-primary placeholder:text-ink-muted focus:border-violet focus:outline-none"
              />
            </label>
            <label className="block">
              <span className="text-xs text-ink-secondary">Send at</span>
              <input
                type="datetime-local"
                value={scheduledAt}
                onChange={(e) => setScheduledAt(e.target.value)}
                className="mt-1 w-full rounded border border-border-strong bg-canvas px-3 py-2 text-sm text-ink-primary focus:border-violet focus:outline-none"
              />
            </label>
          </div>
          <label className="mt-3 block">
            <span className="text-xs text-ink-secondary">Message</span>
            <textarea
              value={messageText}
              onChange={(e) => setMessageText(e.target.value)}
              rows={3}
              className="mt-1 w-full rounded border border-border-strong bg-canvas px-3 py-2 text-sm text-ink-primary focus:border-violet focus:outline-none"
            />
          </label>
          <p className="mt-2 text-xs text-ink-muted">Timezone: {tz} (your browser's timezone)</p>
          <button
            onClick={handleCreate}
            disabled={creating}
            className="mt-3 rounded bg-signal px-4 py-2 text-sm font-medium text-canvas hover:bg-signal/90 disabled:opacity-50"
          >
            {creating ? "Scheduling…" : "Schedule message"}
          </button>
        </div>
      )}

      {loading && <TableSkeleton />}
      {error && <ErrorState message={error} onRetry={refetch} />}

      {data && data.items.length === 0 && (
        <EmptyState title="No scheduled messages" description="Messages you schedule for a future time will appear here." />
      )}

      {data && data.items.length > 0 && (
        <div className="space-y-2">
          {data.items.map((m) => (
            <div key={m.id} className="rounded border border-border bg-surface p-4">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-mono text-sm text-ink-primary">{m.contact_wa_id}</span>
                    <StatusBadge label={m.status} tone={TONE[m.status]} />
                  </div>
                  <p className="mt-1 whitespace-pre-wrap rounded bg-canvas px-2.5 py-1.5 text-sm text-ink-primary">
                    {m.message_text}
                  </p>
                  <p className="mt-1.5 text-xs text-ink-muted">
                    Scheduled for {formatDateTime(m.scheduled_at)} ({m.timezone})
                    {m.sent_at && ` · Sent ${formatDateTime(m.sent_at)}`}
                    {m.failed_at && ` · Failed ${formatDateTime(m.failed_at)}`}
                    {m.cancelled_at && ` · Cancelled ${formatDateTime(m.cancelled_at)}`}
                  </p>
                  {m.error_detail && <p className="mt-1 text-xs text-danger">{m.error_detail}</p>}
                </div>
                <div className="flex shrink-0 gap-2">
                  {m.status === "pending" && (
                    <button
                      disabled={busyId === m.id}
                      onClick={() => setConfirmCancelId(m.id)}
                      className="rounded border border-border-strong px-3 py-1.5 text-xs text-ink-secondary hover:bg-raised hover:text-danger disabled:opacity-50"
                    >
                      Cancel
                    </button>
                  )}
                  {m.status === "failed" && (
                    <button
                      disabled={busyId === m.id}
                      onClick={() => handleRetry(m.id)}
                      className="rounded border border-border-strong px-3 py-1.5 text-xs text-ink-secondary hover:bg-raised hover:text-ink-primary disabled:opacity-50"
                    >
                      Retry now
                    </button>
                  )}
                </div>
              </div>
            </div>
          ))}
        </div>
      )}

      <ConfirmDialog
        open={confirmCancelId !== null}
        title="Cancel this scheduled message?"
        description="It will not be sent."
        confirmLabel="Cancel message"
        danger
        onConfirm={handleCancel}
        onCancel={() => setConfirmCancelId(null)}
      />
    </Layout>
  );
}
