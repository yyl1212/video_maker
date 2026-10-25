"use client";

import { useId } from "react";
import {
  apiRequest,
  type AssetRead,
  type ReferenceInput,
  type ShotRead,
} from "@/lib/api";
import { useWorkbenchStore, type ReferenceDraft } from "@/stores/workbench";
import { useFormAction } from "@/components/useFormAction";
import { ConflictReview } from "@/components/ConflictReview";
import { mediaUrl } from "@/features/assets/MediaPreview";

export function ReferencePicker({
  shot,
  assets,
  onSaved,
}: {
  shot: ShotRead;
  assets: AssetRead[];
  onSaved: (shot: ShotRead) => void;
}) {
  const id = useId();
  const key = `references:${shot.id}`;
  const saved = useWorkbenchStore((s) => s.drafts[key]);
  const setDraft = useWorkbenchStore((s) => s.setDraft);
  const clearDraft = useWorkbenchStore((s) => s.clearDraft);
  const draft: ReferenceDraft =
    saved?.kind === "references"
      ? saved
      : {
          kind: "references",
          value: shot.references.map((r) => ({
            asset_id: r.asset_id,
            role: r.role as ReferenceInput["role"],
          })),
          baseVersion: shot.version,
          dirty: false,
        };
  const action = useFormAction<ShotRead>(key, (s) => {
    clearDraft(key);
    onSaved(s);
  });
  function update(value: ReferenceInput[]) {
    setDraft(key, { ...draft, value, dirty: true });
  }
  function move(index: number, offset: number) {
    const value = [...draft.value];
    const item = value.splice(index, 1)[0];
    if (item) value.splice(index + offset, 0, item);
    update(value);
  }
  return (
    <section className="panel reference-picker">
      <div className="section-heading">
        <h2>参考图片</h2>
        <span className="pill">{draft.value.length} / 8</span>
      </div>
      <p className="muted">
        按顺序传入模型；选择角色文本不会自动绑定图片。也可以不使用参考图。
      </p>
      <fieldset disabled={shot.read_only || action.pending}>
        <div className="reference-options">
          {assets
            .filter((a) => a.type === "image")
            .map((a) => {
              const checked = draft.value.some((r) => r.asset_id === a.id);
              return (
                <label key={a.id}>
                  {/* eslint-disable-next-line @next/next/no-img-element */}
                  <img src={mediaUrl(a, true)} alt="" />
                  <input
                    type="checkbox"
                    aria-label={`参考 ${a.name}`}
                    checked={checked}
                    disabled={!checked && draft.value.length >= 8}
                    onChange={(e) =>
                      update(
                        e.target.checked
                          ? [...draft.value, { asset_id: a.id, role: "other" }]
                          : draft.value.filter((r) => r.asset_id !== a.id),
                      )
                    }
                  />
                  <span>{a.name}</span>
                </label>
              );
            })}
        </div>
        {assets.filter((a) => a.type === "image").length === 0 && (
          <p className="empty-inline">先到资产库导入图片，再为本镜绑定参考。</p>
        )}
        <ol className="reference-order">
          {draft.value.map((ref, index) => (
            <li key={ref.asset_id}>
              <span className="muted">Picture {index + 1}</span>
              <strong>
                {assets.find((a) => a.id === ref.asset_id)?.name ??
                  `不可用素材 #${ref.asset_id}`}
              </strong>
              <label className="sr-only" htmlFor={`${id}-${ref.asset_id}`}>
                参考用途 {index + 1}
              </label>
              <select
                id={`${id}-${ref.asset_id}`}
                value={ref.role ?? "other"}
                onChange={(e) =>
                  update(
                    draft.value.map((r, i) =>
                      i === index
                        ? {
                            ...r,
                            role: e.target.value as ReferenceInput["role"],
                          }
                        : r,
                    ),
                  )
                }
              >
                <option value="character">人物</option>
                <option value="scene">场景</option>
                <option value="other">其他</option>
              </select>
              <div className="row-actions">
                <button
                  type="button"
                  aria-label={`上移参考 ${index + 1}`}
                  disabled={index === 0}
                  onClick={() => move(index, -1)}
                >
                  ↑
                </button>
                <button
                  type="button"
                  aria-label={`下移参考 ${index + 1}`}
                  disabled={index === draft.value.length - 1}
                  onClick={() => move(index, 1)}
                >
                  ↓
                </button>
                <button
                  type="button"
                  aria-label={`移除参考 ${index + 1}`}
                  onClick={() =>
                    update(
                      draft.value.filter((r) => r.asset_id !== ref.asset_id),
                    )
                  }
                >
                  ×
                </button>
              </div>
            </li>
          ))}
        </ol>
        <div className="form-footer">
          <span className="muted">
            {draft.dirty ? "参考图有未保存修改" : "参考顺序已保存"}
          </span>
          <button
            className="button secondary"
            type="button"
            onClick={() =>
              void action.run(
                (signal) =>
                  apiRequest<ShotRead>(`/shots/${shot.id}/references`, {
                    method: "PUT",
                    signal,
                    body: {
                      expected_version: draft.baseVersion,
                      references: draft.value,
                    },
                  }),
                "参考图已保存",
              )
            }
          >
            保存参考图
          </button>
        </div>
      </fieldset>
      {(action.error || action.message) && (
        <p
          role={action.error ? "alert" : "status"}
          className={`notice ${action.error ? "error" : "success"}`}
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
            <p>
              最新参考顺序：{s.reference_asset_ids.join(" → ") || "未绑定"}
              ；画面：{s.raw_description}
            </p>
          )}
          onUse={(s) => {
            setDraft(key, { ...draft, baseVersion: s.version });
            onSaved(s);
            action.clearFeedback();
          }}
        />
      )}
    </section>
  );
}
