import {
  type QueryClient,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import { isActive } from "../lib/status";
import { api } from "./client";
import type {
  AppConfig,
  DashboardSummary,
  IssueDraft,
  ListResponse,
  RepoSort,
  RepoSummary,
  Role,
  Scan,
  ScanDetail,
  ScanStatus,
  SortOrder,
  User,
} from "./types";

const POLL_MS = 2000;

export const keys = {
  config: ["config"] as const,
  me: (token: string | null) => ["me", token] as const,
  repos: (sort: RepoSort, order: SortOrder) => ["repos", sort, order] as const,
  repo: (id: number) => ["repo", id] as const,
  repoScans: (id: number, status: ScanStatus | "all") => ["repoScans", id, status] as const,
  scan: (id: number) => ["scan", id] as const,
  dashboard: ["dashboard"] as const,
  issueDraft: (id: number) => ["issueDraft", id] as const,
  issueDrafts: (repoId?: number) => ["issueDrafts", ...(repoId === undefined ? [] : [repoId])] as const,
  users: ["users"] as const,
};

/** Refresh everything that depends on scan results. */
export function invalidateScanData(qc: QueryClient, repoId?: number): Promise<unknown> {
  return Promise.all([
    qc.invalidateQueries({ queryKey: ["repos"] }),
    qc.invalidateQueries({ queryKey: ["repo", ...(repoId === undefined ? [] : [repoId])] }),
    qc.invalidateQueries({ queryKey: ["repoScans", ...(repoId === undefined ? [] : [repoId])] }),
    qc.invalidateQueries({ queryKey: keys.dashboard }),
  ]);
}

// ---- reads --------------------------------------------------------------------------------

export function useConfig() {
  return useQuery({
    queryKey: keys.config,
    queryFn: () => api.get<AppConfig>("/config"),
    staleTime: 5 * 60_000,
  });
}

export function useRepos(sort: RepoSort, order: SortOrder) {
  return useQuery({
    queryKey: keys.repos(sort, order),
    queryFn: () => api.get<ListResponse<RepoSummary>>("/repos", { sort, order, limit: 200 }),
    // Keep the table live while any scan is running.
    refetchInterval: (query) =>
      query.state.data?.items.some((r) => r.active_scan_id !== null) ? POLL_MS : false,
  });
}

export function useRepo(id: number) {
  return useQuery({
    queryKey: keys.repo(id),
    queryFn: () => api.get<RepoSummary>(`/repos/${id}`),
  });
}

export function useScan(id: number | null | undefined) {
  return useQuery({
    queryKey: keys.scan(id ?? 0),
    queryFn: () => api.get<ScanDetail>(`/scans/${id}`),
    enabled: id !== null && id !== undefined,
    // PRD 15: while a scan is active, poll it every 2 s; stop at a terminal status.
    refetchInterval: (query) => (isActive(query.state.data?.status) ? POLL_MS : false),
  });
}

export function useRepoScans(repoId: number, status: ScanStatus | "all", limit = 200) {
  return useQuery({
    queryKey: keys.repoScans(repoId, status),
    queryFn: () =>
      api.get<ListResponse<Scan>>(`/repos/${repoId}/scans`, {
        status: status === "all" ? undefined : status,
        limit,
      }),
  });
}

export function useDashboard() {
  return useQuery({
    queryKey: keys.dashboard,
    queryFn: () => api.get<DashboardSummary>("/dashboard/summary"),
  });
}

export function useUsers() {
  return useQuery({
    queryKey: keys.users,
    queryFn: () => api.get<ListResponse<User>>("/users", { limit: 200 }),
  });
}

// ---- writes -------------------------------------------------------------------------------

export function useAddRepo() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (url: string) => api.post<RepoSummary>("/repos", { url }),
    onSuccess: () => invalidateScanData(qc),
  });
}

export function useDeleteRepo() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: number) => api.delete<void>(`/repos/${id}`),
    onSuccess: () => invalidateScanData(qc),
  });
}

export function useTriggerScan() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (repoId: number) => api.post<Scan>(`/repos/${repoId}/scan`),
    onSettled: (_data, _error, repoId) => invalidateScanData(qc, repoId),
  });
}

export function useCreateUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { email: string; password: string; role: Role }) =>
      api.post<User>("/users", body),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.users }),
  });
}

export function useUpdateUser() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, ...body }: { id: number; role?: Role; is_active?: boolean }) =>
      api.patch<User>(`/users/${id}`, body),
    onSettled: () => qc.invalidateQueries({ queryKey: keys.users }),
  });
}

export function useChangePassword() {
  return useMutation({
    mutationFn: (body: { current_password: string; new_password: string }) =>
      api.post<void>("/auth/change-password", body),
  });
}

// ---- issue drafts (PRD 15) ------------------------------------------------------------------

export function useIssueDraft(id: number) {
  return useQuery({
    queryKey: keys.issueDraft(id),
    queryFn: () => api.get<IssueDraft>(`/issue-drafts/${id}`),
    // A post in flight finishes on its own; keep the page current until it does.
    refetchInterval: (query) => (query.state.data?.status === "posting" ? POLL_MS : false),
  });
}

export function useRepoDrafts(repoId: number) {
  return useQuery({
    queryKey: keys.issueDrafts(repoId),
    queryFn: () => api.get<ListResponse<IssueDraft>>("/issue-drafts", { repo_id: repoId, limit: 50 }),
  });
}

function invalidateDraftData(qc: QueryClient, draftId?: number): Promise<unknown> {
  return Promise.all([
    qc.invalidateQueries({ queryKey: ["issueDrafts"] }),
    draftId === undefined
      ? Promise.resolve()
      : qc.invalidateQueries({ queryKey: keys.issueDraft(draftId) }),
    // `open_draft_id` on the repo summaries changes with every draft transition.
    qc.invalidateQueries({ queryKey: ["repos"] }),
    qc.invalidateQueries({ queryKey: ["repo"] }),
  ]);
}

export function useCreateDraft() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (scanId: number) => api.post<IssueDraft>(`/scans/${scanId}/issue-draft`),
    onSettled: () => invalidateDraftData(qc),
  });
}

export function useUpdateDraft(id: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: { title?: string; body?: string }) =>
      api.patch<IssueDraft>(`/issue-drafts/${id}`, body),
    onSuccess: (draft) => qc.setQueryData(keys.issueDraft(id), draft),
    onSettled: () => invalidateDraftData(qc, id),
  });
}

export function useApproveDraft(id: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<IssueDraft>(`/issue-drafts/${id}/approve`),
    // PRD 15: the draft is refetched after ANY outcome (a failed post leaves it `approved`).
    onSettled: () => invalidateDraftData(qc, id),
  });
}

export function useDiscardDraft(id: number) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => api.post<IssueDraft>(`/issue-drafts/${id}/discard`),
    onSettled: () => invalidateDraftData(qc, id),
  });
}
