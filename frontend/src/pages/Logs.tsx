import { useState } from "react";
import { Layout } from "../components/Layout";
import { StatusBadge } from "../components/StatusBadge";
import { TableSkeleton, ErrorState, EmptyState } from "../components/States";
import { useApi } from "../lib/useApi";
import { logsApi } from "../api/logs";
import { formatDateTime } from "../lib/format";

export function Logs() {
  const [tab, setTab] = useState<"admin" | "requests">("admin");
  const { data: adminData, loading: adminLoading, error: adminError, refetch: refetchAdmin } = useApi(
    () => logsApi.adminActions(150),
    []
  );
  const { data: reqData, loading: reqLoading, error: reqError, refetch: refetchReq } = useApi(
    () => logsApi.requests(150),
    []
  );

  return (
    <Layout title="Activity log">
      <div className="mb-4 flex gap-2">
        <button
          onClick={() => setTab("admin")}
          className={`rounded px-3 py-1.5 text-sm ${tab === "admin" ? "bg-raised text-ink-primary" : "text-ink-secondary hover:bg-raised/50"}`}
        >
          Admin actions
        </button>
        <button
          onClick={() => setTab("requests")}
          className={`rounded px-3 py-1.5 text-sm ${tab === "requests" ? "bg-raised text-ink-primary" : "text-ink-secondary hover:bg-raised/50"}`}
        >
          Message requests
        </button>
      </div>

      {tab === "admin" && (
        <>
          {adminLoading && <TableSkeleton />}
          {adminError && <ErrorState message={adminError} onRetry={refetchAdmin} />}
          {adminData && adminData.items.length === 0 && <EmptyState title="No admin actions yet" />}
          {adminData && adminData.items.length > 0 && (
            <div className="overflow-x-auto rounded border border-border">
              <table className="w-full text-left text-sm">
                <thead className="border-b border-border bg-surface text-xs uppercase tracking-wide text-ink-secondary">
                  <tr>
                    <th className="px-4 py-2.5 font-medium">Time</th>
                    <th className="px-4 py-2.5 font-medium">Actor</th>
                    <th className="px-4 py-2.5 font-medium">Command</th>
                    <th className="px-4 py-2.5 font-medium">Target</th>
                    <th className="px-4 py-2.5 font-medium">Result</th>
                  </tr>
                </thead>
                <tbody>
                  {adminData.items.map((l) => (
                    <tr key={l.id} className="border-b border-border font-mono text-xs last:border-0 hover:bg-raised/40">
                      <td className="px-4 py-2 text-ink-secondary">{formatDateTime(l.created_at)}</td>
                      <td className="px-4 py-2 text-ink-primary">{l.admin_wa_id}</td>
                      <td className="px-4 py-2 text-ink-primary">{l.command}</td>
                      <td className="px-4 py-2 text-ink-secondary">{l.target_wa_id || "—"}</td>
                      <td className="px-4 py-2 text-ink-secondary">{l.result}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}

      {tab === "requests" && (
        <>
          {reqLoading && <TableSkeleton />}
          {reqError && <ErrorState message={reqError} onRetry={refetchReq} />}
          {reqData && reqData.items.length === 0 && <EmptyState title="No request logs yet" />}
          {reqData && reqData.items.length > 0 && (
            <div className="overflow-x-auto rounded border border-border">
              <table className="w-full text-left text-sm">
                <thead className="border-b border-border bg-surface text-xs uppercase tracking-wide text-ink-secondary">
                  <tr>
                    <th className="px-4 py-2.5 font-medium">Time</th>
                    <th className="px-4 py-2.5 font-medium">Contact</th>
                    <th className="px-4 py-2.5 font-medium">Status</th>
                    <th className="px-4 py-2.5 font-medium">Detail</th>
                    <th className="px-4 py-2.5 font-medium">Latency</th>
                  </tr>
                </thead>
                <tbody>
                  {reqData.items.map((l) => (
                    <tr key={l.id} className="border-b border-border text-xs last:border-0 hover:bg-raised/40">
                      <td className="px-4 py-2 font-mono text-ink-secondary">{formatDateTime(l.created_at)}</td>
                      <td className="px-4 py-2 font-mono text-ink-primary">{l.contact_wa_id || "—"}</td>
                      <td className="px-4 py-2">
                        <StatusBadge
                          label={l.status}
                          tone={l.status === "success" ? "signal" : l.status === "error" ? "danger" : "neutral"}
                        />
                      </td>
                      <td className="px-4 py-2 text-ink-secondary">{l.detail || "—"}</td>
                      <td className="px-4 py-2 font-mono text-ink-secondary">
                        {l.response_time_ms != null ? `${l.response_time_ms}ms` : "—"}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </>
      )}
    </Layout>
  );
}
