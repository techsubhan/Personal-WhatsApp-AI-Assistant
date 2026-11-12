import { useEffect, useState } from "react";
import { Layout } from "../components/Layout";
import { SignalDot } from "../components/SignalDot";
import { Toggle } from "../components/Toggle";
import { CardSkeleton, ErrorState } from "../components/States";
import { useApi } from "../lib/useApi";
import { settingsApi } from "../api/settings";
import { useToast } from "../context/ToastContext";

const DAYS_NOTE = "Applies every day at the configured window (per-weekday scheduling isn't in the backend yet).";

export function WorkingHours() {
  const { data, loading, error, refetch } = useApi(() => settingsApi.get());
  const { notify } = useToast();
  const [saving, setSaving] = useState(false);

  const [enabled, setEnabled] = useState(false);
  const [startTime, setStartTime] = useState("09:00");
  const [endTime, setEndTime] = useState("18:00");
  const [timezone, setTimezone] = useState("UTC");
  const [action, setAction] = useState("away_message");
  const [awayMessage, setAwayMessage] = useState("");

  useEffect(() => {
    if (!data) return;
    setEnabled(data.working_hours.enabled);
    setStartTime(data.working_hours.start_time);
    setEndTime(data.working_hours.end_time);
    setTimezone(data.working_hours.timezone);
    setAction(data.working_hours.outside_hours_action);
    setAwayMessage(data.working_hours.away_message);
  }, [data]);

  const handleSave = async () => {
    setSaving(true);
    try {
      await settingsApi.patch({
        "working_hours.enabled": enabled,
        "working_hours.start_time": startTime,
        "working_hours.end_time": endTime,
        "working_hours.timezone": timezone,
        "working_hours.outside_hours_action": action,
        "working_hours.away_message": awayMessage,
      });
      notify("Working hours updated.");
      refetch();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Failed to save.", "error");
    } finally {
      setSaving(false);
    }
  };

  return (
    <Layout title="Working hours">
      {loading && <CardSkeleton />}
      {error && <ErrorState message={error} onRetry={refetch} />}

      {data && (
        <div className="max-w-xl space-y-4">
          <div className="flex items-center justify-between rounded border border-border bg-surface p-4">
            <div className="flex items-center gap-3">
              <SignalDot tone={enabled ? "live" : "idle"} />
              <div>
                <p className="text-sm font-medium text-ink-primary">Enforce working hours</p>
                <p className="text-xs text-ink-secondary">
                  {enabled ? "AI applies the schedule below." : "AI responds around the clock."}
                </p>
              </div>
            </div>
            <Toggle checked={enabled} onChange={setEnabled} label="Enforce working hours" />
          </div>

          <div className="rounded border border-border bg-surface p-4">
            <div className="grid grid-cols-2 gap-4">
              <label className="block">
                <span className="text-xs text-ink-secondary">Start time</span>
                <input
                  type="time"
                  value={startTime}
                  onChange={(e) => setStartTime(e.target.value)}
                  className="mt-1 w-full rounded border border-border-strong bg-canvas px-3 py-2 text-sm text-ink-primary focus:border-violet focus:outline-none"
                />
              </label>
              <label className="block">
                <span className="text-xs text-ink-secondary">End time</span>
                <input
                  type="time"
                  value={endTime}
                  onChange={(e) => setEndTime(e.target.value)}
                  className="mt-1 w-full rounded border border-border-strong bg-canvas px-3 py-2 text-sm text-ink-primary focus:border-violet focus:outline-none"
                />
              </label>
            </div>
            <label className="mt-4 block">
              <span className="text-xs text-ink-secondary">Timezone (IANA name)</span>
              <input
                value={timezone}
                onChange={(e) => setTimezone(e.target.value)}
                placeholder="e.g. Asia/Karachi"
                className="mt-1 w-full rounded border border-border-strong bg-canvas px-3 py-2 text-sm text-ink-primary placeholder:text-ink-muted focus:border-violet focus:outline-none"
              />
            </label>
            <label className="mt-4 block">
              <span className="text-xs text-ink-secondary">Outside working hours, the AI should</span>
              <select
                value={action}
                onChange={(e) => setAction(e.target.value)}
                className="mt-1 w-full rounded border border-border-strong bg-canvas px-3 py-2 text-sm text-ink-primary focus:border-violet focus:outline-none"
              >
                <option value="away_message">Send an away message</option>
                <option value="emergency_only">Stay silent, unless an emergency keyword is used</option>
                <option value="no_reply">Stay completely silent</option>
              </select>
            </label>
            {action === "away_message" && (
              <label className="mt-4 block">
                <span className="text-xs text-ink-secondary">Away message</span>
                <textarea
                  value={awayMessage}
                  onChange={(e) => setAwayMessage(e.target.value)}
                  rows={3}
                  className="mt-1 w-full rounded border border-border-strong bg-canvas px-3 py-2 text-sm text-ink-primary focus:border-violet focus:outline-none"
                />
              </label>
            )}
            <p className="mt-3 text-xs text-ink-muted">{DAYS_NOTE}</p>
          </div>

          <button
            onClick={handleSave}
            disabled={saving}
            className="rounded bg-signal px-4 py-2 text-sm font-medium text-canvas hover:bg-signal/90 disabled:opacity-50"
          >
            {saving ? "Saving…" : "Save changes"}
          </button>
        </div>
      )}
    </Layout>
  );
}
