import { api } from "./client";
import type { SmartMemoryItem } from "./types";

export const memoryApi = {
  list: (waId: string, search?: string) =>
    api.get<{ wa_id: string; items: SmartMemoryItem[] }>(`/admin/contacts/${waId}/memories`, { search }),

  remove: (waId: string, memoryId: number) =>
    api.delete<{ wa_id: string; deleted_id: number }>(`/admin/contacts/${waId}/memories/${memoryId}`),
};
