import { api } from "./client";
import type { AdminActionLogItem, RequestLogItem } from "./types";

export const logsApi = {
  adminActions: (limit = 100) => api.get<{ items: AdminActionLogItem[] }>("/admin/logs/admin-actions", { limit }),
  requests: (limit = 100) => api.get<{ items: RequestLogItem[] }>("/admin/logs/requests", { limit }),
};
