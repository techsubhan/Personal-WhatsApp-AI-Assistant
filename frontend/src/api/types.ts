export interface Contact {
  wa_id: string;
  display_name: string | null;
  status: "unknown" | "saved" | "blocked";
  ai_enabled: boolean;
  is_blocked: boolean;
  human_override_active: boolean;
  detected_language: string | null;
  web_search_enabled: boolean;
  created_at: string;
  last_message_at: string | null;
}

export interface ContactsPage {
  total: number;
  page: number;
  page_size: number;
  items: Contact[];
}

export interface Message {
  id: number;
  role: "user" | "assistant" | "system" | "owner";
  content: string;
  message_type: string;
  created_at: string;
}

export interface ApprovalRequestItem {
  id: number;
  wa_id: string | null;
  contact_name: string | null;
  user_message: string;
  reason: string;
  status: "pending" | "approved" | "rejected" | "expired";
  action_summary: string;
  created_at: string;
  expires_at: string | null;
  resolved_at: string | null;
}

export interface OverrideItem {
  wa_id: string;
  display_name: string | null;
  active: boolean;
  expires_at: string | null;
}

export interface SmartMemoryItem {
  id: number;
  category: string;
  subcategory: string;
  value: string;
  importance: "critical" | "high" | "medium" | "low";
  confidence: number;
  retention_policy: string;
  access_count: number;
  updated_at: string;
  expires_at: string | null;
}

export interface OverviewStats {
  contacts: { total: number; saved: number; blocked: number; ai_disabled: number };
  messages: { total: number };
  approvals: { pending: number };
  human_override: { active: number };
  smart_memory: { total_active_facts: number };
  ai: { global_ai_enabled: boolean; working_hours_enabled: boolean };
}

export interface AiStatus {
  global_ai_enabled: boolean;
  primary_model: string;
  fallback_model: string;
  typing_delay_enabled: boolean;
  working_hours: { within_hours: boolean; reason: string };
  voice_reply: { tts_model: string; tts_configured: boolean };
  voice_input: { enabled: boolean; stt_model: string; stt_configured: boolean };
  web_search: { configured: boolean; provider: string | null };
}

export interface ScheduledMessageItem {
  id: number;
  contact_wa_id: string;
  message_text: string;
  scheduled_at: string;
  timezone: string;
  status: "pending" | "sent" | "failed" | "cancelled";
  error_detail: string | null;
  created_at: string;
  sent_at: string | null;
  failed_at: string | null;
  cancelled_at: string | null;
}

export interface AdminActionLogItem {
  id: number;
  admin_wa_id: string;
  command: string;
  target_wa_id: string | null;
  result: string;
  created_at: string;
}

export interface RequestLogItem {
  id: number;
  contact_wa_id: string | null;
  status: string;
  detail: string;
  response_time_ms: number | null;
  created_at: string;
}

export interface HealthStatus {
  application: { status: string; environment: string; uptime_seconds: number };
  database: { status: string };
  openrouter: { status: string; primary_model: string; fallback_model: string };
  whatsapp: { status: string };
  overall_status: string;
}
