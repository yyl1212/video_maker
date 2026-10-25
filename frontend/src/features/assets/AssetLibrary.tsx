"use client";

import { useEffect, useId, useState } from "react";
import { apiRequest, type AssetRead } from "@/lib/api";
import { useFormAction } from "@/components/useFormAction";
import { AssetUpload } from "./AssetUpload";
import { MediaPreview, mediaUrl } from "./MediaPreview";

const tags = {
  reference: "参考",
  character: "角色",
  scene: "场景",
  first_frame: "首帧",
  video_clip: "视频片段",
  final_video: "成片",
};

function AssetDetails({
  asset,
  readonly,
  onSaved,
}: {
  asset: AssetRead;
  readonly: boolean;
  onSaved: (asset: AssetRead) => void;
}) {
  const id = useId();
  const [name, setName] = useState(asset.name);
  const [chosen, setChosen] = useState(asset.tags);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [base, setBase] = useState(asset);
  const [latest, setLatest] = useState<AssetRead | null>(null);
  const action = useFormAction<AssetRead>(`asset:${asset.id}`, onSaved);
  const review = useFormAction<AssetRead>(
    `asset-review:${asset.id}`,
    setLatest,
  );
  return (
    <div className="panel asset-details">
      <div className="section-heading">
        <h2>{asset.name}</h2>
        <span className="pill">素材 #{asset.id}</span>
      </div>
      <MediaPreview asset={asset} />
      <form
        onSubmit={(e) => {
          e.preventDefault();
          void action.run(
            (signal) =>
              apiRequest<AssetRead>(`/assets/${asset.id}`, {
                method: "PATCH",
                signal,
                body: { expected_version: base.version, name, tags: chosen },
              }),
            "素材信息已保存",
          );
        }}
      >
        <fieldset disabled={readonly || action.pending}>
          <label htmlFor={id}>素材名称</label>
          <input
            id={id}
            value={name}
            required
            maxLength={200}
            onChange={(e) => setName(e.target.value)}
          />
          <div className="tag-options" aria-label="素材用途">
            {Object.entries(tags).map(([key, label]) => (
              <label key={key}>
                <input
                  type="checkbox"
                  checked={chosen.includes(key)}
                  onChange={(e) =>
                    setChosen(
                      e.target.checked
                        ? [...chosen, key]
                        : chosen.filter((x) => x !== key),
                    )
                  }
                />
                {label}
              </label>
            ))}
          </div>
          <div className="form-footer">
            <button className="button primary small">保存素材信息</button>
            <button
              className="text-button danger"
              type="button"
              onClick={() => setConfirmDelete(true)}
            >
              删除素材
            </button>
          </div>
          {confirmDelete && (
            <div className="notice">
              确认从资产库删除此素材？文件和历史记录会保留。
              <button
                className="button secondary small"
                type="button"
                onClick={() =>
                  void action.run(
                    (signal) =>
                      apiRequest<AssetRead>(`/assets/${asset.id}`, {
                        method: "DELETE",
                        signal,
                        body: { expected_version: base.version },
                      }),
                    "素材已删除",
                  )
                }
              >
                确认删除素材
              </button>
              <button
                className="text-button"
                type="button"
                onClick={() => setConfirmDelete(false)}
              >
                取消
              </button>
            </div>
          )}
        </fieldset>
      </form>
      {(action.error || action.message) && (
        <p
          role={action.error ? "alert" : "status"}
          className={`notice ${action.error ? "error" : "success"}`}
        >
          {action.error || action.message}
        </p>
      )}
      {action.error && (
        <div className="conflict-review">
          <button
            className="button secondary small"
            disabled={review.pending}
            onClick={() =>
              void review.run(async (signal) => {
                const all = await apiRequest<AssetRead[]>(
                  `/projects/${asset.project_id}/assets`,
                  { signal },
                );
                const found = all.find((x) => x.id === asset.id);
                if (!found) throw new Error("素材已删除，请重新打开资产库");
                return found;
              }, "已读取最新版本")
            }
          >
            核对最新素材版本
          </button>
          {review.error && <p role="alert">{review.error}</p>}
          {latest && (
            <div className="notice">
              最新版本 {latest.version}：{latest.name}；用途{" "}
              {latest.tags.join("、") || "未标记"}
              <button
                className="button secondary small"
                disabled={readonly}
                onClick={() => {
                  setBase(latest);
                  action.clearFeedback();
                }}
              >
                保留修改并使用此版本
              </button>
            </div>
          )}
        </div>
      )}
      {asset.source_task_id !== null && (
        <details>
          <summary>生成参数</summary>
          <pre>{JSON.stringify(asset.metadata_json, null, 2)}</pre>
        </details>
      )}
    </div>
  );
}

