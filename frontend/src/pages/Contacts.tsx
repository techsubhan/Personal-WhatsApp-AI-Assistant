import { useState } from "react";
import { Link } from "react-router-dom";
import { Layout } from "../components/Layout";
import { StatusBadge } from "../components/StatusBadge";
import { Toggle } from "../components/Toggle";
import { TableSkeleton, ErrorState, EmptyState } from "../components/States";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { useApi } from "../lib/useApi";
import { contactsApi } from "../api/contacts";
import { timeAgo, titleCase } from "../lib/format";
import { useToast } from "../context/ToastContext";

export function Contacts() {
  const [search, setSearch] = useState("");
  const [statusFilter, setStatusFilter] = useState("");
  const [page, setPage] = useState(1);
  const [confirmTarget, setConfirmTarget] = useState<{ waId: string; blocked: boolean } | null>(null);
  const { notify } = useToast();

  const { data, loading, error, refetch } = useApi(
    () => contactsApi.list({ search: search || undefined, status: statusFilter || undefined, page, page_size: 20 }),
    [search, statusFilter, page]
  );

  const handleAiToggle = async (waId: string, enabled: boolean) => {
    try {
      await contactsApi.setAiEnabled(waId, enabled);
      notify(`AI ${enabled ? "enabled" : "disabled"} for ${waId}`);
      refetch();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Failed to update.", "error");
    }
  };

  const handleWebSearchToggle = async (waId: string, enabled: boolean) => {
    try {
      await contactsApi.setWebSearchEnabled(waId, enabled);
      notify(`Web search ${enabled ? "enabled" : "disabled"} for ${waId}`);
      refetch();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Failed to update.", "error");
    }
  };

  const handleBlockConfirmed = async () => {
    if (!confirmTarget) return;
    try {
      await contactsApi.setBlocked(confirmTarget.waId, confirmTarget.blocked);
      notify(`Contact ${confirmTarget.blocked ? "blocked" : "unblocked"}.`);
      refetch();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Failed to update.", "error");
    } finally {
      setConfirmTarget(null);
    }
  };

  return (
    <Layout title="Contacts">
      <div className="mb-4 flex flex-wrap items-center gap-2">
        <input
          value={search}
          onChange={(e) => {
            setPage(1);
            setSearch(e.target.value);
          }}
          placeholder="Search by name or number…"
          className="w-64 rounded border border-border-strong bg-surface px-3 py-2 text-sm text-ink-primary placeholder:text-ink-muted focus:border-violet focus:outline-none"
        />
        <select
          value={statusFilter}
          onChange={(e) => {
            setPage(1);
            setStatusFilter(e.target.value);
          }}
          className="rounded border border-border-strong bg-surface px-3 py-2 text-sm text-ink-primary focus:border-violet focus:outline-none"
        >
          <option value="">All statuses</option>
          <option value="saved">Saved</option>
          <option value="unknown">Unknown</option>
          <option value="blocked">Blocked</option>
        </select>
      </div>

      {loading && <TableSkeleton />}
      {error && <ErrorState message={error} onRetry={refetch} />}

      {data && data.items.length === 0 && (
        <EmptyState title="No contacts found" description="Contacts appear here once someone messages your WhatsApp number." />
      )}

      {data && data.items.length > 0 && (
        <div className="overflow-x-auto rounded border border-border">
          <table className="w-full text-left text-sm">
            <thead className="border-b border-border bg-surface text-xs uppercase tracking-wide text-ink-secondary">
              <tr>
                <th className="px-4 py-2.5 font-medium">Contact</th>
                <th className="px-4 py-2.5 font-medium">Status</th>
                <th className="px-4 py-2.5 font-medium">Language</th>
                <th className="px-4 py-2.5 font-medium">AI</th>
                <th className="px-4 py-2.5 font-medium">Web search</th>
                <th className="px-4 py-2.5 font-medium">Last activity</th>
                <th className="px-4 py-2.5 font-medium text-right">Actions</th>
              </tr>
            </thead>
            <tbody>
              {data.items.map((c) => (
                <tr key={c.wa_id} className="border-b border-border last:border-0 hover:bg-raised/40">
                  <td className="px-4 py-2.5">
                    <Link to={`/conversations?wa_id=${c.wa_id}`} className="text-ink-primary hover:text-violet">
                      {c.display_name || c.wa_id}
                    </Link>
                    {c.display_name && <p className="font-mono text-xs text-ink-muted">{c.wa_id}</p>}
                  </td>
                  <td className="px-4 py-2.5">
                    <StatusBadge
                      label={c.is_blocked ? "Blocked" : c.status}
                      tone={c.is_blocked ? "danger" : c.status === "saved" ? "signal" : "neutral"}
                    />
                    {c.human_override_active && (
                      <span className="ml-1.5">
                        <StatusBadge label="Human active" tone="violet" />
                      </span>
                    )}
                  </td>
                  <td className="px-4 py-2.5 text-ink-secondary">
                    {c.detected_language ? titleCase(c.detected_language) : "—"}
                  </td>
                  <td className="px-4 py-2.5">
                    <Toggle checked={c.ai_enabled} onChange={(v) => handleAiToggle(c.wa_id, v)} label="Toggle AI" />
                  </td>
                  <td className="px-4 py-2.5">
                    <Toggle
                      checked={c.web_search_enabled}
                      onChange={(v) => handleWebSearchToggle(c.wa_id, v)}
                      label="Toggle web search"
                    />
                  </td>
                  <td className="px-4 py-2.5 text-ink-secondary">{timeAgo(c.last_message_at)}</td>
                  <td className="px-4 py-2.5 text-right">
                    <button
                      onClick={() => setConfirmTarget({ waId: c.wa_id, blocked: !c.is_blocked })}
                      className="rounded border border-border-strong px-2.5 py-1 text-xs text-ink-secondary hover:bg-raised hover:text-ink-primary"
                    >
                      {c.is_blocked ? "Unblock" : "Block"}
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {data && data.total > data.page_size && (
        <div className="mt-4 flex items-center justify-between text-sm text-ink-secondary">
          <span>
            Page {data.page} of {Math.ceil(data.total / data.page_size)}
          </span>
          <div className="flex gap-2">
            <button
              disabled={page <= 1}
              onClick={() => setPage((p) => p - 1)}
              className="rounded border border-border-strong px-3 py-1 disabled:opacity-40"
            >
              Previous
            </button>
            <button
              disabled={page >= Math.ceil(data.total / data.page_size)}
              onClick={() => setPage((p) => p + 1)}
              className="rounded border border-border-strong px-3 py-1 disabled:opacity-40"
            >
              Next
            </button>
          </div>
        </div>
      )}

      <ConfirmDialog
        open={!!confirmTarget}
        title={confirmTarget?.blocked ? "Block this contact?" : "Unblock this contact?"}
        description={
          confirmTarget?.blocked
            ? "The AI will stop responding to this contact entirely until unblocked."
            : "This contact will be able to message the assistant again."
        }
        confirmLabel={confirmTarget?.blocked ? "Block" : "Unblock"}
        danger={!!confirmTarget?.blocked}
        onConfirm={handleBlockConfirmed}
        onCancel={() => setConfirmTarget(null)}
      />
    </Layout>
  );
}
