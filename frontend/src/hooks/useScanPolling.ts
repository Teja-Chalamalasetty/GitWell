import { useQueryClient } from "@tanstack/react-query";
import { useEffect, useRef } from "react";
import { invalidateScanData, useScan } from "../api/hooks";
import { isActive, isTerminal } from "../lib/status";

/**
 * Polls GET /scans/{id} every 2 s while the scan is queued or running (the interval lives in
 * `useScan`). When it reaches a terminal status, the repo, history and dashboard queries are
 * invalidated so the page shows the new results.
 */
export function useScanPolling(scanId: number | null | undefined, repoId: number | undefined) {
  const qc = useQueryClient();
  const scan = useScan(scanId);
  const wasActive = useRef(false);
  const status = scan.data?.status;

  useEffect(() => {
    if (isActive(status)) {
      wasActive.current = true;
    } else if (isTerminal(status) && wasActive.current) {
      wasActive.current = false;
      void invalidateScanData(qc, repoId);
    }
  }, [status, qc, repoId]);

  return scan;
}
