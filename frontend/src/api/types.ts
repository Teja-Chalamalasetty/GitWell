// Mirrors the backend schemas (PRD 11.3). Timestamps are ISO 8601 strings ending in "Z".

export type Role = "viewer" | "admin";
export type ScanStatus = "queued" | "running" | "completed" | "failed";
export type ScanTrigger = "manual" | "scheduled" | "on_add";
export type RuleStatus = "pass" | "warn" | "fail" | "not_applicable" | "error";

export interface User {
  id: number;
  email: string;
  role: Role;
  is_active: boolean;
  created_at: string;
}

export interface AppConfig {
  allow_registration: boolean;
  github_token_configured: boolean;
  version: string;
}

export interface LoginResponse {
  access_token: string;
  token_type: "bearer";
  expires_in: number;
  user: User;
}

export interface ListResponse<T> {
  items: T[];
  total: number;
}

export interface LatestScan {
  id: number;
  status: ScanStatus;
  error_code: string | null;
  created_at: string;
  finished_at: string | null;
}

export interface LatestCompletedScan {
  id: number;
  score: number | null;
  grade: string | null;
  commit_sha: string | null;
  finished_at: string | null;
  rules_errored: number;
  is_partial: boolean;
  has_findings: boolean;
}

export interface RepoSummary {
  id: number;
  github_id: number;
  url: string;
  full_name: string;
  default_branch: string;
  is_private: boolean;
  is_archived: boolean;
  has_issues: boolean;
  added_at: string;
  latest_scan: LatestScan | null;
  latest_completed_scan: LatestCompletedScan | null;
  score_delta: number | null;
  active_scan_id: number | null;
  open_draft_id: number | null;
}

export interface Scan {
  id: number;
  repo_id: number;
  status: ScanStatus;
  trigger: ScanTrigger;
  score: number | null;
  grade: string | null;
  rules_errored: number;
  ruleset_version: string | null;
  commit_sha: string | null;
  snapshot_mode: string | null;
  is_partial: boolean;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface RuleResult {
  rule_id: string;
  rule_name: string;
  status: RuleStatus;
  weight: number;
  display_order: number;
  message: string;
  details: Record<string, unknown>;
  fix_tip: string;
}

export interface ScanDetail extends Scan {
  results: RuleResult[];
}

export interface RuleFailure {
  rule_id: string;
  rule_name: string;
  fail: number;
  warn: number;
}

export interface TrendPoint {
  date: string;
  average_score: number;
}

export interface DashboardSummary {
  repo_count: number;
  scanned_repo_count: number;
  average_score: number | null;
  grade_distribution: Record<string, number>;
  rule_failures: RuleFailure[];
  score_trend: TrendPoint[];
}

export type RepoSort = "name" | "score" | "last_scanned";
export type SortOrder = "asc" | "desc";

export type DraftStatus = "drafted" | "approved" | "posting" | "posted" | "discarded";

export interface IssueDraft {
  id: number;
  scan_id: number;
  repo_id: number;
  repo_full_name: string;
  repo_is_private: boolean;
  title: string;
  body: string;
  status: DraftStatus;
  post_attempts: number;
  github_issue_number: number | null;
  github_issue_url: string | null;
  last_error: string | null;
  created_by_id: number | null;
  approved_by_id: number | null;
  created_at: string;
  updated_at: string;
  approved_at: string | null;
}
