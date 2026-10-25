"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError, apiRequest } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { MediaPreview } from "@/features/assets/MediaPreview";
import {
  useActiveTaskPolling,
  useTaskPolling,
} from "@/features/jobs/useTaskPolling";
import { ExportHistory } from "./ExportHistory";
import { useWorkbenchStore, type ExportDraft } from "@/stores/workbench";

type Preview = components["schemas"]["ExportPreviewRead"];
type Intent = components["schemas"]["ExportSubmit"];
type Preset = NonNullable<Intent["output_profile"]>;
type Draft = ExportDraft["value"];
const explicitRejections = new Set([
  "VERSION_CONFLICT",
  "EXPORT_TIMELINE_CHANGED",
  "EXPORT_NOT_READY",
  "TASK_BUSY",
  "ASSET_CHANGED",
  "ARCHIVED",
  "EPISODE_NOT_FOUND",
]);

export function ExportTimeline({
  projectId,
  episodeId,
}: {
  projectId: number;
  episodeId: number;
}) {
  const draftKey = `export:${projectId}:${episodeId}`;
  const [draft, setDraft] = useState<Draft | null>(() => {
    const saved = useWorkbenchStore.getState().drafts[draftKey];
    return saved?.kind === "export" ? saved.value : null;
  });
  const saveDraft = useWorkbenchStore((s) => s.setDraft);
  const [candidate, setCandidate] = useState<Preview | null>(null);
  const [intent, setIntent] = useState<Intent | null>(null);
  const [jobId, setJobId] = useState<number | null>(null);
  const [pending, setPending] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const [error, setError] = useState("");
  const alive = useRef(false);
  const submitting = useRef(false);
  const requestRef = useRef<AbortController | null>(null);
  const refreshRef = useRef<AbortController | null>(null);
  const initialized = useRef(false);
  const intentKey = `vm:export-intent:${episodeId}`;
  const jobKey = `vm:export-job:${episodeId}`;
  const restore = useCallback(() => {
    if (initialized.current) return;
    initialized.current = true;
    try {
      const stored: unknown = JSON.parse(
        sessionStorage.getItem(intentKey) ?? "null",
      );
      if (
        stored &&
        typeof stored === "object" &&
        "idempotency_key" in stored &&
        typeof stored.idempotency_key === "string" &&
        "clips" in stored &&
        Array.isArray(stored.clips)
      )
        setIntent(stored as Intent);
      const saved = Number(sessionStorage.getItem(jobKey));
      if (Number.isSafeInteger(saved) && saved > 0) setJobId(saved);
    } catch {
      /* 本机存储不可用时仍可查服务端历史。 */
    }
  }, [intentKey, jobKey]);
  const active = useActiveTaskPolling(restore);
  const job = useTaskPolling(jobId);
  const task =
    job.task?.project_id === projectId &&
    job.task.episode_id === episodeId &&
    job.task.kind === "EXPORT"
      ? job.task
      : null;
  const inProgress = task?.status === "PENDING" || task?.status === "RUNNING";
  const locked = pending || Boolean(intent) || Boolean(draft?.source.read_only);
  const blocked =
    active.connection !== "connected" ||
    active.data?.runtime_state.state !== "IDLE";
  useEffect(() => {
    if (draft)
      saveDraft(draftKey, {
        kind: "export",
        value: draft,
        baseVersion: draft.source.episode_version,
        dirty: true,
      });
  }, [draft, draftKey, saveDraft]);
  useEffect(() => {
    alive.current = true;
    const controller = new AbortController();
    apiRequest<Preview>(`/episodes/${episodeId}/export-preview`, {
      signal: controller.signal,
    })
      .then((source) => {
        if (controller.signal.aborted) return;
        if (source.project_id !== projectId) {
          setError("剧集不属于当前项目，请返回重新选择");
          return;
        }
        const saved = useWorkbenchStore.getState().drafts[draftKey];
        if (saved?.kind === "export") setCandidate(source);
        else
          setDraft({
            source,
            selected: source.clips.map((row) => row.shot_id),
            acknowledgements: {},
            profile: "portrait",
          });
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted)
          setError(reason instanceof Error ? reason.message : "无法读取时间线");
      });
    return () => {
      alive.current = false;
      controller.abort();
      requestRef.current?.abort();
      refreshRef.current?.abort();
    };
  }, [episodeId, projectId, draftKey]);
  async function refreshSources() {
    refreshRef.current?.abort();
    const controller = new AbortController();
    refreshRef.current = controller;
    setRefreshing(true);
    setError("");
    try {
      const result = await apiRequest<Preview>(
        `/episodes/${episodeId}/export-preview`,
        { signal: controller.signal },
      );
      if (!alive.current || controller.signal.aborted) return;
      if (result.project_id !== projectId)
        throw new Error("来源不属于当前项目");
      setCandidate(result);
    } catch (reason: unknown) {
      if (alive.current && !controller.signal.aborted)
        setError(reason instanceof Error ? reason.message : "刷新来源失败");
    } finally {
      if (alive.current && !controller.signal.aborted) setRefreshing(false);
    }
  }
  function adoptSources() {
    if (!candidate || !draft) return;
    // 只在明确采纳后替换来源；保留用户重排，新镜头不自动加入，所有旧确认清空。
    const remaining = new Map(candidate.clips.map((row) => [row.shot_id, row]));
    const ordered = draft.source.clips.flatMap((row) => {
      const next = remaining.get(row.shot_id);
      remaining.delete(row.shot_id);
      return next ? [next] : [];
    });
    setDraft({
      ...draft,
      source: { ...candidate, clips: [...ordered, ...remaining.values()] },
      selected: draft.selected.filter((id) =>
        candidate.clips.some((row) => row.shot_id === id),
      ),
      acknowledgements: {},
    });
    setCandidate(null);
    setError("");
  }
  function move(id: number, delta: number) {
    if (!draft) return;
    const clips = [...draft.source.clips],
      index = clips.findIndex((row) => row.shot_id === id),
      target = index + delta;
    if (index < 0 || target < 0 || target >= clips.length) return;
    [clips[index], clips[target]] = [clips[target]!, clips[index]!];
    setDraft({ ...draft, source: { ...draft.source, clips } });
  }
  async function submit(frozen: Intent) {
    if (submitting.current) return;
    submitting.current = true;
    const controller = new AbortController();
    requestRef.current = controller;
    setPending(true);
    setError("");
    setIntent(frozen);
    try {
      sessionStorage.setItem(intentKey, JSON.stringify(frozen));
    } catch {}
    try {
      const accepted = await apiRequest<components["schemas"]["TaskAccepted"]>(
        `/episodes/${episodeId}/export-jobs`,
        { method: "POST", body: frozen, signal: controller.signal },
      );
      if (!alive.current || controller.signal.aborted) return;
      setJobId(accepted.job_id);
      setIntent(null);
      try {
        sessionStorage.setItem(jobKey, String(accepted.job_id));
        sessionStorage.removeItem(intentKey);
      } catch {}
    } catch (reason: unknown) {
      if (!alive.current || controller.signal.aborted) return;
      // 网络/403无法证明旧意图未接收，仅明确业务拒绝才能建立新意图。
      if (
        reason instanceof ApiError &&
        reason.status >= 400 &&
        reason.status < 500 &&
        reason.code &&
        explicitRejections.has(reason.code)
      ) {
        setIntent(null);
        try {
          sessionStorage.removeItem(intentKey);
        } catch {}
      }
      setError(
        reason instanceof Error && !(reason instanceof TypeError)
          ? reason.message
          : "连接中断，导出提交结果待确认，请查询或重试原导出",
      );
    } finally {
      submitting.current = false;
      if (alive.current && !controller.signal.aborted) setPending(false);
    }
  }
  if (!draft)
    return (
      <p
        role={error ? "alert" : "status"}
        className={error ? "error notice" : "muted"}
      >
        {error || "正在读取导出时间线…"}
      </p>
    );
  const selected = draft.source.clips.filter((row) =>
    draft.selected.includes(row.shot_id),
  );
  const invalid = selected.some(
    (row) =>
      !row.available ||
      (row.stale && draft.acknowledgements[row.shot_id] !== row.review_token),
  );
  const duration = selected.reduce(
    (sum, row) => sum + (row.normalized_duration ?? 0),
    0,
  );
  return (
    <>
      <Link
        className="back-link"
        href={`/projects/${projectId}/episodes/${episodeId}`}
      >
        ← {draft.source.episode_title} · 分镜工作台
      </Link>
      <header className="page-heading">
        <div>
          <p className="eyebrow">{draft.source.project_name} / 本机成片</p>
          <h1>把镜头连成故事</h1>
          <p className="lede">勾选片段、调整顺序，保留每一段声音。</p>
        </div>
        <button
          className="button secondary"
          disabled={pending || Boolean(intent) || refreshing}
          onClick={() => void refreshSources()}
        >
          {refreshing ? "正在核对来源…" : "刷新来源"}
        </button>
      </header>
      {error && (
        <p className="notice error" role="alert">
          {error}
        </p>
      )}
      {candidate && (
        <aside className="notice">
          <p>
            最新来源已读取：{candidate.clips.length}{" "}
            镜。采用后保留现有排序、移除已删除镜头，新镜头默认不勾选；所有旧片段确认将清空。
          </p>
          <div className="button-row">
            <button
              className="button secondary"
              disabled={pending || Boolean(intent)}
              onClick={adoptSources}
            >
              使用最新来源并保留时间线排序
            </button>
            <button className="text-button" onClick={() => setCandidate(null)}>
              保持当前配置
            </button>
          </div>
        </aside>
      )}
      {draft.source.read_only && (
        <p className="notice">
          剧集或项目已归档，可查看并下载历史成片，恢复后可继续导出。
        </p>
      )}
      <section className="panel">
        <div className="section-heading">
          <div>
            <p className="eyebrow">时间线 · 最多 24 镜</p>
            <h2>{draft.source.episode_title}</h2>
          </div>
          <span className="muted">
            已选 {selected.length} 镜 · 约 {duration.toFixed(2)} 秒
          </span>
        </div>
        <p className="muted">
          使用各镜头已采用的视频。缺失片段需补齐或明确取消勾选；输出统一为
          24fps、有声双声道，按比例补边。
        </p>
        <ol className="export-timeline">
          {draft.source.clips.map((row, index) => (
            <li
              className="timeline-row"
              data-testid="timeline-row"
              key={row.shot_id}
            >
              <div className="timeline-row-heading">
                <label className="checkbox-label">
                  <input
                    type="checkbox"
                    aria-label={`包含镜头${row.shot_id}`}
                    checked={draft.selected.includes(row.shot_id)}
                    disabled={locked}
                    onChange={(e) =>
                      setDraft({
                        ...draft,
                        selected: e.target.checked
                          ? [...draft.selected, row.shot_id]
                          : draft.selected.filter((id) => id !== row.shot_id),
                      })
                    }
                  />
                  <strong>
                    {index + 1}. 镜头 {row.order_index}
                  </strong>
                </label>
                <div className="button-row">
                  <button
                    className="text-button"
                    aria-label={`上移镜头${row.shot_id}`}
                    disabled={locked || index === 0}
                    onClick={() => move(row.shot_id, -1)}
                  >
                    上移
                  </button>
                  <button
                    className="text-button"
                    aria-label={`下移镜头${row.shot_id}`}
                    disabled={locked || index === draft.source.clips.length - 1}
                    onClick={() => move(row.shot_id, 1)}
                  >
                    下移
                  </button>
                </div>
              </div>
              <p>{row.description || "未填写画面描述"}</p>
              <p className="muted">
                {row.asset
                  ? `${row.asset.name} · #${row.asset.id} · ${row.asset.has_audio ? "有声音" : "无声音，将补静音"}`
                  : "未选择可用片段"}
                {row.source_duration != null &&
                  ` · 原始 ${row.source_duration.toFixed(3)} 秒 → 输出 ${row.normalized_duration?.toFixed(3)} 秒`}
              </p>
              {row.issues.length > 0 && (
                <p className="notice">{row.issues.join("；")}</p>
              )}
              {row.stale && row.available && (
                <label className="checkbox-label">
                  <input
                    type="checkbox"
                    aria-label={`确认使用镜头${row.shot_id}的旧片段`}
                    disabled={locked}
                    checked={
                      draft.acknowledgements[row.shot_id] === row.review_token
                    }
                    onChange={(e) =>
                      setDraft({
                        ...draft,
                        acknowledgements: {
                          ...draft.acknowledgements,
                          [row.shot_id]: e.target.checked
                            ? row.review_token
                            : "",
                        },
                      })
                    }
                  />
                  确认使用该镜头的旧片段
                </label>
              )}
              {row.asset && row.available && (
                <details>
                  <summary>播放片段</summary>
                  <MediaPreview asset={row.asset} />
                </details>
              )}
            </li>
          ))}
        </ol>
        {!draft.source.clips.length && (
          <p className="notice">
            剧集尚无镜头，请返回工作台创建分镜并选择视频。
          </p>
        )}
        <div className="export-actions">
          <label>
            成片比例
            <select
              aria-label="成片比例"
              value={draft.profile}
              disabled={locked}
              onChange={(e) =>
                setDraft({ ...draft, profile: e.target.value as Preset })
              }
            >
              <option value="portrait">竖屏 · 720 × 1280</option>
              <option value="landscape">横屏 · 1280 × 720</option>
              <option value="square">方形 · 720 × 720</option>
            </select>
          </label>
          <p className="muted">H.264 / AAC · 48kHz · MP4</p>
          {intent ? (
            <div className="notice">
              <p>有一笔导出结果待确认，重试将使用原顺序、来源与同一幂等键。</p>
              <button
                className="button secondary"
                disabled={pending}
                onClick={() => void submit(intent)}
              >
                查询或重试原导出
              </button>
            </div>
          ) : (
            <button
              className="button primary"
              disabled={
                locked ||
                blocked ||
                inProgress ||
                invalid ||
                selected.length === 0 ||
                selected.length > 24 ||
                refreshing ||
                Boolean(candidate)
              }
              onClick={() =>
                void submit({
                  idempotency_key: crypto.randomUUID(),
                  expected_project_version: draft.source.project_version,
                  expected_episode_version: draft.source.episode_version,
                  output_profile: draft.profile,
                  clips: selected.map((row) => ({
                    shot_id: row.shot_id,
                    review_token: row.review_token,
                    confirm_stale:
                      row.stale &&
                      draft.acknowledgements[row.shot_id] === row.review_token,
                  })),
                })
              }
            >
              导出 MP4
            </button>
          )}
        </div>
        {invalid && (
          <p className="notice">请处理所选镜头的缺失片段或旧版本确认。</p>
        )}
        {blocked && (
          <p className="muted">
            {active.data?.runtime_state.state === "ACTIVE"
              ? "当前有任务执行中，请查看顶部进度。"
              : active.data?.runtime_state.state === "NEEDS_RESET"
                ? "本机任务状态待恢复，请在顶部核对。"
                : "正在确认本机任务状态。"}
          </p>
        )}
        {task && (
          <div className="generation-result" role="status">
            <p>
              导出任务 #{task.id} · {task.stage}
            </p>
            {task.status === "DONE" && (
              <p className="success">
                成片已保存，可在下方播放或下载。时间线配置已保留。
              </p>
            )}
            {task.status === "FAILED" && (
              <p className="error">{task.error_message}</p>
            )}
            {inProgress && (
              <p className="muted">
                后台正在导出，离开页面后仍可从历史查看结果。
              </p>
            )}
          </div>
        )}
        {job.connection === "disconnected" && (
          <p className="notice">导出查询连接中断，将继续查询；不会重新提交。</p>
        )}
      </section>
      <ExportHistory
        episodeId={episodeId}
        projectId={projectId}
        revision={task?.status === "DONE" ? task.id : 0}
      />
    </>
  );
}
