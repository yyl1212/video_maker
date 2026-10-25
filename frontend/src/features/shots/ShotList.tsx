"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  apiRequest,
  type AssetRead,
  type ProjectRead,
  type ShotListRead,
  type ShotRead,
} from "@/lib/api";
import { useWorkbenchStore } from "@/stores/workbench";
import { useFormAction } from "@/components/useFormAction";
import { VideoGenerationPanel } from "./VideoGenerationPanel";
import { ImageGenerationPanel } from "./ImageGenerationPanel";
import { ShotEditor } from "./ShotEditor";
import { ReferencePicker } from "./ReferencePicker";
import { SelectionPanel } from "./SelectionPanel";

function mergeSnapshot(
  current: ShotListRead | null,
  incoming: ShotListRead,
): ShotListRead {
  if (!current) return incoming;
  if (incoming.episode_version < current.episode_version) return current;
  const known = new Map(current.shots.map((shot) => [shot.id, shot]));
  return {
    ...incoming,
    shots: incoming.shots.map((shot) => {
      const saved = known.get(shot.id);
      // 普通编辑不会递增Episode.version；采用也有独立版本，必须整体保留较新DTO。
      return saved &&
        (saved.version > shot.version ||
          saved.selection.version > shot.selection.version)
        ? saved
        : shot;
    }),
  };
}

