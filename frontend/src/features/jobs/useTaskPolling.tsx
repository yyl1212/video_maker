"use client";

import { useCallback, useEffect, useState } from "react";
import { apiRequest, type ActiveJobRead, type TaskDetail } from "@/lib/api";

type Connection = "idle" | "loading" | "connected" | "disconnected";
type State<T> = {
  path: string | null;
  data: T | null;
  connection: Connection;
  error: string;
};
const taskActive = (value: TaskDetail) =>
  value.status === "PENDING" || value.status === "RUNNING";
const discoverContinuously = () => true;

function usePolling<T>(
  path: string | null,
  keepPolling: (value: T) => boolean,
  onReceived?: (value: T) => void,
) {
  const [sequence, setSequence] = useState(0);
  const [state, setState] = useState<State<T>>({
    path,
    data: null,
    connection: path ? "loading" : "idle",
    error: "",
  });
  const refresh = useCallback(() => setSequence((value) => value + 1), []);
  useEffect(() => {
    if (!path) return;
    let disposed = false;
    let busy = false;
    let terminal = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let controller: AbortController | null = null;
    function schedule() {
      clearTimeout(timer);
      if (!disposed && !terminal)
        timer = setTimeout(() => void poll(), document.hidden ? 5000 : 2000);
    }
    async function poll() {
      if (disposed || busy) return;
      busy = true;
      const request = new AbortController();
      controller = request;
      try {
        const value = await apiRequest<T>(path!, { signal: request.signal });
        // Abort可能晚于响应到达；作用域失效后仍不得写入当前页面。
        if (disposed || request.signal.aborted) return;
        terminal = !keepPolling(value);
        setState({ path, data: value, connection: "connected", error: "" });
        onReceived?.(value);
      } catch (reason: unknown) {
        if (disposed || request.signal.aborted) return;
        setState((previous) => ({
          path,
          data: previous.path === path ? previous.data : null,
          connection: "disconnected",
          error:
            reason instanceof Error && !(reason instanceof TypeError)
              ? reason.message
              : "连接暂时中断，恢复后继续查询；不会重新提交任务",
        }));
      } finally {
        busy = false;
        schedule();
      }
    }
    function visibilityChanged() {
      if (!busy) schedule();
    }
    document.addEventListener("visibilitychange", visibilityChanged);
    void poll();
    return () => {
      disposed = true;
      clearTimeout(timer);
      controller?.abort();
      document.removeEventListener("visibilitychange", visibilityChanged);
    };
  }, [path, sequence, keepPolling, onReceived]);
  const current: State<T> =
    state.path === path
      ? state
      : { path, data: null, connection: path ? "loading" : "idle", error: "" };
  return { ...current, refresh };
}

export function useTaskPolling(jobId: number | null) {
  const path =
    jobId !== null && Number.isSafeInteger(jobId) && jobId > 0
      ? `/jobs/${jobId}`
      : null;
  const result = usePolling<TaskDetail>(path, taskActive);
  return {
    task: result.data,
    connection: result.connection,
    error: result.error,
    refresh: result.refresh,
  };
}

export function useActiveTaskPolling(
  onReceived: (value: ActiveJobRead) => void,
) {
  return usePolling<ActiveJobRead>(
    "/jobs/active",
    discoverContinuously,
    onReceived,
  );
}