export function AssetLibrary({
  projectId,
  readonly = false,
}: {
  projectId: number;
  readonly?: boolean;
}) {
  const id = useId();
  const [assets, setAssets] = useState<AssetRead[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState("");
  const [kind, setKind] = useState("");
  const [tag, setTag] = useState("");
  const [shot, setShot] = useState("");
  const [selected, setSelected] = useState<number | null>(null);
  useEffect(() => {
    const controller = new AbortController();
    apiRequest<AssetRead[]>(`/projects/${projectId}/assets`, {
      signal: controller.signal,
    })
      .then((items) => {
        if (!controller.signal.aborted) {
          setAssets(items);
          setLoaded(true);
        }
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted)
          setError(reason instanceof Error ? reason.message : "读取素材失败");
      });
    return () => controller.abort();
  }, [projectId]);
  function saved(asset: AssetRead) {
    setAssets((items) =>
      [asset, ...items.filter((a) => a.id !== asset.id)].filter(
        (a) => !a.deleted_at,
      ),
    );
    setSelected(asset.deleted_at ? null : asset.id);
  }
  const current = assets.find((a) => a.id === selected);
  const visible = assets.filter(
    (a) =>
      (!kind || a.type === kind) &&
      (!tag || a.tags.includes(tag)) &&
      (!shot ||
        (shot === "upload"
          ? a.source_task_id === null
          : a.shot_id === Number(shot))),
  );
  return (
    <>
      <section className="panel">
        <h2>导入你的素材</h2>
        <AssetUpload
          projectId={projectId}
          onUploaded={saved}
          readonly={readonly || !loaded}
        />
      </section>
      {error && (
        <p role="alert" className="notice error">
          {error}
        </p>
      )}
      <div className="asset-filters">
        <label htmlFor={`${id}-type`}>
          素材类型
          <select
            id={`${id}-type`}
            value={kind}
            onChange={(e) => setKind(e.target.value)}
          >
            <option value="">全部类型</option>
            <option value="image">图片</option>
            <option value="video">视频</option>
          </select>
        </label>
        <label htmlFor={`${id}-tag`}>
          素材用途
          <select
            id={`${id}-tag`}
            value={tag}
            onChange={(e) => setTag(e.target.value)}
          >
            <option value="">全部用途</option>
            {Object.entries(tags).map(([key, name]) => (
              <option key={key} value={key}>
                {name}
              </option>
            ))}
          </select>
        </label>
        <label htmlFor={`${id}-shot`}>
          所属镜头
          <select
            id={`${id}-shot`}
            value={shot}
            onChange={(e) => setShot(e.target.value)}
          >
            <option value="">全部来源</option>
            <option value="upload">上传素材</option>
            {[
              ...new Set(
                assets.flatMap((a) => (a.shot_id === null ? [] : [a.shot_id])),
              ),
            ].map((n) => (
              <option key={n} value={n}>
                镜头 #{n}
              </option>
            ))}
          </select>
        </label>
        <span className="muted">{visible.length} 个素材</span>
      </div>
      {!loaded && !error && <p className="muted">正在读取素材…</p>}
      {loaded && visible.length === 0 && (
        <p className="empty-inline">这里还没有符合条件的素材。</p>
      )}
      <div className="asset-workspace">
        <div className="asset-grid">
          {visible.map((a) => (
            <button
              key={a.id}
              className={`asset-card ${selected === a.id ? "selected" : ""}`}
              aria-label={`预览 ${a.name}`}
              onClick={() => setSelected(a.id)}
            >
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img src={mediaUrl(a, true)} alt="" loading="lazy" />
              <strong>{a.name}</strong>
              <span>
                {a.type === "image" ? "图片" : "视频"} · {a.width} × {a.height}
              </span>
              <span>
                {a.tags
                  .map((t) => tags[t as keyof typeof tags] ?? t)
                  .join(" / ") || "未标记用途"}
              </span>
            </button>
          ))}
        </div>
        {current && (
          <AssetDetails
            key={`${current.id}:${current.version}`}
            asset={current}
            readonly={readonly}
            onSaved={saved}
          />
        )}
      </div>
    </>
  );
}
