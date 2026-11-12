import { api } from "./client";

export interface WorkingHours {
  enabled: boolean;
  start_time: string;
  end_time: string;
  timezone: string;
  outside_hours_action: "no_reply" | "away_message" | "emergency_only";
  away_message: string;
  emergency_keywords: string[];
}

export interface VoiceReplySettings {
  enabled: boolean;
  reply_mode: "text_only" | "voice_only" | "text_and_voice";
  voice: string;
  speed: number;
  format: string;
  max_characters: number;
  text_fallback_enabled: boolean;
}

export interface WebSearchSettings {
  enabled: boolean;
  mode: "automatic" | "always" | "never" | "only_when_requested";
}

export interface RuntimeSettings {
  global_ai_enabled: boolean;
  working_hours: WorkingHours;
  welcome_message: string;
  auto_save_unknown_contacts: boolean;
  approval: { require_owner_approval_for_sensitive: boolean; approval_timeout_minutes: number };
  typing_delay: { enabled: boolean; min_seconds: number; max_seconds: number };
  voice_reply: VoiceReplySettings;
  web_search: WebSearchSettings;
}

export interface OwnerProfile {
  name: string;
  profession: string;
  language: string;
  preferences: string[];
  writing_style: string;
  rules: string[];
}

export const settingsApi = {
  get: () => api.get<RuntimeSettings>("/admin/settings"),
  patch: (updates: Record<string, unknown>) => api.patch<RuntimeSettings>("/admin/settings", { updates }),

  getProfile: () => api.get<OwnerProfile>("/admin/profile"),
  patchProfile: (updates: Record<string, unknown>) => api.patch<OwnerProfile>("/admin/profile", { updates }),

  reload: () => api.post<{ reloaded: boolean; at: string }>("/admin/reload"),
};
