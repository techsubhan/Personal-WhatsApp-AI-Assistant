import { useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Layout } from "../components/Layout";
import { EmptyState, ErrorState, TableSkeleton } from "../components/States";
import { StatusBadge } from "../components/StatusBadge";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { useApi } from "../lib/useApi";
import { contactsApi } from "../api/contacts";
import { conversationsApi } from "../api/conversations";
import { formatDateTime, timeAgo } from "../lib/format";
import { useToast } from "../context/ToastContext";

export function Conversations() {
  const [params, setParams] = useSearchParams();
  const selectedWaId = params.get("wa_id");
  const [confirmClear, setConfirmClear] = useState(false);
  const { notify } = useToast();

  const { data: contactsData, loading: contactsLoading, error: contactsError } = useApi(
    () => contactsApi.list({ page: 1, page_size: 50 }),
    []
  );

  const {
    data: messagesData,
    loading: messagesLoading,
    error: messagesError,
    refetch: refetchMessages,
  } = useApi(() => (selectedWaId ? conversationsApi.list(selectedWaId) : Promise.resolve(null)), [selectedWaId]);

  const handleClear = async () => {
    if (!selectedWaId) return;
    try {
      const res = await conversationsApi.clear(selectedWaId);
      notify(`Cleared ${res.cleared} message(s).`);
      refetchMessages();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Failed to clear.", "error");
    } finally {
      setConfirmClear(false);
    }
  };

  const selectedContact = contactsData?.items.find((c) => c.wa_id === selectedWaId);

  return (
    <Layout title="Conversations">
      <div className="flex h-[calc(100vh-6.5rem)] gap-4">
        <aside
          className={`w-full shrink-0 overflow-y-auto rounded border border-border md:flex md:w-64 ${
            selectedWaId ? "hidden" : "flex flex-col"
          }`}
        >
          {contactsLoading && (
            <div className="p-3">
              <TableSkeleton rows={8} />
            </div>
          )}
          {contactsError && <ErrorState message={contactsError} />}
          {contactsData?.items.map((c) => (
            <button
              key={c.wa_id}
              onClick={() => setParams({ wa_id: c.wa_id })}
              className={`flex w-full flex-col border-b border-border px-3 py-2.5 text-left last:border-0 hover:bg-raised/50 ${
                selectedWaId === c.wa_id ? "bg-raised" : ""
              }`}
            >
              <span className="text-sm text-ink-primary">{c.display_name || c.wa_id}</span>
              <span className="text-xs text-ink-muted">{timeAgo(c.last_message_at)}</span>
            </button>
          ))}
        </aside>

        <section
          className={`flex-1 flex-col overflow-hidden rounded border border-border md:flex ${
            selectedWaId ? "flex" : "hidden"
          }`}
        >
          {!selectedWaId && (
            <div className="hidden flex-1 items-center justify-center md:flex">
              <EmptyState title="Select a conversation" description="Pick a contact on the left to view their message history." />
            </div>
          )}

          {selectedWaId && (
            <>
              <div className="flex items-center justify-between gap-2 border-b border-border bg-surface px-4 py-2.5">
                <div className="flex min-w-0 items-center gap-2">
                  <button
                    onClick={() => setParams({})}
                    aria-label="Back to conversation list"
                    className="shrink-0 text-ink-secondary hover:text-ink-primary md:hidden"
                  >
                    <svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8">
                      <path d="m15 6-6 6 6 6" />
                    </svg>
                  </button>
                  <div className="min-w-0">
                    <p className="truncate text-sm font-medium text-ink-primary">
                      {selectedContact?.display_name || selectedWaId}
                    </p>
                    <p className="font-mono text-xs text-ink-muted">{selectedWaId}</p>
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  {selectedContact?.human_override_active && <StatusBadge label="Human active" tone="violet" />}
                  <button
                    onClick={() => setConfirmClear(true)}
                    className="rounded border border-border-strong px-2.5 py-1 text-xs text-ink-secondary hover:bg-raised hover:text-danger"
                  >
                    Clear history
                  </button>
                </div>
              </div>

              <div className="flex-1 overflow-y-auto p-4">
                {messagesLoading && <TableSkeleton rows={5} />}
                {messagesError && <ErrorState message={messagesError} />}
                {messagesData && messagesData.items.length === 0 && (
                  <EmptyState title="No messages yet" description="Conversation history will appear here once messages are exchanged." />
                )}
                {messagesData && (
                  <div className="flex flex-col gap-2">
                    {messagesData.items.map((m) => {
                      const fromUser = m.role === "user";
                      return (
                        <div key={m.id} className={`flex ${fromUser ? "justify-start" : "justify-end"}`}>
                          <div
                            className={`max-w-[75%] rounded px-3 py-2 text-sm ${
                              fromUser ? "bg-raised text-ink-primary" : "bg-violet-dim text-ink-primary"
                            }`}
                          >
                            <p className="whitespace-pre-wrap">{m.content}</p>
                            <p className="mt-1 text-[11px] text-ink-muted">
                              {m.role === "owner" ? "Owner" : titleRole(m.role)} · {formatDateTime(m.created_at)}
                            </p>
                          </div>
                        </div>
                      );
                    })}
                  </div>
                )}
              </div>
            </>
          )}
        </section>
      </div>

      <ConfirmDialog
        open={confirmClear}
        title="Clear conversation history?"
        description="This permanently deletes all stored messages for this contact. Smart Memory facts are not affected."
        confirmLabel="Clear"
        danger
        onConfirm={handleClear}
        onCancel={() => setConfirmClear(false)}
      />
    </Layout>
  );
}

function titleRole(role: string) {
  if (role === "assistant") return "AI";
  if (role === "user") return "Contact";
  return role;
}
