import { useEffect, useState } from "react";
import { Layout } from "../components/Layout";
import { SignalDot } from "../components/SignalDot";
import { Toggle } from "../components/Toggle";
import { CardSkeleton, ErrorState } from "../components/States";
import { useApi } from "../lib/useApi";
import { aiApi } from "../api/ai";
import { settingsApi } from "../api/settings";
import { useToast } from "../context/ToastContext";

export function AIControl() {
  const { data, loading, error, refetch } = useApi(() => aiApi.status());
  const { data: settingsData, refetch: refetchSettings } = useApi(() => settingsApi.get());
  const { notify } = useToast();
  const [toggling, setToggling] = useState(false);

  const [delayEnabled, setDelayEnabled] = useState(true);
  const [delayMin, setDelayMin] = useState(2);
  const [delayMax, setDelayMax] = useState(5);
  const [savingDelay, setSavingDelay] = useState(false);

  const [voiceEnabled, setVoiceEnabled] = useState(false);
  const [voiceMode, setVoiceMode] = useState<"text_only" | "voice_only" | "text_and_voice">("text_only");
  const [voiceName, setVoiceName] = useState("alloy");
  const [voiceSpeed, setVoiceSpeed] = useState(1.0);
  const [voiceFormat, setVoiceFormat] = useState("mp3");
  const [voiceMaxChars, setVoiceMaxChars] = useState(600);
  const [voiceFallback, setVoiceFallback] = useState(true);
  const [savingVoice, setSavingVoice] = useState(false);

  const [webSearchEnabled, setWebSearchEnabled] = useState(false);
  const [webSearchMode, setWebSearchMode] = useState<"automatic" | "always" | "never" | "only_when_requested">(
    "automatic"
  );
  const [savingWebSearch, setSavingWebSearch] = useState(false);

  useEffect(() => {
    if (!settingsData) return;
    setDelayEnabled(settingsData.typing_delay.enabled);
    setDelayMin(settingsData.typing_delay.min_seconds);
    setDelayMax(settingsData.typing_delay.max_seconds);

    const v = settingsData.voice_reply;
    setVoiceEnabled(v.enabled);
    setVoiceMode(v.reply_mode);
    setVoiceName(v.voice);
    setVoiceSpeed(v.speed);
    setVoiceFormat(v.format);
    setVoiceMaxChars(v.max_characters);
    setVoiceFallback(v.text_fallback_enabled);

    setWebSearchEnabled(settingsData.web_search.enabled);
    setWebSearchMode(settingsData.web_search.mode);
  }, [settingsData]);

  const handleToggle = async (enabled: boolean) => {
    setToggling(true);
    try {
      await aiApi.toggle(enabled);
      notify(`AI is now ${enabled ? "active" : "paused"}.`);
      refetch();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Failed to update.", "error");
    } finally {
      setToggling(false);
    }
  };

  const saveDelay = async () => {
    setSavingDelay(true);
    try {
      await settingsApi.patch({
        "typing_delay.enabled": delayEnabled,
        "typing_delay.min_seconds": delayMin,
        "typing_delay.max_seconds": delayMax,
      });
      notify("Response delay updated.");
      refetchSettings();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Failed to save.", "error");
    } finally {
      setSavingDelay(false);
    }
  };

  const saveVoice = async () => {
    setSavingVoice(true);
    try {
      await settingsApi.patch({
        "voice_reply.enabled": voiceEnabled,
        "voice_reply.reply_mode": voiceMode,
        "voice_reply.voice": voiceName,
        "voice_reply.speed": voiceSpeed,
        "voice_reply.format": voiceFormat,
        "voice_reply.max_characters": voiceMaxChars,
        "voice_reply.text_fallback_enabled": voiceFallback,
      });
      notify("AI Voice Reply settings updated.");
      refetchSettings();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Failed to save.", "error");
    } finally {
      setSavingVoice(false);
    }
  };

  const saveWebSearch = async () => {
    setSavingWebSearch(true);
    try {
      await settingsApi.patch({
        "web_search.enabled": webSearchEnabled,
        "web_search.mode": webSearchMode,
      });
      notify("Web search settings updated.");
      refetchSettings();
    } catch (e) {
      notify(e instanceof Error ? e.message : "Failed to save.", "error");
    } finally {
      setSavingWebSearch(false);
    }
  };

  return (
    <Layout title="AI control">
      {loading && <CardSkeleton />}
      {error && <ErrorState message={error} onRetry={refetch} />}

      {data && (
        <div className="max-w-2xl space-y-4">
          <div className="rounded border border-border bg-surface p-5">
            <div className="flex items-center justify-between">
              <div className="flex items-center gap-3">
                <SignalDot tone={data.global_ai_enabled ? "live" : "off"} pulse />
                <div>
                  <p className="text-sm font-medium text-ink-primary">
                    {data.global_ai_enabled ? "AI is active" : "AI is paused"}
                  </p>
                  <p className="text-sm text-ink-secondary">
                    {data.global_ai_enabled
                      ? "The assistant is automatically responding to incoming WhatsApp messages."
                      : "Automatic AI responses are currently disabled."}
                  </p>
                </div>
              </div>
              <Toggle checked={data.global_ai_enabled} onChange={handleToggle} disabled={toggling} label="Global AI switch" />
            </div>
          </div>

          <div className="rounded border border-border bg-surface p-5">
            <h2 className="text-sm font-medium text-ink-primary">Working hours</h2>
            <div className="mt-3 flex items-center gap-2">
              <SignalDot tone={data.working_hours.within_hours ? "live" : "warn"} />
              <p className="text-sm text-ink-secondary">
                {data.working_hours.within_hours ? "AI is within working hours" : "AI is outside working hours"}
              </p>
            </div>
            <p className="mt-1 text-xs text-ink-muted font-mono">{data.working_hours.reason}</p>
          </div>

          <div className="rounded border border-border bg-surface p-5">
            <h2 className="text-sm font-medium text-ink-primary">Model configuration</h2>
            <dl className="mt-3 grid grid-cols-2 gap-y-2 text-sm">
              <dt className="text-ink-secondary">Primary model</dt>
              <dd className="font-mono text-ink-primary">{data.primary_model}</dd>
              <dt className="text-ink-secondary">Fallback model</dt>
              <dd className="font-mono text-ink-primary">{data.fallback_model}</dd>
            </dl>
          </div>

          <div className="rounded border border-border bg-surface p-5">
            <div className="flex items-center justify-between">
              <div>
                <h2 className="text-sm font-medium text-ink-primary">Response delay</h2>
                <p className="mt-0.5 text-xs text-ink-secondary">
                  Controls how long the AI waits before sending an automated response, so replies feel less
                  instantaneous. A random delay is picked between the min and max each time.
                </p>
              </div>
              <Toggle checked={delayEnabled} onChange={setDelayEnabled} label="Response delay enabled" />
            </div>
            <div className="mt-4 grid grid-cols-2 gap-4">
              <label className="block">
                <span className="text-xs text-ink-secondary">Min seconds</span>
                <input
                  type="number"
                  min={0}
                  step={0.5}
                  value={delayMin}
                  disabled={!delayEnabled}
                  onChange={(e) => setDelayMin(Number(e.target.value))}
                  className="mt-1 w-full rounded border border-border-strong bg-canvas px-3 py-2 text-sm text-ink-primary focus:border-violet focus:outline-none disabled:opacity-40"
                />
              </label>
              <label className="block">
                <span className="text-xs text-ink-secondary">Max seconds</span>
                <input
                  type="number"
                  min={0}
                  step={0.5}
                  value={delayMax}
                  disabled={!delayEnabled}
                  onChange={(e) => setDelayMax(Number(e.target.value))}
                  className="mt-1 w-full rounded border border-border-strong bg-canvas px-3 py-2 text-sm text-ink-primary focus:border-violet focus:outline-none disabled:opacity-40"
                />
              </label>
            </div>
            <button
              onClick={saveDelay}
              disabled={savingDelay}
              className="mt-4 rounded bg-signal px-4 py-2 text-sm font-medium text-canvas hover:bg-signal/90 disabled:opacity-50"
            >
              {savingDelay ? "Saving…" : "Save response delay"}
            </button>
          </div>

          <div className="rounded border border-border bg-surface p-5">
            <div className="flex items-center justify-between">
              <div>
                <h2 className="text-sm font-medium text-ink-primary">AI voice reply</h2>
                <p className="mt-0.5 text-xs text-ink-secondary">
                  Optionally sends replies as a WhatsApp voice message instead of, or alongside, text. Off by
                  default - existing text replies are unaffected either way.
                </p>
              </div>
              <Toggle checked={voiceEnabled} onChange={setVoiceEnabled} label="AI voice reply enabled" />
            </div>

            <div className="mt-3 flex items-center gap-2 text-xs">
              <SignalDot tone={data.voice_reply.tts_configured ? "live" : "off"} />
              <span className="text-ink-secondary">
                Model: <span className="font-mono text-ink-primary">{data.voice_reply.tts_model}</span> (OpenRouter) ·{" "}
                {data.voice_reply.tts_configured ? "configured" : "not configured (set OPENROUTER_API_KEY in .env)"}
              </span>
            </div>

            <div className={`mt-4 space-y-4 ${voiceEnabled ? "" : "opacity-40"}`}>
              <label className="block">
                <span className="text-xs text-ink-secondary">Reply mode</span>
                <select
                  value={voiceMode}
                  disabled={!voiceEnabled}
                  onChange={(e) => setVoiceMode(e.target.value as typeof voiceMode)}
                  className="mt-1 w-full rounded border border-border-strong bg-canvas px-3 py-2 text-sm text-ink-primary focus:border-violet focus:outline-none disabled:opacity-40"
                >
                  <option value="text_only">Text only</option>
                  <option value="voice_only">Voice only</option>
                  <option value="text_and_voice">Text + voice</option>
                </select>
              </label>

              <div className="grid grid-cols-2 gap-4">
                <label className="block">
                  <span className="text-xs text-ink-secondary">Voice</span>
                  <input
                    value={voiceName}
                    disabled={!voiceEnabled}
                    onChange={(e) => setVoiceName(e.target.value)}
                    placeholder="e.g. alloy"
                    className="mt-1 w-full rounded border border-border-strong bg-canvas px-3 py-2 text-sm text-ink-primary placeholder:text-ink-muted focus:border-violet focus:outline-none disabled:opacity-40"
                  />
                </label>
                <label className="block">
                  <span className="text-xs text-ink-secondary">Speed</span>
                  <input
                    type="number"
                    min={0.25}
                    max={4}
                    step={0.05}
                    value={voiceSpeed}
                    disabled={!voiceEnabled}
                    onChange={(e) => setVoiceSpeed(Number(e.target.value))}
                    className="mt-1 w-full rounded border border-border-strong bg-canvas px-3 py-2 text-sm text-ink-primary focus:border-violet focus:outline-none disabled:opacity-40"
                  />
                </label>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <label className="block">
                  <span className="text-xs text-ink-secondary">Audio format</span>
                  <select
                    value={voiceFormat}
                    disabled={!voiceEnabled}
                    onChange={(e) => setVoiceFormat(e.target.value)}
                    className="mt-1 w-full rounded border border-border-strong bg-canvas px-3 py-2 text-sm text-ink-primary focus:border-violet focus:outline-none disabled:opacity-40"
                  >
                    <option value="mp3">MP3</option>
                    <option value="aac">AAC</option>
                    <option value="opus">Opus (OGG)</option>
                  </select>
                </label>
                <label className="block">
                  <span className="text-xs text-ink-secondary">Max characters per reply</span>
                  <input
                    type="number"
                    min={1}
                    value={voiceMaxChars}
                    disabled={!voiceEnabled}
                    onChange={(e) => setVoiceMaxChars(Number(e.target.value))}
                    className="mt-1 w-full rounded border border-border-strong bg-canvas px-3 py-2 text-sm text-ink-primary focus:border-violet focus:outline-none disabled:opacity-40"
                  />
                </label>
              </div>

              <div className="flex items-center justify-between">
                <div>
                  <span className="text-sm text-ink-primary">Text fallback</span>
                  <p className="text-xs text-ink-secondary">
                    In "Voice only" mode, send text instead if voice generation fails.
                  </p>
                </div>
                <Toggle checked={voiceFallback} onChange={setVoiceFallback} disabled={!voiceEnabled} label="Text fallback" />
              </div>
            </div>

            <button
              onClick={saveVoice}
              disabled={savingVoice}
              className="mt-4 rounded bg-signal px-4 py-2 text-sm font-medium text-canvas hover:bg-signal/90 disabled:opacity-50"
            >
              {savingVoice ? "Saving…" : "Save voice reply settings"}
            </button>
          </div>

          <div className="rounded border border-border bg-surface p-5">
            <h2 className="text-sm font-medium text-ink-primary">Voice input (incoming voice messages)</h2>
            <p className="mt-0.5 text-xs text-ink-secondary">
              When enabled, incoming WhatsApp voice notes are transcribed and answered like a normal text message.
              Set with <span className="font-mono">VOICE_INPUT_ENABLED</span> in .env - not dashboard-editable
              since it affects how every incoming message is parsed.
            </p>
            <div className="mt-3 flex items-center gap-2 text-xs">
              <SignalDot tone={data.voice_input.enabled ? "live" : "idle"} />
              <span className="text-ink-secondary">
                {data.voice_input.enabled ? "Enabled" : "Disabled"} · Model:{" "}
                <span className="font-mono text-ink-primary">{data.voice_input.stt_model}</span> (OpenRouter) ·{" "}
                {data.voice_input.stt_configured ? "configured" : "not configured"}
              </span>
            </div>
          </div>

          <div className="rounded border border-border bg-surface p-5">
            <div className="flex items-center justify-between">
              <div>
                <h2 className="text-sm font-medium text-ink-primary">Web search</h2>
                <p className="mt-0.5 text-xs text-ink-secondary">
                  Controls when the AI is allowed to attempt a web search. No search provider is connected yet -
                  see the note below.
                </p>
              </div>
              <Toggle checked={webSearchEnabled} onChange={setWebSearchEnabled} label="Web search enabled" />
            </div>

            <div className="mt-3 flex items-center gap-2 text-xs">
              <SignalDot tone={data.web_search.configured ? "live" : "off"} />
              <span className="text-ink-secondary">
                {data.web_search.configured
                  ? `Provider configured: ${data.web_search.provider}`
                  : "No search provider configured - the AI will tell people search is unavailable rather than fake a search. See web_search.py for how to add one."}
              </span>
            </div>

            <label className={`mt-4 block ${webSearchEnabled ? "" : "opacity-40"}`}>
              <span className="text-xs text-ink-secondary">Search mode</span>
              <select
                value={webSearchMode}
                disabled={!webSearchEnabled}
                onChange={(e) => setWebSearchMode(e.target.value as typeof webSearchMode)}
                className="mt-1 w-full rounded border border-border-strong bg-canvas px-3 py-2 text-sm text-ink-primary focus:border-violet focus:outline-none disabled:opacity-40"
              >
                <option value="automatic">Automatic (time-sensitive questions or explicit requests)</option>
                <option value="always">Always search</option>
                <option value="never">Never search</option>
                <option value="only_when_requested">Only when explicitly requested</option>
              </select>
            </label>

            <button
              onClick={saveWebSearch}
              disabled={savingWebSearch}
              className="mt-4 rounded bg-signal px-4 py-2 text-sm font-medium text-canvas hover:bg-signal/90 disabled:opacity-50"
            >
              {savingWebSearch ? "Saving…" : "Save web search settings"}
            </button>
          </div>
        </div>
      )}
    </Layout>
  );
}
