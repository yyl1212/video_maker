"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  ApiError,
  apiRequest,
  type ProjectRead,
  type ShotRead,
} from "@/lib/api";
import type { components } from "@/lib/api-types";
import { useWorkbenchStore } from "@/stores/workbench";
import {
  useActiveTaskPolling,
  useTaskPolling,
} from "@/features/jobs/useTaskPolling";
import { DrawThingsSettings } from "./DrawThingsSettings";

type Intent = components["schemas"]["ImageJobSubmit"];
type Accepted = components["schemas"]["TaskAccepted"];

export function ImageGenerationPanel({
  project,
  episodeVersion,
  shot,
  onCompleted,
}: {
  project: ProjectRead;
  episodeVersion: number;
  shot: ShotRead;
  onCompleted: () => void;
}) {
  const [intent, setIntent] = useState<Intent | null>(null);
  const [jobId, setJobId] = useState<number | null>(null);
  const [pending, setPending] = useState(false);
  const [error, setError] = useState("");
  const requestRef = useRef<AbortController | null>(null);
  const alive = useRef(false);
  const completed = useRef<number | null>(null);
  const onCompletedRef = useRef(onCompleted);
  useEffect(() => {
    onCompletedRef.current = onCompleted;
  }, [onCompleted]);
  const intentKey = `vm:image-intent:${shot.id}`;
  const jobKey = `vm:image-job:${shot.id}`;
  const drafts = useWorkbenchStore((s) => s.drafts);
  const dirty = Boolean(
    drafts[`shot:${shot.id}`]?.dirty || drafts[`references:${shot.id}`]?.dirty,
  );
  const initialized = useRef(false);
  const received = useCallback(() => {
    if (initialized.current) return;
    initialized.current = true;
    try {
      const stored = sessionStorage.getItem(intentKey);
      if (stored) {
        const data: unknown = JSON.parse(stored);
        if (
          data &&
          typeof data === "object" &&
          "idempotency_key" in data &&
          typeof data.idempotency_key === "string"
        )
          setIntent(data as Intent);
      }
      const saved = Number(sessionStorage.getItem(jobKey));
      if (Number.isSafeInteger(saved) && saved > 0) setJobId(saved);
    } catch {
      /* 浏览器存储不可用时仍依赖服务器。 */
    }
  }, [intentKey, jobKey]);
  const active = useActiveTaskPolling(received);
  const job = useTaskPolling(jobId);
  const task = job.task?.shot_id === shot.id ? job.task : null;
  const inProgress = task?.status === "PENDING" || task?.status === "RUNNING";
  const runtime = active.data?.runtime_state;
  const blocked =
    active.connection !== "connected" || runtime?.state !== "IDLE";
  const readonly = shot.read_only || Boolean(project.archived_at);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      requestRef.current?.abort();
    };
  }, [intentKey, jobKey]);
  useEffect(() => {
    if (task?.status === "DONE" && task.id !== completed.current) {
      completed.current = task.id;
      onCompletedRef.current();
    }
  }, [task]);
  const refresh = useCallback(() => onCompletedRef.current(), []);
  async function submit(frozen: Intent) {
    if (pending) return;
    const request = new AbortController();
    requestRef.current = request;
    setPending(true);
    setError("");
    setIntent(frozen);
    // POST之前保存原始意图；响应丢失后只允许复用相同幂等键和版本。
    try {
      sessionStorage.setItem(intentKey, JSON.stringify(frozen));
    } catch {}
    try {
      const accepted = await apiRequest<Accepted>(
        `/shots/${shot.id}/image-jobs`,
        { method: "POST", signal: request.signal, body: frozen },
      );
      if (!alive.current || request.signal.aborted) return;
      setJobId(accepted.job_id);
      setIntent(null);
      try {
        sessionStorage.setItem(jobKey, String(accepted.job_id));
        sessionStorage.removeItem(intentKey);
      } catch {}
    } catch (reason: unknown) {
      if (!alive.current || request.signal.aborted) return;
      // 403/代理错误只拒绝本次重试，不能证明此前意图未接收。
      // 仅服务端幂等查询之后的明确业务拒绝允许重新建立意图。
      const rejectedBeforeAcceptance = new Set([
        "VERSION_CONFLICT",
        "IMAGE_INPUT_CHANGED",
        "SHOT_NOT_READY",
        "IMAGE_MODEL_UNSUPPORTED",
        "IMAGE_DIMENSIONS",
        "TASK_BUSY",
        "ASSET_CHANGED",
        "ARCHIVED",
        "SHOT_NOT_FOUND",
      ]);
      if (
        reason instanceof ApiError &&
        reason.status >= 400 &&
        reason.status < 500 &&
        reason.code !== null &&
        rejectedBeforeAcceptance.has(reason.code)
      ) {
        setIntent(null);
        try {
          sessionStorage.removeItem(intentKey);
        } catch {}
      }
      setError(
        reason instanceof Error && !(reason instanceof TypeError)
          ? reason.message
          : "连接中断，提交结果待确认，请查询或重试原提交",
      );
    } finally {
      if (alive.current && !request.signal.aborted) setPending(false);
    }
  }
  return (
    <section className="panel image-generation">
      <div className="section-heading">
        <div>
          <p className="eyebrow">本机创作 · 首帧</p>
          <h3>生成首帧</h3>
        </div>
        <span className="muted">Draw Things · 单任务</span>
      </div>
      <p className="muted">
        按已保存的Prompt、全局风格与{shot.references.length}
        张有序参考图生成。结果加入历史，当前首帧由你采用。
      </p>
      {dirty && (
        <p className="notice">先保存本镜文字与参考草稿，再生成首帧。</p>
      )}
      {runtime?.state === "ACTIVE" && (
        <p className="notice">当前有任务执行中，请在顶部查看进度。</p>
      )}
      {runtime?.state === "NEEDS_RESET" && (
        <p className="notice">本机任务状态待恢复，请在顶部核对并明确恢复。</p>
      )}
      {shot.status !== "READY" && (
        <p className="notice">{shot.configuration_errors.join("；")}</p>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {intent ? (
        <div className="notice">
          <p>
            有一笔提交结果待确认，重试会查询或复用原任务，不读取新的镜头参数。
          </p>
          <button
            className="button secondary"
            disabled={pending}
            onClick={() => void submit(intent)}
          >
            查询或重试原提交
          </button>
        </div>
      ) : (
        <button
          className="button primary"
          disabled={
            readonly ||
            pending ||
            dirty ||
            blocked ||
            inProgress ||
            shot.status !== "READY"
          }
          onClick={() =>
            void submit({
              idempotency_key: crypto.randomUUID(),
              expected_project_version: project.version,
              expected_episode_version: episodeVersion,
              expected_shot_version: shot.version,
              expected_image_input_hash: shot.image_input_hash,
            })
          }
        >
          {pending
            ? "正在提交…"
            : task?.status === "DONE"
              ? "重新生成首帧"
              : "生成首帧"}
        </button>
      )}
      {active.connection === "disconnected" && (
        <p className="muted">无法确认全局任务状态，连接恢复后可继续。</p>
      )}
      {task && (
        <div className="generation-result" role="status">
          <p>
            任务 #{task.id} · {task.stage}
          </p>
          {task.status === "DONE" && (
            <p className="success">
              首帧已生成，请在当前媒体版本中采用。
              <button className="text-button" onClick={refresh}>
                刷新历史版本
              </button>
            </p>
          )}
          {task.status === "FAILED" && (
            <p className="error">{task.error_message}</p>
          )}
          {inProgress && (
            <p className="muted">
              后台正在处理，可继续查看镜头；不会自动重试。
            </p>
          )}
        </div>
      )}
      {job.connection === "disconnected" && (
        <p className="notice">任务查询连接中断，将继续查询。</p>
      )}
      <DrawThingsSettings onSaved={refresh} />
    </section>
  );
}
