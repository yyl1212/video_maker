"use client";

import { useId } from "react";
import { apiRequest, type EpisodeRead } from "@/lib/api";
import { useWorkbenchStore, type EpisodeDraft } from "@/stores/workbench";
import { useFormAction } from "@/components/useFormAction";
import { ConflictReview } from "@/components/ConflictReview";

type Props = {
  projectId: number;
  episode?: EpisodeRead;
  readonly?: boolean;
  onSaved: (episode: EpisodeRead) => void;
};

export function EpisodeForm({
  projectId,
  episode,
  readonly = false,
  onSaved,
}: Props) {
  const id = useId();
  const key = episode ? `episode:${episode.id}` : `episode-new:${projectId}`;
  const stored = useWorkbenchStore((state) => state.drafts[key]);
  const setDraft = useWorkbenchStore((state) => state.setDraft);
  const clearDraft = useWorkbenchStore((state) => state.clearDraft);
  const draft: EpisodeDraft =
    stored?.kind === "episode"
      ? stored
      : {
          kind: "episode",
          value: {
            title: episode?.title ?? "",
            script_text: episode?.script_text ?? "",
          },
          baseVersion: episode?.version ?? 1,
          dirty: false,
        };
  const action = useFormAction<EpisodeRead>(key, (value) => {
    clearDraft(key);
    onSaved(value);
  });
  const locked = readonly || Boolean(episode?.archived_at);
  function update(values: Partial<EpisodeDraft["value"]>) {
    setDraft(key, {
      ...draft,
      value: { ...draft.value, ...values },
      dirty: true,
    });
  }

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        void action.run(
          (signal) =>
            apiRequest<EpisodeRead>(
              episode
                ? `/episodes/${episode.id}`
                : `/projects/${projectId}/episodes`,
              {
                method: episode ? "PATCH" : "POST",
                signal,
                body: {
                  ...draft.value,
                  ...(episode ? { expected_version: draft.baseVersion } : {}),
                },
              },
            ),
          episode ? "剧本已保存" : "剧集已创建",
        );
      }}
    >
      <fieldset disabled={locked || action.pending}>
        <label htmlFor={`${id}-title`}>剧集标题</label>
        <input
          id={`${id}-title`}
          value={draft.value.title}
          onChange={(e) => update({ title: e.target.value })}
          maxLength={200}
          placeholder="例如：第一集 · 清晨的来信"
          required
        />
        <label htmlFor={`${id}-script`}>原始剧本</label>
        <textarea
          id={`${id}-script`}
          className="script-editor"
          value={draft.value.script_text}
          onChange={(e) => update({ script_text: e.target.value })}
          maxLength={12000}
          rows={episode ? 15 : 7}
          placeholder="从这里写下或粘贴你的原创剧本。人物、场景、动作与对白，都可以保留在原文中。"
        />
        <div className="form-footer">
          <span className="muted">
            {draft.value.script_text.length.toLocaleString()} / 12,000 字
            {draft.dirty ? " · 未保存" : ""}
          </span>
          <button
            type="submit"
            className="button primary"
            disabled={locked || action.pending}
          >
            {action.pending ? "正在保存…" : episode ? "保存剧本" : "创建剧集"}
          </button>
        </div>
      </fieldset>
      {action.error && (
        <p role="alert" className="notice error">
          {action.error}
        </p>
      )}
      {action.error && episode && (
        <ConflictReview<EpisodeRead>
          scope={key}
          load={(signal) =>
            apiRequest<EpisodeRead>(`/episodes/${episode.id}`, { signal })
          }
          describe={(latest) => (
            <dl>
              <dt>剧集标题</dt>
              <dd>{latest.title}</dd>
              <dt>原始剧本</dt>
              <dd className="snapshot-script">
                {latest.script_text || "未填写"}
              </dd>
            </dl>
          )}
          onUse={(latest) => {
            setDraft(key, {
              ...draft,
              baseVersion: latest.version,
              dirty: true,
            });
            action.clearFeedback();
          }}
        />
      )}
      {action.message && (
        <p role="status" className="notice success">
          {action.message}
        </p>
      )}
    </form>
  );
}
