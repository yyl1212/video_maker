"use client";

import { useEffect, useState } from "react";
import { apiRequest, type EpisodeRead, type ShotRead } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { useWorkbenchStore } from "@/stores/workbench";
import { useFormAction } from "@/components/useFormAction";

type Review = components["schemas"]["DraftRead"];
type Value = Review["value"];
type Row = components["schemas"]["DraftShot"];
export type Applied = EpisodeRead | ShotRead;

export function DraftReview({
  taskId,
  projectId,
  onApplied,
}: {
  taskId: number;
  projectId: number;
  onApplied: (value: Applied) => void;
}) {
  const [data, setData] = useState<Review | null>(null);
  const [error, setError] = useState("");
  const [sequence, setSequence] = useState(0);
  const [confirm, setConfirm] = useState(false);
  const key = `ai-review:${projectId}:${taskId}`;
  const saved = useWorkbenchStore((s) => s.drafts[key]);
  const drafts = useWorkbenchStore((s) => s.drafts);
  const save = useWorkbenchStore((s) => s.setDraft);
  const clear = useWorkbenchStore((s) => s.clearDraft);
  const reviewedValue = saved?.kind === "ai-review" ? saved.value : data?.value;
  const action = useFormAction<Applied>(
    `ai-apply:${projectId}:${taskId}`,
    (result) => {
      clear(key);
      setData((current) =>
        current
          ? {
              ...current,
              value: reviewedValue ?? current.value,
              applied_at: new Date().toISOString(),
              apply_allowed: false,
              conflict_reason: "此草稿已采纳",
            }
          : current,
      );
      onApplied(result);
    },
  );
  useEffect(() => {
    const controller = new AbortController();
    apiRequest<Review>(`/jobs/${taskId}/draft?project_id=${projectId}`, {
      signal: controller.signal,
    })
      .then((result) => {
        if (controller.signal.aborted) return;
        if (result.task_id !== taskId || result.project_id !== projectId)
          throw new Error("草稿不属于当前来源");
        setData(result);
        setError("");
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted)
          setError(reason instanceof Error ? reason.message : "读取草稿失败");
      });
    return () => controller.abort();
  }, [taskId, projectId, sequence]);
  if (!data || data.task_id !== taskId || data.project_id !== projectId)
    return (
      <p className="notice" role={error ? "alert" : "status"}>
        {error || "正在读取文本草稿…"}
      </p>
    );
  const value: Value = saved?.kind === "ai-review" ? saved.value : data.value;
  const parse = "shots" in value;
  const sourceDirty = Boolean(
    (parse && drafts[`episode:${data.episode_id}`]?.dirty) ||
    data.existing_shot_ids.some(
      (id) => drafts[`shot:${id}`]?.dirty || drafts[`references:${id}`]?.dirty,
    ),
  );
  const readonly = Boolean(data.applied_at) || action.pending;
  const needsConfirm = parse && data.existing_shot_ids.length > 0;
  function change(next: Value) {
    save(key, {
      kind: "ai-review",
      value: next,
      baseVersion: data!.expected_version,
      dirty: true,
    });
  }
  function updateRow(index: number, fields: Partial<Row>) {
    if ("shots" in value)
      change({
        shots: value.shots.map((row, i) =>
          i === index ? { ...row, ...fields } : row,
        ),
      });
  }
  function replaceRows(rows: Row[]) {
    change({ shots: rows.map((row, i) => ({ ...row, order_index: i + 1 })) });
  }
  return (
    <section className="draft-review" aria-label={`审阅文本草稿${taskId}`}>
      <div className="section-heading">
        <h3>草稿 #{taskId} · 人工审阅</h3>
        <button
          className="text-button"
          disabled={action.pending}
          onClick={() => {
            setConfirm(false);
            setSequence((n) => n + 1);
          }}
        >
          刷新草稿状态
        </button>
      </div>
      <p className="muted">
        这里只编辑本次草稿。采纳前会重新核对已保存来源，结果不会自动进入原始表单。
      </p>
      {(error || action.error) && (
        <p className="notice error" role="alert">
          {error || action.error}
        </p>
      )}
      {data.conflict_reason && <p className="notice">{data.conflict_reason}</p>}
      {action.message && (
        <p className="success" role="status">
          {action.message}
        </p>
      )}
      {sourceDirty && (
        <p className="notice">
          来源仍有未保存的人工编辑，请先处理；草稿已保留。
        </p>
      )}
      <fieldset disabled={readonly}>
        {"shots" in value ? (
          <>
            {value.shots.map((row, index) => (
              <article className="ai-draft-shot" key={index}>
                <div className="section-heading">
                  <h4>分镜 {index + 1}</h4>
                  <div className="button-row">
                    <button
                      type="button"
                      className="text-button"
                      disabled={readonly || index === 0}
                      onClick={() => {
                        const rows = [...value.shots];
                        [rows[index - 1], rows[index]] = [
                          rows[index]!,
                          rows[index - 1]!,
                        ];
                        replaceRows(rows);
                      }}
                    >
                      上移草稿镜头
                    </button>
                    <button
                      type="button"
                      className="text-button danger"
                      disabled={readonly || value.shots.length === 1}
                      onClick={() =>
                        replaceRows(value.shots.filter((_, i) => i !== index))
                      }
                    >
                      移除草稿镜头
                    </button>
                  </div>
                </div>
                <label htmlFor={`${key}-${index}-description`}>
                  分镜{index + 1}画面描述
                </label>
                <textarea
                  id={`${key}-${index}-description`}
                  rows={2}
                  maxLength={4000}
                  value={row.raw_description}
                  onChange={(e) =>
                    updateRow(index, { raw_description: e.target.value })
                  }
                />
                <label htmlFor={`${key}-${index}-dialogue`}>
                  分镜{index + 1}台词
                </label>
                <textarea
                  id={`${key}-${index}-dialogue`}
                  rows={2}
                  maxLength={4000}
                  value={row.dialogue}
                  onChange={(e) =>
                    updateRow(index, { dialogue: e.target.value })
                  }
                />
                <label htmlFor={`${key}-${index}-audio`}>
                  分镜{index + 1}声音要求
                </label>
                <textarea
                  id={`${key}-${index}-audio`}
                  rows={2}
                  maxLength={4000}
                  value={row.audio_prompt}
                  onChange={(e) =>
                    updateRow(index, { audio_prompt: e.target.value })
                  }
                />
                <label htmlFor={`${key}-${index}-duration`}>
                  分镜{index + 1}目标时长（秒）
                </label>
                <input
                  id={`${key}-${index}-duration`}
                  type="number"
                  min={1}
                  max={8}
                  step={0.1}
                  value={row.duration_seconds}
                  onChange={(e) =>
                    updateRow(index, {
                      duration_seconds: Number(e.target.value),
                    })
                  }
                />
                <div className="tag-options">
                  {data.characters.map((character) => (
                    <label key={character.id}>
                      <input
                        type="checkbox"
                        aria-label={`分镜${index + 1}角色${character.name}`}
                        checked={row.character_ids.includes(character.id)}
                        onChange={(e) =>
                          updateRow(index, {
                            character_ids: e.target.checked
                              ? [...row.character_ids, character.id]
                              : row.character_ids.filter(
                                  (id) => id !== character.id,
                                ),
                          })
                        }
                      />
                      {character.name}
                    </label>
                  ))}
                </div>
              </article>
            ))}
            <button
              type="button"
              className="button secondary small"
              disabled={readonly || value.shots.length >= 24}
              onClick={() =>
                replaceRows([
                  ...value.shots,
                  {
                    order_index: value.shots.length + 1,
                    raw_description: "",
                    dialogue: "",
                    audio_prompt: "",
                    character_ids: [],
                    duration_seconds: 3,
                  },
                ])
              }
            >
              增加草稿镜头
            </button>
          </>
        ) : (
          (
            [
              ["image_prompt", "AI 生图Prompt草稿", 8000],
              ["video_prompt", "AI 视频Prompt草稿", 8000],
              ["audio_prompt", "AI 声音Prompt草稿", 4000],
            ] as const
          ).map(([field, label, max]) => (
            <div key={field}>
              <label htmlFor={`${key}-${field}`}>{label}</label>
              <textarea
                id={`${key}-${field}`}
                rows={3}
                maxLength={max}
                value={value[field]}
                onChange={(e) => change({ ...value, [field]: e.target.value })}
              />
            </div>
          ))
        )}
        {needsConfirm && (
          <label className="checkbox-label">
            <input
              type="checkbox"
              checked={confirm}
              onChange={(e) => setConfirm(e.target.checked)}
            />
            确认替换当前分镜，保留旧镜头与素材
          </label>
        )}
        <button
          className="button primary"
          type="button"
          disabled={
            readonly ||
            sourceDirty ||
            !data.apply_allowed ||
            (needsConfirm && !confirm)
          }
          onClick={() =>
            void action.run(
              (signal) =>
                apiRequest<Applied>(`/jobs/${taskId}/apply`, {
                  method: "POST",
                  signal,
                  body: {
                    project_id: projectId,
                    expected_version: data.expected_version,
                    edited_value: value,
                    replace_confirm: confirm,
                  },
                }),
              "草稿已采纳，正式内容已更新",
            )
          }
        >
          {parse ? "采纳分镜草稿" : "采纳Prompt草稿"}
        </button>
      </fieldset>
    </section>
  );
}
