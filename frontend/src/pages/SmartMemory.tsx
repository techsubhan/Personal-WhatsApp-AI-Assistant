import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Layout } from "../components/Layout";
import { StatusBadge } from "../components/StatusBadge";
import { TableSkeleton, ErrorState, EmptyState } from "../components/States";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { useApi } from "../lib/useApi";
import { contactsApi } from "../api/contacts";
import { memoryApi } from "../api/memory";
import { formatDateTime, titleCase } from "../lib/format";
import { useToast } from "../context/ToastContext";

const IMPORTANCE_TONE = {
  critical: "danger",
  high: "amber",
  medium: "violet",
  low: "neutral",
} as const;

export function SmartMemory() {
  const [params, setParams] = useSearchParams();
  const selectedWaId = params.get("wa_id");
  const [search, setSearch] = useState("");
  const [confirmDelete, setConfirmDelete] = useState<number | null>(null);
  const { notify } = useToast();

  const { data: contactsData } = useApi(() => contactsApi.list({ page: 1, page_size: 100 }), []);

  const {
    data: memoriesData,
    loading,
    error,
    refetch,
  } = useApi(() => (selectedWaId ? memoryApi.list(selectedWaId, search || undefined) : Promise.resolve(null)), [
    selectedWaId,
    search,
  ]);

  const handleDelete = async () => {
    if (!selectedWaId || confirmDelete === null) return;
    try {
      await memoryApi.remove(selectedWaId, confirmDelete);
      notify("Memory deleted.");
      refetch();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Failed to delete.", "error");
    } finally {
      setConfirmDelete(null);
    }
  };

  const selectedContact = contactsData?.items.find((c) => c.wa_id === selectedWaId);

  return (
    <Layout title="Smart memory">
      {selectedContact && (
        <div className="mb-4 rounded border border-border bg-surface px-4 py-2.5">
          <p className="text-sm text-ink-primary">{selectedContact.display_name || selectedContact.wa_id}</p>
          <p className="font-mono text-xs text-ink-muted">{selectedContact.wa_id}</p>
        </div>
      )}

      <div className="mb-4 flex flex-wrap items-center gap-2">
        <select
          value={selectedWaId || ""}
          onChange={(e) => setParams(e.target.value ? { wa_id: e.target.value } : {})}
          className="w-64 rounded border border-border-strong bg-surface px-3 py-2 text-sm text-ink-primary focus:border-violet focus:outline-none"
        >
          <option value="">Select a contact…</option>
          {contactsData?.items.map((c) => (
            <option key={c.wa_id} value={c.wa_id}>
              {c.display_name || c.wa_id}
            </option>
          ))}
        </select>
        {selectedWaId && (
          <input
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            placeholder="Search facts…"
            className="w-56 rounded border border-border-strong bg-surface px-3 py-2 text-sm text-ink-primary placeholder:text-ink-muted focus:border-violet focus:outline-none"
          />
        )}
      </div>

      {!selectedWaId && (
        <EmptyState title="Pick a contact" description="Select a contact above to view their stored Smart Memory facts." />
      )}

      {selectedWaId && loading && <TableSkeleton />}
      {selectedWaId && error && <ErrorState message={error} onRetry={refetch} />}

      {selectedWaId && memoriesData && memoriesData.items.length === 0 && (
        <EmptyState title="No facts stored" description="Facts the AI learns about this contact will appear here." />
      )}

      {selectedWaId && memoriesData && memoriesData.items.length > 0 && (
        <div className="space-y-2">
          {memoriesData.items.map((m) => {
            const isNamedContact = m.category === "contact" && /\d/.test(m.subcategory);
            return (
              <div
                key={m.id}
                className="flex flex-col gap-3 rounded border border-border bg-surface p-3.5 sm:flex-row sm:items-start sm:justify-between"
              >
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2">
                    {!isNamedContact && (
                      <span className="text-xs font-medium uppercase tracking-wide text-ink-muted">
                        {titleCase(m.category)} · {m.subcategory}
                      </span>
                    )}
                    <StatusBadge label={m.importance} tone={IMPORTANCE_TONE[m.importance]} />
                    {m.expires_at ? (
                      <StatusBadge label="Expires" tone="amber" />
                    ) : (
                      <StatusBadge label="Permanent" tone="signal" />
                    )}
                  </div>

                  {isNamedContact ? (
                    <>
                      <p className="mt-1 text-sm font-medium text-ink-primary">{m.value}</p>
                      <p className="font-mono text-xs text-ink-secondary">{m.subcategory}</p>
                    </>
                  ) : (
                    <p className="mt-1 text-sm text-ink-primary">{m.value}</p>
                  )}

                  <p className="mt-1 text-xs text-ink-muted">
                    Used {m.access_count}x · Updated {formatDateTime(m.updated_at)}
                    {m.expires_at && ` · Expires ${formatDateTime(m.expires_at)}`}
                  </p>
                </div>
                <button
                  onClick={() => setConfirmDelete(m.id)}
                  className="shrink-0 self-start rounded border border-border-strong px-2.5 py-1 text-xs text-ink-secondary hover:bg-raised hover:text-danger"
                >
                  Delete
                </button>
              </div>
            );
          })}
        </div>
      )}

      <ConfirmDialog
        open={confirmDelete !== null}
        title="Delete this memory?"
        description="This fact will no longer be available to the AI for this contact."
        confirmLabel="Delete"
        danger
        onConfirm={handleDelete}
        onCancel={() => setConfirmDelete(null)}
      />
    </Layout>
  );
}
