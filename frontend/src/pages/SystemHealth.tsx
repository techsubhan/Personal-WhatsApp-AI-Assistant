import { Layout } from "../components/Layout";
import { SignalDot } from "../components/SignalDot";
import { CardSkeleton, ErrorState } from "../components/States";
import { useApi } from "../lib/useApi";
import { overviewApi } from "../api/overview";

function Row({ label, ok, detail }: { label: string; ok: boolean; detail?: string }) {
  return (
    <div className="flex items-center justify-between border-b border-border px-4 py-3 last:border-0">
      <div className="flex items-center gap-3">
        <SignalDot tone={ok ? "live" : "off"} />
        <span className="text-sm text-ink-primary">{label}</span>
      </div>
      <span className={`text-sm ${ok ? "text-signal" : "text-danger"}`}>{ok ? "Healthy" : "Error"}</span>
      {detail && <span className="ml-3 hidden text-xs text-ink-muted md:inline">{detail}</span>}
    </div>
  );
}

export function SystemHealth() {
  const { data, loading, error, refetch } = useApi(() => overviewApi.fullStatus());

  return (
    <Layout title="System health">
      {loading && <CardSkeleton />}
      {error && <ErrorState message={error} onRetry={refetch} />}

      {data && (
        <div className="max-w-xl">
          <div className="mb-4 flex items-center gap-2 rounded border border-border bg-surface px-4 py-3">
            <SignalDot tone={data.overall_status === "ok" ? "live" : "warn"} pulse />
            <p className="text-sm text-ink-primary">
              Overall status: <span className="font-medium">{data.overall_status === "ok" ? "All systems operational" : "Degraded"}</span>
            </p>
          </div>

          <div className="rounded border border-border bg-surface">
            <Row label="Application" ok={data.application.status === "ok"} detail={`up ${Math.floor(data.application.uptime_seconds / 60)}m`} />
            <Row label="Database" ok={data.database.status === "ok"} />
            <Row label="AI provider (OpenRouter)" ok={data.openrouter.status === "ok"} detail={data.openrouter.primary_model} />
            <Row label="WhatsApp Cloud API" ok={data.whatsapp.status === "ok"} />
          </div>

          <p className="mt-4 text-xs text-ink-muted">Environment: {data.application.environment}</p>
        </div>
      )}
    </Layout>
  );
}
