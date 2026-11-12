import { api } from "./client";
import type { ScheduledMessageItem } from "./types";

export const scheduledMessagesApi = {
  list: (status?: string) => api.get<{ items: ScheduledMessageItem[] }>("/admin/scheduled-messages", { status }),

  create: (payload: { contact_wa_id: string; message_text: string; scheduled_at: string; timezone: string }) =>
    api.post<ScheduledMessageItem>("/admin/scheduled-messages", payload),

  cancel: (id: number) => api.post<ScheduledMessageItem>(`/admin/scheduled-messages/${id}/cancel`),

  retry: (id: number) => api.post<ScheduledMessageItem>(`/admin/scheduled-messages/${id}/retry`),
};
