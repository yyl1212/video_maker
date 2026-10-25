"use client";

import { useId } from "react";
import {
  apiRequest,
  type ShotRead,
  type ProjectRead,
  type ShotEditable,
} from "@/lib/api";
import { useWorkbenchStore, type ShotDraft } from "@/stores/workbench";
import { useFormAction } from "@/components/useFormAction";
import { ConflictReview } from "@/components/ConflictReview";

function editable(shot: ShotRead): ShotEditable {
  return {
    raw_description: shot.raw_description,
    dialogue: shot.dialogue,
    character_ids: shot.character_ids,
    image_prompt: shot.image_prompt,
    video_prompt: shot.video_prompt,
    audio_prompt: shot.audio_prompt,
    duration_seconds: shot.duration_seconds,
    image_settings: shot.image_settings,
    video_settings: {
      ...shot.video_settings,
      width:
        typeof shot.video_settings.width === "number"
          ? shot.video_settings.width
          : 384,
      height:
        typeof shot.video_settings.height === "number"
          ? shot.video_settings.height
          : 672,
    },
  };
}

export function ShotEditor({
  shot,
  project,
  onSaved,
}: {
  shot: ShotRead;
  project: ProjectRead;
  onSaved: (shot: ShotRead) => void;
}) {
  const id = useId();
  const key = `shot:${shot.id}`;
  const saved = useWorkbenchStore((s) => s.drafts[key]);
  const setDraft = useWorkbenchStore((s) => s.setDraft);
  const clearDraft = useWorkbenchStore((s) => s.clearDraft);
  const draft: ShotDraft =
    saved?.kind === "shot"
      ? saved
      : {
          kind: "shot",
          value: editable(shot),
          baseVersion: shot.version,
          dirty: false,
        };
  const action = useFormAction<ShotRead>(key, (result) => {
    clearDraft(key);
    onSaved(result);
  });
  const readonly = shot.read_only || Boolean(project.archived_at);
  function update(fields: Partial<ShotEditable>) {
    setDraft(key, {
      ...draft,
      value: { ...draft.value, ...fields },
      dirty: true,
    });
  }
  const textFields = [
    ["raw_description", "画面描述", 4000],
    ["dialogue", "台词", 4000],
    ["image_prompt", "生图Prompt", 8000],
    ["video_prompt", "视频Prompt", 8000],
    ["audio_prompt", "声音Prompt", 4000],
  ] as const;
  return (
    <section className="panel shot-editor">
      <div className="section-heading">
        <div>
          <p className="eyebrow">
            镜头 {String(shot.order_index).padStart(2, "0")}
          </p>
          <h2>把想象写进画面</h2>
        </div>
        <span className="pill">
          {shot.status === "READY" ? "生图配置就绪" : "草稿"}
        </span>
      </div>
      <p className="muted">
        全局风格将由系统统一加入。当前设置：
        {project.style_anchor || "尚未设置全局风格"}
      </p>
      {draft.baseVersion !== shot.version && (
        <p className="notice">本页保留旧版本草稿。保存前请核对最新分镜。</p>
      )}
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void action.run(
            (signal) =>
              apiRequest<ShotRead>(`/shots/${shot.id}`, {
                method: "PATCH",
                signal,
                body: { ...draft.value, expected_version: draft.baseVersion },
              }),
            "分镜文字与参数已保存",
          );
        }}
      >
        <fieldset disabled={readonly || action.pending}>
          {textFields.map(([field, name, max]) => (
            <div key={field}>
              <label htmlFor={`${id}-${field}`}>{name}</label>
              <textarea
                id={`${id}-${field}`}
                value={draft.value[field]}
                rows={field === "raw_description" ? 3 : 2}
                maxLength={max}
                onChange={(e) => update({ [field]: e.target.value })}
              />
            </div>
          ))}
          <label htmlFor={`${id}-duration`}>目标视频时长（秒）</label>
          <input
            id={`${id}-duration`}
            type="number"
            min={1}
            max={8}
            step={0.1}
            value={draft.value.duration_seconds}
            required
            onChange={(e) =>
              update({ duration_seconds: Number(e.target.value) })
            }
          />
          <p className="muted">
            已保存参数对应的H3实际时长：
            {shot.actual_duration_seconds.toFixed(2)}秒 / 24 fps。
          </p>
          <div className="tag-options" aria-label="出镜角色">
            {project.characters.map((c) => (
              <label key={c.id}>
                <input
                  type="checkbox"
                  checked={draft.value.character_ids.includes(c.id)}
                  onChange={(e) =>
                    update({
                      character_ids: e.target.checked
                        ? [...draft.value.character_ids, c.id]
                        : draft.value.character_ids.filter((x) => x !== c.id),
                    })
                  }
                />
                {c.name}
              </label>
            ))}
            {project.characters.length === 0 && (
              <p className="muted">
                在项目设置中添加角色后，可为本镜选择出镜人物。
              </p>
            )}
          </div>
          {draft.value.character_ids.some(
            (c) => !project.characters.some((p) => p.id === c),
          ) && (
            <p className="notice">
              部分角色已移除。
              <button
                type="button"
                className="text-button"
                onClick={() =>
                  update({
                    character_ids: draft.value.character_ids.filter((c) =>
                      project.characters.some((p) => p.id === c),
                    ),
                  })
                }
              >
                清除失效角色
              </button>
            </p>
          )}
          <details className="advanced-settings">
            <summary>镜头生成参数</summary>
            <p className="muted">
              图片留空时使用项目外的本机默认配置。参数保存在本镜头。
            </p>
            <div className="parameter-grid">
              {(
                ["width", "height", "steps", "seed", "guidance_scale"] as const
              ).map((field) => {
                const label = {
                  width: "图片宽度",
                  height: "图片高度",
                  steps: "图片步数",
                  seed: "图片种子",
                  guidance_scale: "引导强度",
                }[field];
                return (
                  <label key={field} htmlFor={`${id}-image-${field}`}>
                    {label}
                    <input
                      id={`${id}-image-${field}`}
                      type="number"
                      step={field === "guidance_scale" ? 0.1 : 1}
                      value={draft.value.image_settings[field] ?? ""}
                      min={
                        field === "seed" || field === "guidance_scale" ? 0 : 1
                      }
                      onChange={(e) =>
                        update({
                          image_settings: {
                            ...draft.value.image_settings,
                            [field]:
                              e.target.value === ""
                                ? undefined
                                : Number(e.target.value),
                          },
                        })
                      }
                    />
                  </label>
                );
              })}
            </div>
            <label htmlFor={`${id}-negative`}>反向提示词</label>
            <textarea
              id={`${id}-negative`}
              value={draft.value.image_settings.negative_prompt ?? ""}
              maxLength={4000}
              rows={2}
              onChange={(e) =>
                update({
                  image_settings: {
                    ...draft.value.image_settings,
                    negative_prompt: e.target.value,
                  },
                })
              }
            />
            <div className="parameter-grid">
              <label htmlFor={`${id}-preset`}>
                视频档位
                <select
                  id={`${id}-preset`}
                  value={draft.value.video_settings.preset ?? ""}
                  onChange={(e) =>
                    update({
                      video_settings: {
                        ...draft.value.video_settings,
                        preset: (e.target.value || undefined) as
                          "quality" | "fast" | undefined,
                      },
                    })
                  }
                >
                  <option value="">使用默认档位</option>
                  <option value="quality">质量 · 20步</option>
                  <option value="fast">快速 · 4步</option>
                </select>
              </label>
              <label htmlFor={`${id}-size`}>
                视频画幅
                <select
                  id={`${id}-size`}
                  value={`${draft.value.video_settings.width ?? 384}x${draft.value.video_settings.height ?? 672}`}
                  onChange={(e) => {
                    const parts = e.target.value.split("x");
                    update({
                      video_settings: {
                        ...draft.value.video_settings,
                        width: Number(parts[0]),
                        height: Number(parts[1]),
                      },
                    });
                  }}
                >
                  <option value="384x672">竖屏 · 384 × 672</option>
                  <option value="672x384">横屏 · 672 × 384</option>
                  <option value="384x384">方形 · 384 × 384</option>
                </select>
              </label>
              <label htmlFor={`${id}-video-seed`}>
                视频种子
                <input
                  id={`${id}-video-seed`}
                  type="number"
                  min={0}
                  max={4294967295}
                  value={draft.value.video_settings.seed ?? ""}
                  placeholder="默认"
                  onChange={(e) =>
                    update({
                      video_settings: {
                        ...draft.value.video_settings,
                        seed:
                          e.target.value === ""
                            ? undefined
                            : Number(e.target.value),
                      },
                    })
                  }
                />
              </label>
            </div>
          </details>
          <div className="form-footer">
            <span className="muted">
              {draft.dirty ? "有未保存修改" : `版本 ${shot.version}`}
            </span>
            <button className="button primary">
              {action.pending ? "正在保存…" : "保存分镜文字与参数"}
            </button>
          </div>
        </fieldset>
      </form>
      {shot.configuration_errors.length > 0 && (
        <p className="notice">{shot.configuration_errors.join("；")}</p>
      )}
      {(action.error || action.message) && (
        <p
          className={`notice ${action.error ? "error" : "success"}`}
          role={action.error ? "alert" : "status"}
        >
          {action.error || action.message}
        </p>
      )}
      {(action.error || draft.baseVersion !== shot.version) && (
        <ConflictReview<ShotRead>
          scope={key}
          load={(signal) =>
            apiRequest<ShotRead>(`/shots/${shot.id}`, { signal })
          }
          describe={(s) => (
            <>
              <p>{s.raw_description || "未填写画面描述"}</p>
              <p>{s.image_prompt || "未填写生图Prompt"}</p>
            </>
          )}
          onUse={(s) => {
            setDraft(key, { ...draft, baseVersion: s.version });
            onSaved(s);
            action.clearFeedback();
          }}
        />
      )}
      <details className="compiled-preview">
        <summary>查看已保存配置的完整Prompt</summary>
        <h3>生图</h3>
        <p>{shot.compiled_image_prompt}</p>
        <h3>视频与声音</h3>
        <p>{shot.compiled_video_prompt}</p>
      </details>
    </section>
  );
}
