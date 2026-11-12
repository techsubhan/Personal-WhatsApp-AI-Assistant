import { api } from "./client";
import type { ApprovalRequestItem } from "./types";

export const approvalsApi = {
  list: (status?: string) => api.get<{ items: ApprovalRequestItem[] }>("/admin/approvals", { status }),

  resolve: (id: number, approve: boolean) =>
    api.post<{ id: number; status: string; action_result: string }>(`/admin/approvals/${id}/resolve`, { approve }),
};
