import { api } from "./client";
import type { AiStatus, OverrideItem } from "./types";

export const aiApi = {
  status: () => api.get<AiStatus>("/admin/ai/status"),
  toggle: (enabled: boolean) => api.post<{ global_ai_enabled: boolean }>("/admin/ai/toggle", { enabled }),
};

export const overridesApi = {
  list: () => api.get<{ items: OverrideItem[] }>("/admin/overrides"),

  activate: (waId: string) =>
    api.post<{ wa_id: string; active: boolean; expires_at: string | null }>(`/admin/contacts/${waId}/override`, {
      action: "activate",
    }),

  clear: (waId: string) =>
    api.post<{ wa_id: string; active: boolean; expires_at: string | null }>(`/admin/contacts/${waId}/override`, {
      action: "clear",
    }),
};