export function ShotList({
  project,
  episodeId,
  readonly,
  onEpisodeChanged,
}: {
  project: ProjectRead;
  episodeId: number;
  readonly: boolean;
  onEpisodeChanged?: () => void;
}) {
  const [listing, setListing] = useState<ShotListRead | null>(null);
  const [assets, setAssets] = useState<AssetRead[]>([]);
  const [selected, setSelected] = useState<number | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [initialError, setInitialError] = useState("");
  const drafts = useWorkbenchStore((s) => s.drafts);
  const clearDraft = useWorkbenchStore((s) => s.clearDraft);
  const dirty = Object.entries(drafts).some(
    ([key, d]) =>
      d.dirty &&
      listing?.shots.some(
        (s) => key === `shot:${s.id}` || key === `references:${s.id}`,
      ),
  );
  const action = useFormAction<ShotListRead>(`shot-list:${episodeId}`, (s) => {
    setListing((current) => mergeSnapshot(current, s));
    setConfirmDelete(false);
    onEpisodeChanged?.();
  });
  const refresh = useFormAction<{ list: ShotListRead; assets: AssetRead[] }>(
    `shot-refresh:${episodeId}`,
    (s) => {
      setListing((current) => mergeSnapshot(current, s.list));
      setAssets(s.assets);
      onEpisodeChanged?.();
    },
  );
  useEffect(() => {
    const controller = new AbortController();
    Promise.all([
      apiRequest<ShotListRead>(`/episodes/${episodeId}/shots`, {
        signal: controller.signal,
      }),
      apiRequest<AssetRead[]>(`/projects/${project.id}/assets`, {
        signal: controller.signal,
      }),
    ])
      .then(([s, a]) => {
        if (!controller.signal.aborted) {
          setListing(s);
          setAssets(a);
        }
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted)
          setInitialError(
            reason instanceof Error ? reason.message : "读取分镜失败",
          );
      });
    return () => controller.abort();
  }, [episodeId, project.id]);
  const busy = action.pending || refresh.pending;
  const active =
    listing?.shots.find((s) => s.id === selected) ?? listing?.shots[0];
  function saved(shot: ShotRead) {
    setListing((s) =>
      s
        ? {
            ...s,
            shots: s.shots.map((item) => (item.id === shot.id ? shot : item)),
          }
        : s,
    );
  }
  async function reload(signal: AbortSignal) {
    return apiRequest<ShotListRead>(`/episodes/${episodeId}/shots`, { signal });
  }
  function move(index: number, offset: number) {
    if (!listing) return;
    const ids = listing.shots.map((s) => s.id);
    const item = ids.splice(index, 1)[0];
    if (item === undefined) return;
    ids.splice(index + offset, 0, item);
    void action.run(async (signal) => {
      await apiRequest<ShotRead[]>(`/episodes/${episodeId}/shots/reorder`, {
        method: "POST",
        signal,
        body: { expected_version: listing.episode_version, shot_ids: ids },
      });
      return reload(signal);
    }, "分镜顺序已保存");
  }
  if (initialError)
    return (
      <p role="alert" className="notice error">
        {initialError}
      </p>
    );
  if (!listing) return <p className="muted">正在读取分镜…</p>;
  return (
    <section className="shot-workbench" aria-label="分镜工作台">
      <div className="section-heading">
        <div>
          <p className="eyebrow">分镜工作台</p>
          <h2>
            把故事展开成镜头{" "}
            <span className="count">{listing.shots.length} / 24</span>
          </h2>
        </div>
        <div className="toolbar">
          <Link className="text-button" href={`/projects/${project.id}/assets`}>
            资产库 →
          </Link>
          <button
            className="button secondary small"
            disabled={busy}
            onClick={() =>
              void refresh.run(async (signal) => {
                const [list, assets] = await Promise.all([
                  reload(signal),
                  apiRequest<AssetRead[]>(`/projects/${project.id}/assets`, {
                    signal,
                  }),
                ]);
                return { list, assets };
              }, "已读取最新素材和分镜；未保存草稿保留")
            }
          >
            刷新素材与分镜
          </button>
          <button
            className="button primary small"
            disabled={readonly || busy || listing.shots.length >= 24}
            onClick={() =>
              void action.run(async (signal) => {
                const item = await apiRequest<ShotRead>(
                  `/episodes/${episodeId}/shots`,
                  { method: "POST", signal, body: {} },
                );
                setSelected(item.id);
                return reload(signal);
              }, "已添加空白分镜")
            }
          >
            添加分镜
          </button>
        </div>
      </div>
      {(action.error || action.message || refresh.error || refresh.message) && (
        <p
          role={action.error || refresh.error ? "alert" : "status"}
          className={`notice ${action.error || refresh.error ? "error" : "success"}`}
        >
          {action.error || refresh.error || action.message || refresh.message}
        </p>
      )}
      {readonly && <p className="notice">当前项目或剧集已归档，工作台只读。</p>}
      {listing.shots.length === 0 ? (
        <div className="empty-small">
          <h3>从第一个镜头开始</h3>
          <p>添加分镜，填写画面与Prompt，再选择参考图片和媒体版本。</p>
        </div>
      ) : (
        <div className="shot-layout">
          <aside className="shot-list">
            {listing.shots.map((s, index) => (
              <div
                className={`shot-row ${active?.id === s.id ? "selected" : ""}`}
                key={s.id}
              >
                <button
                  className="shot-tab"
                  onClick={() => {
                    setSelected(s.id);
                    setConfirmDelete(false);
                  }}
                >
                  <span className="eyebrow">
                    镜头 {String(s.order_index).padStart(2, "0")}
                  </span>
                  <strong>{s.raw_description || "未填写画面描述"}</strong>
                  <span className="muted">
                    {s.status === "READY" ? "配置就绪" : "草稿"} ·{" "}
                    {s.duration_seconds}秒
                  </span>
                  {(s.selection.first_frame_stale ||
                    s.selection.video_stale) && (
                    <span className="stale-badge">含旧版本</span>
                  )}
                </button>
                <div className="row-actions">
                  <button
                    aria-label={`上移镜头 ${s.order_index}`}
                    disabled={readonly || busy || dirty || index === 0}
                    onClick={() => move(index, -1)}
                  >
                    ↑
                  </button>
                  <button
                    aria-label={`下移镜头 ${s.order_index}`}
                    disabled={
                      readonly ||
                      busy ||
                      dirty ||
                      index === listing.shots.length - 1
                    }
                    onClick={() => move(index, 1)}
                  >
                    ↓
                  </button>
                </div>
              </div>
            ))}
            {dirty && (
              <p className="muted">先保存文字与参考草稿，再调整顺序。</p>
            )}
          </aside>
          {active && (
            <div className="shot-content">
              <ShotEditor
                key={`text:${active.id}`}
                shot={{ ...active, read_only: readonly || active.read_only }}
                project={project}
                onSaved={saved}
              />
              <ReferencePicker
                key={`refs:${active.id}`}
                shot={{ ...active, read_only: readonly || active.read_only }}
                assets={assets}
                onSaved={saved}
              />
              <ImageGenerationPanel
                key={`generate:${active.id}`}
                shot={{ ...active, read_only: readonly || active.read_only }}
                project={project}
                episodeVersion={listing.episode_version}
                onCompleted={() =>
                  void refresh.run(async (signal) => {
                    const [list, refreshedAssets] = await Promise.all([
                      reload(signal),
                      apiRequest<AssetRead[]>(
                        `/projects/${project.id}/assets`,
                        { signal },
                      ),
                    ]);
                    return { list, assets: refreshedAssets };
                  }, "已刷新首帧历史与输入")
                }
              />
              <VideoGenerationPanel
                key={`video-generate:${active.id}`}
                shot={{ ...active, read_only: readonly || active.read_only }}
                project={project}
                episodeVersion={listing.episode_version}
                onCompleted={() =>
                  void refresh.run(async (signal) => {
                    const [list, refreshedAssets] = await Promise.all([
                      reload(signal),
                      apiRequest<AssetRead[]>(
                        `/projects/${project.id}/assets`,
                        { signal },
                      ),
                    ]);
                    return { list, assets: refreshedAssets };
                  }, "已刷新有声视频历史与输入")
                }
              />
              <SelectionPanel
                key={`select:${active.id}`}
                shot={{ ...active, read_only: readonly || active.read_only }}
                assets={assets}
                onSaved={saved}
              />
              <div className="delete-shot">
                <button
                  className="text-button danger"
                  disabled={readonly || busy}
                  onClick={() => setConfirmDelete(true)}
                >
                  删除当前分镜
                </button>
                {confirmDelete && (
                  <div className="notice">
                    确认删除镜头 {active.order_index}
                    ？本镜未保存草稿将清除，素材与历史保留。
                    <button
                      className="button secondary small"
                      disabled={readonly || busy}
                      onClick={() =>
                        void action.run(async (signal) => {
                          await apiRequest<ShotRead>(`/shots/${active.id}`, {
                            method: "DELETE",
                            signal,
                            body: { expected_version: active.version },
                          });
                          clearDraft(`shot:${active.id}`);
                          clearDraft(`references:${active.id}`);
                          setSelected(null);
                          return reload(signal);
                        }, "分镜已删除")
                      }
                    >
                      确认删除分镜
                    </button>
                    <button
                      className="text-button"
                      onClick={() => setConfirmDelete(false)}
                    >
                      取消
                    </button>
                  </div>
                )}
              </div>
            </div>
          )}
        </div>
      )}
    </section>
  );
}
