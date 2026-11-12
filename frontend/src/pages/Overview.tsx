import { Layout } from "../components/Layout";
import { StatCard } from "../components/StatCard";
import { CardSkeleton, ErrorState } from "../components/States";
import { SignalDot } from "../components/SignalDot";
import { useApi } from "../lib/useApi";
import { overviewApi } from "../api/overview";

export function Overview() {
  const { data, loading, error, refetch } = useApi(() => overviewApi.stats());

  return (
    <Layout title="Dashboard">
      <p className="mb-5 text-sm text-ink-secondary">What's happening in your WhatsApp AI Assistant right now.</p>

      {loading && (
        <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
          {Array.from({ length: 8 }).map((_, i) => (
            <CardSkeleton key={i} />
          ))}
        </div>
      )}

      {error && <ErrorState message={error} onRetry={refetch} />}

      {data && (
        <>
          <div className="mb-6 flex items-center gap-2 rounded border border-border bg-surface px-4 py-3">
            <SignalDot tone={data.ai.global_ai_enabled ? "live" : "off"} pulse />
            <p className="text-sm text-ink-primary">
              AI is currently <span className="font-medium">{data.ai.global_ai_enabled ? "active" : "paused"}</span>
              {data.ai.working_hours_enabled && " · working hours enforcement is on"}
            </p>
          </div>

          <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
            <StatCard label="Total contacts" value={data.contacts.total} />
            <StatCard label="Saved contacts" value={data.contacts.saved} />
            <StatCard label="Blocked contacts" value={data.contacts.blocked} />
            <StatCard label="AI disabled (per-contact)" value={data.contacts.ai_disabled} />
            <StatCard label="Total messages" value={data.messages.total} />
            <StatCard label="Pending approvals" value={data.approvals.pending} />
            <StatCard label="Active human overrides" value={data.human_override.active} />
            <StatCard label="Smart memory facts" value={data.smart_memory.total_active_facts} />
          </div>
        </>
      )}
    </Layout>
  );
}
