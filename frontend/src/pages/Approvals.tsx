import { useState } from "react";
import { Layout } from "../components/Layout";
import { StatusBadge } from "../components/StatusBadge";
import { TableSkeleton, ErrorState, EmptyState } from "../components/States";
import { useApi } from "../lib/useApi";
import { approvalsApi } from "../api/approvals";
import { formatDateTime } from "../lib/format";
import { useToast } from "../context/ToastContext";
import type { ApprovalRequestItem } from "../api/types";

const TONE: Record<ApprovalRequestItem["status"], "amber" | "signal" | "danger" | "neutral"> = {
  pending: "amber",
  approved: "signal",
  rejected: "danger",
  expired: "neutral",
};

export function Approvals() {
  const [statusFilter, setStatusFilter] = useState("pending");
  const { data, loading, error, refetch } = useApi(() => approvalsApi.list(statusFilter || undefined), [statusFilter]);
  const { notify } = useToast();
  const [resolvingId, setResolvingId] = useState<number | null>(null);

  const handleResolve = async (id: number, approve: boolean) => {
    setResolvingId(id);
    try {
      const res = await approvalsApi.resolve(id, approve);
      const detail =
        res.action_result === "ai_reply_sent"
          ? "AI reply sent to the contact."
          : res.action_result === "rejection_sent"
          ? "Rejection sent to the contact."
          : "";
      notify(`Request #${id} ${approve ? "approved" : "rejected"}. ${detail}`.trim());
      refetch();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Failed to resolve.", "error");
    } finally {
      setResolvingId(null);
    }
  };

  return (
    <Layout title="Approvals">
      <div className="mb-4">
        <select
          value={statusFilter}
          onChange={(e) => setStatusFilter(e.target.value)}
          className="rounded border border-border-strong bg-surface px-3 py-2 text-sm text-ink-primary focus:border-violet focus:outline-none"
        >
          <option value="pending">Pending</option>
          <option value="approved">Approved</option>
          <option value="rejected">Rejected</option>
          <option value="expired">Expired</option>
          <option value="">All</option>
        </select>
      </div>

      {loading && <TableSkeleton />}
      {error && <ErrorState message={error} onRetry={refetch} />}

      {data && data.items.length === 0 && (
        <EmptyState title="No approval requests" description="Sensitive requests that need your sign-off will show up here." />
      )}

      {data && data.items.length > 0 && (
        <div className="space-y-3">
          {data.items.map((r) => (
            <div key={r.id} className="rounded border border-border bg-surface p-4">
              <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="text-sm font-medium text-ink-primary">#{r.id}</span>
                    <StatusBadge label={r.status} tone={TONE[r.status]} />
                  </div>
                  <p className="mt-1 text-sm text-ink-primary">
                    {r.contact_name || "Unknown"}
                    {r.wa_id && <span className="ml-2 font-mono text-xs text-ink-muted">{r.wa_id}</span>}
                  </p>
                  <p className="mt-2 text-xs text-ink-secondary">Reason: {r.reason}</p>
                  <p className="mt-1 whitespace-pre-wrap rounded bg-canvas px-2.5 py-1.5 text-sm text-ink-primary">
                    "{r.user_message}"
                  </p>
                  <p className="mt-1.5 text-xs text-ink-muted">
                    Created {formatDateTime(r.created_at)}
                    {r.expires_at && ` · Expires ${formatDateTime(r.expires_at)}`}
                    {r.resolved_at && ` · Resolved ${formatDateTime(r.resolved_at)}`}
                  </p>
                  <p className="mt-1 text-xs text-ink-secondary">Action: {r.action_summary}</p>
                </div>
                {r.status === "pending" && (
                  <div className="flex shrink-0 gap-2">
                    <button
                      disabled={resolvingId === r.id}
                      onClick={() => handleResolve(r.id, true)}
                      className="rounded bg-signal px-3 py-1.5 text-xs font-medium text-canvas hover:bg-signal/90 disabled:opacity-50"
                    >
                      Approve
                    </button>
                    <button
                      disabled={resolvingId === r.id}
                      onClick={() => handleResolve(r.id, false)}
                      className="rounded border border-danger/40 px-3 py-1.5 text-xs font-medium text-danger hover:bg-danger-dim disabled:opacity-50"
                    >
                      Reject
                    </button>
                  </div>
                )}
              </div>
            </div>
          ))}
        </div>
      )}
    </Layout>
  );
}
