import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Layout } from "../components/Layout";
import { Toggle } from "../components/Toggle";
import { CardSkeleton, ErrorState } from "../components/States";
import { useApi } from "../lib/useApi";
import { settingsApi } from "../api/settings";
import { useToast } from "../context/ToastContext";

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded border border-border bg-surface p-4">
      <h2 className="mb-3 text-sm font-medium text-ink-primary">{title}</h2>
      {children}
    </div>
  );
}

export function Settings() {
  const { data, loading, error, refetch } = useApi(() => settingsApi.get());
  const { data: profile, refetch: refetchProfile } = useApi(() => settingsApi.getProfile());
  const { notify } = useToast();

  const [welcomeMessage, setWelcomeMessage] = useState("");
  const [autoSave, setAutoSave] = useState(true);
  const [requireApproval, setRequireApproval] = useState(true);
  const [approvalTimeout, setApprovalTimeout] = useState(15);
  const [savingGeneral, setSavingGeneral] = useState(false);

  const [ownerName, setOwnerName] = useState("");
  const [ownerProfession, setOwnerProfession] = useState("");
  const [writingStyle, setWritingStyle] = useState("");
  const [savingProfile, setSavingProfile] = useState(false);
  const [reloading, setReloading] = useState(false);

  useEffect(() => {
    if (!data) return;
    setWelcomeMessage(data.welcome_message);
    setAutoSave(data.auto_save_unknown_contacts);
    setRequireApproval(data.approval.require_owner_approval_for_sensitive);
    setApprovalTimeout(data.approval.approval_timeout_minutes);
  }, [data]);

  useEffect(() => {
    if (!profile) return;
    setOwnerName(profile.name);
    setOwnerProfession(profile.profession);
    setWritingStyle(profile.writing_style);
  }, [profile]);

  const saveGeneral = async () => {
    setSavingGeneral(true);
    try {
      await settingsApi.patch({
        welcome_message: welcomeMessage,
        auto_save_unknown_contacts: autoSave,
        "approval.require_owner_approval_for_sensitive": requireApproval,
        "approval.approval_timeout_minutes": approvalTimeout,
      });
      notify("Settings saved.");
      refetch();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Failed to save.", "error");
    } finally {
      setSavingGeneral(false);
    }
  };

  const saveProfile = async () => {
    setSavingProfile(true);
    try {
      await settingsApi.patchProfile({ name: ownerName, profession: ownerProfession, writing_style: writingStyle });
      notify("Profile saved.");
      refetchProfile();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Failed to save.", "error");
    } finally {
      setSavingProfile(false);
    }
  };

  const handleReload = async () => {
    setReloading(true);
    try {
      await settingsApi.reload();
      notify("Configuration reloaded from disk.");
      refetch();
      refetchProfile();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Failed to reload.", "error");
    } finally {
      setReloading(false);
    }
  };

  return (
    <Layout title="Settings">
      {loading && <CardSkeleton />}
      {error && <ErrorState message={error} onRetry={refetch} />}

      {data && (
        <div className="max-w-xl space-y-4">
          <Section title="General">
            <label className="block">
              <span className="text-xs text-ink-secondary">Welcome message</span>
              <textarea
                value={welcomeMessage}
                onChange={(e) => setWelcomeMessage(e.target.value)}
                rows={2}
                className="mt-1 w-full rounded border border-border-strong bg-canvas px-3 py-2 text-sm text-ink-primary focus:border-violet focus:outline-none"
              />
            </label>
            <div className="mt-3 flex items-center justify-between">
              <span className="text-sm text-ink-primary">Auto-save unknown contacts</span>
              <Toggle checked={autoSave} onChange={setAutoSave} label="Auto-save unknown contacts" />
            </div>
          </Section>

          <Section title="Sensitive-info approval">
            <div className="flex items-center justify-between">
              <span className="text-sm text-ink-primary">Require owner approval</span>
              <Toggle checked={requireApproval} onChange={setRequireApproval} label="Require owner approval" />
            </div>
            <label className="mt-3 block">
              <span className="text-xs text-ink-secondary">Approval timeout (minutes)</span>
              <input
                type="number"
                min={1}
                value={approvalTimeout}
                onChange={(e) => setApprovalTimeout(Number(e.target.value))}
                className="mt-1 w-32 rounded border border-border-strong bg-canvas px-3 py-2 text-sm text-ink-primary focus:border-violet focus:outline-none"
              />
            </label>
          </Section>

          <p className="text-xs text-ink-muted">
            Looking for the response/typing delay setting? It moved to{" "}
            <Link to="/ai-control" className="text-violet hover:underline">
              AI control
            </Link>
            , next to the other AI behavior settings.
          </p>

          <button
            onClick={saveGeneral}
            disabled={savingGeneral}
            className="rounded bg-signal px-4 py-2 text-sm font-medium text-canvas hover:bg-signal/90 disabled:opacity-50"
          >
            {savingGeneral ? "Saving…" : "Save settings"}
          </button>

          {profile && (
            <Section title="Owner profile">
              <label className="block">
                <span className="text-xs text-ink-secondary">Name</span>
                <input
                  value={ownerName}
                  onChange={(e) => setOwnerName(e.target.value)}
                  className="mt-1 w-full rounded border border-border-strong bg-canvas px-3 py-2 text-sm text-ink-primary focus:border-violet focus:outline-none"
                />
              </label>
              <label className="mt-3 block">
                <span className="text-xs text-ink-secondary">Profession</span>
                <input
                  value={ownerProfession}
                  onChange={(e) => setOwnerProfession(e.target.value)}
                  className="mt-1 w-full rounded border border-border-strong bg-canvas px-3 py-2 text-sm text-ink-primary focus:border-violet focus:outline-none"
                />
              </label>
              <label className="mt-3 block">
                <span className="text-xs text-ink-secondary">Writing style</span>
                <input
                  value={writingStyle}
                  onChange={(e) => setWritingStyle(e.target.value)}
                  className="mt-1 w-full rounded border border-border-strong bg-canvas px-3 py-2 text-sm text-ink-primary focus:border-violet focus:outline-none"
                />
              </label>
              <button
                onClick={saveProfile}
                disabled={savingProfile}
                className="mt-4 rounded bg-signal px-4 py-2 text-sm font-medium text-canvas hover:bg-signal/90 disabled:opacity-50"
              >
                {savingProfile ? "Saving…" : "Save profile"}
              </button>
            </Section>
          )}

          <Section title="Configuration">
            <p className="text-sm text-ink-secondary">
              Force-reload dynamic settings and profile from disk (does not restart the process).
            </p>
            <button
              onClick={handleReload}
              disabled={reloading}
              className="mt-3 rounded border border-border-strong px-4 py-2 text-sm text-ink-primary hover:bg-raised disabled:opacity-50"
            >
              {reloading ? "Reloading…" : "Reload configuration"}
            </button>
          </Section>
        </div>
      )}
    </Layout>
  );
}
