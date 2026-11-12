import { api } from "./client";
import type { Contact, ContactsPage } from "./types";

export const contactsApi = {
  list: (params: {
    search?: string;
    status?: string;
    blocked?: boolean;
    ai_enabled?: boolean;
    page?: number;
    page_size?: number;
  }) => api.get<ContactsPage>("/admin/contacts", params),

  get: (waId: string) => api.get<Contact>(`/admin/contacts/${waId}`),

  setBlocked: (waId: string, blocked: boolean) =>
    api.post<{ wa_id: string; is_blocked: boolean }>(`/admin/contacts/${waId}/block`, { blocked }),

  setAiEnabled: (waId: string, enabled: boolean) =>
    api.patch<{ wa_id: string; ai_enabled: boolean }>(`/admin/contacts/${waId}/ai`, { enabled }),

  setWebSearchEnabled: (waId: string, enabled: boolean) =>
    api.patch<{ wa_id: string; web_search_enabled: boolean }>(`/admin/contacts/${waId}/web-search`, { enabled }),
};
