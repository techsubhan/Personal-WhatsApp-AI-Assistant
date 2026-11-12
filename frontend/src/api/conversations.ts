import { api } from "./client";
import type { Message } from "./types";

export const conversationsApi = {
  list: (waId: string, limit = 100) =>
    api.get<{ wa_id: string; items: Message[] }>(`/admin/contacts/${waId}/messages`, { limit }),

  clear: (waId: string) => api.delete<{ wa_id: string; cleared: number }>(`/admin/contacts/${waId}/messages`),
};
