"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { apiRequest, ApiError, type TaskRead } from "@/lib/api";
import type { components } from "@/lib/api-types";
import {
  useActiveTaskPolling,
  useTaskPolling,
} from "@/features/jobs/useTaskPolling";
import { DraftReview, type Applied } from "./DraftReview";

type Intent = components["schemas"]["TextJobSubmit"];
export function AssistantTaskPanel({
  kind,
  projectId,
  episodeId,
  shotId,
  projectVersion,
  episodeVersion,
  shotVersion,
  readonly,
  dirty,
  ready,
  onApplied,
}: {
  kind: "PARSE_SCRIPT" | "OPTIMIZE_PROMPTS";
  projectId: number;
  episodeId: number;
  shotId?: number;
  projectVersion: number;
  episodeVersion: number;
  shotVersion?: number;
  readonly: boolean;
  dirty: boolean;
  ready: boolean;
  onApplied: (result: Applied) => void;
}) {
  const parse = kind === "PARSE_SCRIPT";
  const scope = `${kind}:${projectId}:${shotId ?? episodeId}`;
  const intentKey = `vm:text-intent:${scope}`,
    jobKey = `vm:text-job:${scope}`;
  const [intent, setIntent] = useState<Intent | null>(null);
  const [jobId, setJobId] = useState<number | null>(null);
  const [opened, setOpened] = useState<number | null>(null);
  const [history, setHistory] = useState<TaskRead[]>([]);
  const [refresh, setRefresh] = useState(0);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const [historyError, setHistoryError] = useState("");
  const initialized = useRef(false),
    alive = useRef(false),
    submitting = useRef(false);
  const controller = useRef<AbortController | null>(null);
  const restore = useCallback(() => {
    if (initialized.current) return;
    initialized.current = true;
    try {
      const value: unknown = JSON.parse(
        sessionStorage.getItem(intentKey) ?? "null",
      );
      if (
        value &&
        typeof value === "object" &&
        "idempotency_key" in value &&
        typeof value.idempotency_key === "string"
      )
        setIntent(value as Intent);
      const saved = Number(sessionStorage.getItem(jobKey));
      if (Number.isSafeInteger(saved) && saved > 0) setJobId(saved);
    } catch {}
  }, [intentKey, jobKey]);
  const active = useActiveTaskPolling(restore);
  const job = useTaskPolling(jobId);
  const matches = useCallback(
    (task: TaskRead) =>
      task.project_id === projectId &&
      task.episode_id === episodeId &&
      task.kind === kind &&
      task.shot_id === (shotId ?? null),
    [projectId, episodeId, kind, shotId],
  );
  const current = job.task && matches(job.task) ? job.task : null;
  const completed = current?.status === "DONE" ? current.id : 0;
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      controller.current?.abort();
    };
  }, [scope]);
  useEffect(() => {
    const request = new AbortController();
    apiRequest<TaskRead[]>(`/projects/${projectId}/jobs?limit=100`, {
      signal: request.signal,
    })
      .then((tasks) => {
        if (!request.signal.aborted) {
          setHistory(tasks.filter(matches));
          setHistoryError("");
        }
      })
      .catch(() => {
        if (!request.signal.aborted)
          setHistoryError("暂时无法读取文本历史，可手动刷新。");
      });
    return () => request.abort();
  }, [projectId, matches, completed, refresh]);
  async function submit(frozen: Intent) {
    if (submitting.current) return;
    submitting.current = true;
    const request = new AbortController();
    controller.current = request;
    setPending(true);
    setError("");
    setIntent(frozen);
    try {
      sessionStorage.setItem(intentKey, JSON.stringify(frozen));
    } catch {}
    try {
      const accepted = await apiRequest<components["schemas"]["TaskAccepted"]>(
        parse
          ? `/episodes/${episodeId}/parse-jobs`
          : `/shots/${shotId}/prompt-jobs`,
        { method: "POST", signal: request.signal, body: frozen },
      );
      if (!alive.current || request.signal.aborted) return;
      setJobId(accepted.job_id);
      setIntent(null);
      try {
        sessionStorage.removeItem(intentKey);
        sessionStorage.setItem(jobKey, String(accepted.job_id));
      } catch {}
    } catch (reason: unknown) {
      if (!alive.current || request.signal.aborted) return;
      const known = new Set([
        "TEXT_MODEL_REQUIRED",
        "TEXT_INPUT_INVALID",
        "VERSION_CONFLICT",
        "TASK_BUSY",
        "ARCHIVED",
        "SHOT_NOT_FOUND",
        "INVALID_SCOPE",
      ]);
      if (reason instanceof ApiError && reason.code && known.has(reason.code)) {
        setIntent(null);
        try {
          sessionStorage.removeItem(intentKey);
        } catch {}
      }
      setError(
        reason instanceof Error && !(reason instanceof TypeError)
          ? reason.message
          : "提交结果待确认，请查询或重试原文本提交",
      );
    } finally {
      submitting.current = false;
      if (alive.current && !request.signal.aborted) setPending(false);
    }
  }
  const blocked =
    active.connection !== "connected" ||
    active.data?.runtime_state?.state !== "IDLE";
  const visibleHistory = history.filter((task) => task.status === "DONE");
  if (
    current?.status === "DONE" &&
    !visibleHistory.some((task) => task.id === current.id)
  )
    visibleHistory.unshift(current);
  return (
    <section
      className="panel text-assistant"
      aria-label={parse ? "AI 剧本解析" : "AI Prompt辅助"}
    >
      <div className="section-heading">
        <div>
          <p className="eyebrow">本机 AI · 人工确认后采纳</p>
          <h2>{parse ? "把剧本拆成分镜草稿" : "扩写这一镜的三类Prompt"}</h2>
        </div>
        <button
          className="text-button"
          onClick={() => setRefresh((n) => n + 1)}
        >
          刷新文本历史
        </button>
      </div>
      <p className="muted">
        {parse
          ? "使用已保存剧本与角色设定，最多24镜。已有分镜须明确确认替换，旧镜头与素材保留。"
          : "使用已保存的画面、动作与声音要求。英文镜头描述与台词原文分开，全局风格由系统统一加入。"}
      </p>
      {dirty && <p className="notice">来源有未保存的人工编辑，请先处理。</p>}
      {error && (
        <p className="notice error" role="alert">
          {error}
        </p>
      )}
      {intent ? (
        <div className="notice">
          <p>提交结果待确认；查询或重试将复用原版本与同一幂等键。</p>
          <button
            className="button secondary"
            disabled={pending}
            onClick={() => void submit(intent)}
          >
            查询或重试原文本提交
          </button>
        </div>
      ) : (
        <button
          className="button primary"
          disabled={
            readonly ||
            dirty ||
            !ready ||
            pending ||
            blocked ||
            current?.status === "RUNNING" ||
            current?.status === "PENDING"
          }
          onClick={() =>
            void submit({
              idempotency_key: crypto.randomUUID(),
              expected_project_version: projectVersion,
              expected_episode_version: episodeVersion,
              ...(parse ? {} : { expected_shot_version: shotVersion }),
            })
          }
        >
          {parse ? "AI 拆解已保存剧本" : "AI 扩写三类 Prompt"}
        </button>
      )}
      {blocked && (
        <p className="muted">
          {active.data?.runtime_state?.state === "ACTIVE"
            ? "当前有任务执行中，请查看顶部进度。"
            : active.data?.runtime_state?.state === "NEEDS_RESET"
              ? "本机执行状态待恢复，请在顶部核对。"
              : "正在确认本机任务状态。"}
        </p>
      )}
      {current && (
        <div className="generation-result" role="status">
          <p>
            文本任务 #{current.id} · {current.stage}
          </p>
          {current.status === "DONE" && (
            <p className="success">草稿已生成，打开审阅后再采纳。</p>
          )}
          {current.status === "FAILED" && (
            <p className="error">{current.error_message}</p>
          )}
        </div>
      )}
      {job.connection === "disconnected" && (
        <p className="notice">文本任务查询中断，将继续查询；不会重新生成。</p>
      )}
      {historyError && <p className="muted">{historyError}</p>}
      <div className="text-history">
        {visibleHistory.map((task) => (
          <button
            className="button secondary small"
            key={task.id}
            onClick={() => setOpened(task.id)}
          >
            审阅草稿 #{task.id}
          </button>
        ))}
      </div>
      {opened && (
        <DraftReview
          key={`${projectId}:${opened}`}
          taskId={opened}
          projectId={projectId}
          onApplied={onApplied}
        />
      )}
    </section>
  );
}
