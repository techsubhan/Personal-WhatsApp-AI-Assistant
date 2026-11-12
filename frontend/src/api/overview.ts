import { api } from "./client";
import type { OverviewStats, HealthStatus } from "./types";

export const overviewApi = {
  stats: () => api.get<OverviewStats>("/admin/overview/stats"),
  health: () => api.get<{ status: string; uptime_seconds: number }>("/health"),
  fullStatus: () => api.get<HealthStatus>("/status"),
};
