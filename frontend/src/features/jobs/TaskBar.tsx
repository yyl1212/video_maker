"use client";

import Link from "next/link";
import { useCallback, useId, useState } from "react";
import { apiRequest, type ActiveJobRead, type RuntimeRead } from "@/lib/api";
import { useFormAction } from "@/components/useFormAction";
import { useActiveTaskPolling, useTaskPolling } from "./useTaskPolling";

const storageKey = "video-maker:last-job-id";
const backendNames = {
  llm: "本机文本服务",
  drawthings: "Draw Things",
  mlx: "本机视频服务",
  ffmpeg: "本机媒体进程",
  local: "本机执行环境",
} as const;
type Backend = keyof typeof backendNames;
const kindNames = {
  PARSE_SCRIPT: "剧本解析",
  OPTIMIZE_PROMPTS: "提示词扩写",
  IMAGE: "首帧图片",
  VIDEO: "视频与声音",
  EXPORT: "成片导出",
} as const;
const statusNames = {
  PENDING: "等待启动",
  RUNNING: "执行中",
  DONE: "已完成",
  FAILED: "未完成",
} as const;

function readStoredId() {
  try {
    const value = Number(sessionStorage.getItem(storageKey));
    return Number.isSafeInteger(value) && value > 0 ? value : null;
  } catch {
    return null;
  }
}
function rememberId(id: number | null) {
  try {
    if (id === null) sessionStorage.removeItem(storageKey);
    else sessionStorage.setItem(storageKey, String(id));
  } catch {
    /* 禁用浏览器存储时仍以服务端全局状态为准。 */
  }
}

export function TaskBar() {
  const id = useId();
  const [jobId, setJobId] = useState<number | null>(null);
  const [confirmed, setConfirmed] = useState<{
    version: number;
    backend: Backend;
  } | null>(null);
  const [resetResult, setResetResult] = useState<RuntimeRead | null>(null);
  const received = useCallback((value: ActiveJobRead) => {
    const activeId = value.task?.id ?? value.runtime_state.active_task_id;
    if (activeId !== null && activeId !== undefined) {
      rememberId(activeId);
      setJobId(activeId);
    } else {
      setJobId((previous) => previous ?? readStoredId());
    }
  }, []);
  const discovery = useActiveTaskPolling(received);
  const polling = useTaskPolling(jobId);
  const runtime =
    resetResult &&
    (!discovery.data ||
      resetResult.version > discovery.data.runtime_state.version)
      ? resetResult
      : discovery.data?.runtime_state;
  const task =
    polling.task ??
    (discovery.data?.task?.id === jobId ? discovery.data.task : null);
  const reset = useFormAction<RuntimeRead>("runtime-reset", (value) => {
    setConfirmed(null);
    setResetResult(value);
    discovery.refresh();
  });
  const backend =
    runtime?.affected_backend && runtime.affected_backend in backendNames
      ? (runtime.affected_backend as Backend)
      : null;
  const mayReset = Boolean(
    runtime?.state === "NEEDS_RESET" &&
    backend &&
    confirmed?.version === runtime.version &&
    confirmed.backend === backend,
  );
  const active = task?.status === "PENDING" || task?.status === "RUNNING";
  const error = discovery.error || polling.error || reset.error;
  return (
    <section
      className={`global-taskbar ${runtime?.state === "NEEDS_RESET" ? "needs-reset" : ""}`}
      aria-label="全局制作任务"
    >
      <div className="task-heading">
        <div>
          <span className={`task-indicator ${active ? "busy" : ""}`} />
          <strong>制作任务</strong>
          <span className="task-phase">
            {runtime?.state === "NEEDS_RESET"
              ? "待恢复执行状态"
              : discovery.connection === "disconnected" ||
                  polling.connection === "disconnected"
                ? "连接中断，状态待确认"
                : active
                  ? statusNames[task.status]
                  : runtime?.state === "ACTIVE"
                    ? "执行占位中"
                    : discovery.connection === "loading"
                      ? "正在读取…"
                      : "当前空闲"}
          </span>
        </div>
        <button
          className="text-button"
          onClick={() => {
            discovery.refresh();
            polling.refresh();
          }}
        >
          刷新任务状态
        </button>
      </div>
      {task && (
        <div className="task-detail">
          <div className="task-description">
            <span>
              {kindNames[task.kind]} · #{task.id}
            </span>
            <strong>{task.stage}</strong>
            {!active && (
              <span className="pill">{statusNames[task.status]}</span>
            )}
            {task.episode_id && (
              <Link
                className="text-button"
                href={`/projects/${task.project_id}/episodes/${task.episode_id}`}
              >
                查看所属剧集 →
              </Link>
            )}
          </div>
          {task.progress !== null && (
            <div className="task-progress">
              <progress aria-label="制作进度" max={1} value={task.progress} />
              <span>{Math.round(task.progress * 100)}%</span>
            </div>
          )}
          {task.error_message && (
            <p className="notice error">{task.error_message}</p>
          )}
          {task.result_summary && (
            <p className="muted">{task.result_summary}</p>
          )}
          {!active && runtime?.state === "IDLE" && (
            <button
              className="text-button"
              onClick={() => {
                setJobId(null);
                rememberId(null);
              }}
            >
              收起结果
            </button>
          )}
        </div>
      )}
      {runtime?.reason && <p className="notice">{runtime.reason}</p>}
      {runtime?.state === "NEEDS_RESET" && (
        <div className="runtime-reset">
          <p>
            受影响服务：{backend ? backendNames[backend] : "执行环境未知"}
            。先停止或重启服务，确认旧任务已经停止，再恢复制作。
          </p>
          <label htmlFor={id}>
            <input
              id={id}
              type="checkbox"
              checked={mayReset}
              disabled={reset.pending || !backend}
              onChange={(e) =>
                setConfirmed(
                  e.target.checked && backend
                    ? { version: runtime.version, backend }
                    : null,
                )
              }
            />
            我已停止或重启受影响服务，并确认任务已停止
          </label>
          <button
            className="button secondary small"
            disabled={!mayReset || reset.pending}
            onClick={() => {
              if (!confirmed || !mayReset) return;
              void reset.run(
                (signal) =>
                  apiRequest<RuntimeRead>("/runtime/reset", {
                    method: "POST",
                    signal,
                    body: {
                      expected_version: confirmed.version,
                      backend: confirmed.backend,
                      confirmed_stopped: true,
                    },
                  }),
                "执行状态已恢复",
              );
            }}
          >
            {reset.pending ? "正在恢复…" : "恢复执行状态"}
          </button>
        </div>
      )}
      {error && (
        <p role="alert" className="notice error">
          {error}
        </p>
      )}
      {!task &&
        jobId !== null &&
        runtime?.state === "IDLE" &&
        polling.connection === "disconnected" && (
          <button
            className="text-button"
            onClick={() => {
              setJobId(null);
              rememberId(null);
            }}
          >
            清除本页任务记录
          </button>
        )}
      {reset.message && (
        <p role="status" className="notice success">
          {reset.message}
        </p>
      )}
    </section>
  );
}
