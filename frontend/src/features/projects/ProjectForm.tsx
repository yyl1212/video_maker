"use client";

import { useId } from "react";
import { apiRequest, type Character, type ProjectRead } from "@/lib/api";
import { useWorkbenchStore, type ProjectDraft } from "@/stores/workbench";
import { useFormAction } from "@/components/useFormAction";
import { ConflictReview } from "@/components/ConflictReview";

type Props = { project?: ProjectRead; onSaved: (project: ProjectRead) => void };

export function ProjectForm({ project, onSaved }: Props) {
  const id = useId();
  const key = project ? `project:${project.id}` : "project:new";
  const savedDraft = useWorkbenchStore((state) => state.drafts[key]);
  const setDraft = useWorkbenchStore((state) => state.setDraft);
  const clearDraft = useWorkbenchStore((state) => state.clearDraft);
  const draft: ProjectDraft =
    savedDraft?.kind === "project"
      ? savedDraft
      : {
          kind: "project",
          value: {
            name: project?.name ?? "",
            style_anchor: project?.style_anchor ?? "",
            characters: project?.characters ?? [],
          },
          baseVersion: project?.version ?? 1,
          dirty: false,
        };
  const action = useFormAction<ProjectRead>(key, (result) => {
    clearDraft(key);
    onSaved(result);
  });
  const readonly = Boolean(project?.archived_at);
  function update(values: Partial<ProjectDraft["value"]>) {
    setDraft(key, {
      ...draft,
      value: { ...draft.value, ...values },
      dirty: true,
    });
  }
  function changeCharacter(index: number, values: Partial<Character>) {
    update({
      characters: draft.value.characters.map((item, i) =>
        i === index ? { ...item, ...values } : item,
      ),
    });
  }

  return (
    <form
      onSubmit={(event) => {
        event.preventDefault();
        void action.run(
          (signal) =>
            apiRequest<ProjectRead>(
              project ? `/projects/${project.id}` : "/projects",
              {
                method: project ? "PATCH" : "POST",
                signal,
                body: {
                  ...draft.value,
                  ...(project ? { expected_version: draft.baseVersion } : {}),
                },
              },
            ),
          project ? "项目设置已保存" : "项目已创建",
        );
      }}
    >
      <fieldset disabled={readonly || action.pending}>
        <label htmlFor={`${id}-name`}>项目名称</label>
        <input
          id={`${id}-name`}
          value={draft.value.name}
          onChange={(e) => update({ name: e.target.value })}
          maxLength={120}
          placeholder="为你的故事起一个名字"
          required
        />
        <label htmlFor={`${id}-style`}>全局风格描述</label>
        <textarea
          id={`${id}-style`}
          value={draft.value.style_anchor}
          onChange={(e) => update({ style_anchor: e.target.value })}
          maxLength={4000}
          rows={3}
          placeholder="例如：暖色自然光、纪实摄影、克制的镜头语言"
        />
        <div className="section-heading compact">
          <div>
            <h3>角色设定</h3>
            <p className="muted">在本项目的剧集中保持角色一致。</p>
          </div>
          <button
            type="button"
            className="button secondary small"
            onClick={() =>
              update({
                characters: [
                  ...draft.value.characters,
                  { id: crypto.randomUUID(), name: "", description: "" },
                ],
              })
            }
          >
            添加角色
          </button>
        </div>
        {draft.value.characters.length === 0 && (
          <p className="empty-inline">尚未添加角色，也可以稍后补充。</p>
        )}
        {draft.value.characters.map((character, index) => (
          <div className="character" key={character.id}>
            <div className="section-heading compact">
              <span className="eyebrow">
                角色 {String(index + 1).padStart(2, "0")}
              </span>
              <button
                type="button"
                className="text-button danger"
                aria-label={`移除角色 ${index + 1}`}
                onClick={() =>
                  update({
                    characters: draft.value.characters.filter(
                      (_, i) => i !== index,
                    ),
                  })
                }
              >
                移除
              </button>
            </div>
            <label
              htmlFor={`${id}-character-${character.id}`}
              className="sr-only"
            >
              角色名称 {index + 1}
            </label>
            <input
              id={`${id}-character-${character.id}`}
              value={character.name}
              onChange={(e) => changeCharacter(index, { name: e.target.value })}
              maxLength={120}
              placeholder="角色名称"
              required
            />
            <label
              htmlFor={`${id}-description-${character.id}`}
              className="sr-only"
            >
              角色描述 {index + 1}
            </label>
            <textarea
              id={`${id}-description-${character.id}`}
              value={character.description}
              onChange={(e) =>
                changeCharacter(index, { description: e.target.value })
              }
              maxLength={4000}
              rows={2}
              placeholder="外貌、服装、性格，以及需要保持一致的细节"
            />
          </div>
        ))}
        <div className="form-footer">
          <span className="muted">
            {readonly
              ? "项目已归档，恢复后可继续编辑"
              : draft.dirty
                ? "有未保存的修改"
                : "内容保存在本机"}
          </span>
          <button
            type="submit"
            className="button primary"
            disabled={readonly || action.pending}
          >
            {action.pending
              ? "正在保存…"
              : project
                ? "保存项目设置"
                : "创建项目"}
          </button>
        </div>
      </fieldset>
      {action.error && (
        <p role="alert" className="notice error">
          {action.error}
        </p>
      )}
      {action.error && project && (
        <ConflictReview<ProjectRead>
          scope={key}
          load={(signal) =>
            apiRequest<ProjectRead>(`/projects/${project.id}`, { signal })
          }
          describe={(latest) => (
            <dl>
              <dt>项目名称</dt>
              <dd>{latest.name}</dd>
              <dt>全局风格</dt>
              <dd>{latest.style_anchor || "未填写"}</dd>
              <dt>角色设定</dt>
              <dd>
                {latest.characters
                  .map((item) => `${item.name}：${item.description}`)
                  .join("；") || "未添加"}
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
