"use client";

import { useId, useState } from "react";
import { apiRequest, type AssetRead, type ShotRead } from "@/lib/api";
import { useFormAction } from "@/components/useFormAction";
import { MediaPreview } from "@/features/assets/MediaPreview";

type Candidate = {
  id: number;
  shotVersion: number;
  selectionVersion: number;
  inputHash: string | null;
};
type ClearChoice = {
  kind: "image" | "video";
  assetId: number;
  shotVersion: number;
  selectionVersion: number;
};

export function SelectionPanel({
  shot,
  assets,
  onSaved,
}: {
  shot: ShotRead;
  assets: AssetRead[];
  onSaved: (shot: ShotRead) => void;
}) {
  const id = useId();
  const [frame, setFrame] = useState<Candidate | null>(null);
  const [video, setVideo] = useState<Candidate | null>(null);
  const [confirmed, setConfirmed] = useState(false);
  const [clear, setClear] = useState<ClearChoice | null>(null);
  const action = useFormAction<ShotRead>(`selection:${shot.id}`, (s) => {
    setFrame(null);
    setVideo(null);
    setConfirmed(false);
    setClear(null);
    onSaved(s);
  });
  const candidates = assets.filter(
    (a) => a.source_task_id === null || a.shot_id === shot.id,
  );
  const selectedFrame = assets.find(
    (a) => a.id === shot.selection.first_frame_asset_id,
  );
  const selectedVideo = assets.find(
    (a) => a.id === shot.selection.selected_video_asset_id,
  );
  const clearStale =
    clear !== null &&
    (clear.shotVersion !== shot.version ||
      clear.selectionVersion !== shot.selection.version ||
      clear.assetId !==
        (clear.kind === "image"
          ? shot.selection.first_frame_asset_id
          : shot.selection.selected_video_asset_id));
  function askClear(kind: "image" | "video") {
    const assetId =
      kind === "image"
        ? shot.selection.first_frame_asset_id
        : shot.selection.selected_video_asset_id;
    if (assetId !== null)
      setClear({
        kind,
        assetId,
        shotVersion: shot.version,
        selectionVersion: shot.selection.version,
      });
  }
  function candidate(value: string, kind: "image" | "video") {
    return value
      ? {
          id: Number(value),
          shotVersion: shot.version,
          selectionVersion: shot.selection.version,
          inputHash:
            kind === "image" ? shot.image_input_hash : shot.video_input_hash,
        }
      : null;
  }
  function blocked(value: Candidate | null, kind: "image" | "video") {
    if (
      !value ||
      value.shotVersion !== shot.version ||
      value.selectionVersion !== shot.selection.version ||
      value.inputHash !==
        (kind === "image" ? shot.image_input_hash : shot.video_input_hash)
    )
      return true;
    return (
      assets.find((a) => a.id === value.id)?.source_task_id === null &&
      !confirmed
    );
  }
  function adopt(
    kind: "image" | "video",
    value: Candidate | null,
    removal?: ClearChoice,
  ) {
    void action.run(
      (signal) =>
        apiRequest<ShotRead>(`/shots/${shot.id}/selection`, {
          method: "PUT",
          signal,
          body: {
            shot_version:
              value?.shotVersion ?? removal?.shotVersion ?? shot.version,
            selection_version:
              value?.selectionVersion ??
              removal?.selectionVersion ??
              shot.selection.version,
            [kind === "image"
              ? "first_frame_asset_id"
              : "selected_video_asset_id"]: value?.id ?? null,
            confirm_uploaded: confirmed,
            ...(value
              ? {
                  [kind === "image"
                    ? "expected_image_input_hash"
                    : "expected_video_input_hash"]: value.inputHash,
                }
              : {}),
          },
        }),
      value ? "已明确采用此版本" : "已取消采用",
    );
  }
  return (
    <section className="panel selection-panel">
      <div className="section-heading">
        <h2>当前采用的版本</h2>
        <span className="pill">选择版本 {shot.selection.version}</span>
      </div>
      <p className="muted">
        上传素材采用时绑定当前输入。输入变化后会标记旧版本，保留文件和选择。
      </p>
      <fieldset disabled={shot.read_only || action.pending}>
        <h3>首帧</h3>
        <p>
          {selectedFrame?.name ??
            (shot.selection.first_frame_asset_id
              ? `素材 #${shot.selection.first_frame_asset_id}`
              : "尚未采用首帧")}
          {shot.selection.first_frame_stale && (
            <span className="stale-badge">旧版本</span>
          )}
        </p>
        {shot.selection.first_frame_issue && (
          <p className="notice">{shot.selection.first_frame_issue}</p>
        )}
        {selectedFrame && <MediaPreview asset={selectedFrame} />}
        <label htmlFor={`${id}-frame`}>首帧版本</label>
        <select
          id={`${id}-frame`}
          value={frame?.id ?? ""}
          onChange={(e) => {
            setFrame(candidate(e.target.value, "image"));
            setConfirmed(false);
          }}
        >
          <option value="">选择图片版本</option>
          {candidates
            .filter((a) => a.type === "image")
            .map((a) => (
              <option key={a.id} value={a.id}>
                {a.name} · {a.source_task_id ? "本镜生成" : "上传"} #{a.id}
              </option>
            ))}
        </select>
        <div className="form-footer">
          <button
            className="button secondary small"
            disabled={blocked(frame, "image")}
            onClick={() => adopt("image", frame)}
          >
            采用首帧
          </button>
          {shot.selection.first_frame_asset_id && (
            <button className="text-button" onClick={() => askClear("image")}>
              取消首帧采用
            </button>
          )}
        </div>
        <h3>视频片段</h3>
        <p>
          {selectedVideo?.name ??
            (shot.selection.selected_video_asset_id
              ? `素材 #${shot.selection.selected_video_asset_id}`
              : "尚未采用视频")}
          {shot.selection.video_stale && (
            <span className="stale-badge">旧版本</span>
          )}
        </p>
        {shot.selection.video_issue && (
          <p className="notice">{shot.selection.video_issue}</p>
        )}
        {selectedVideo && <MediaPreview asset={selectedVideo} />}
        <label htmlFor={`${id}-video`}>视频版本</label>
        <select
          id={`${id}-video`}
          value={video?.id ?? ""}
          onChange={(e) => {
            setVideo(candidate(e.target.value, "video"));
            setConfirmed(false);
          }}
        >
          <option value="">选择视频版本</option>
          {candidates
            .filter((a) => a.type === "video")
            .map((a) => (
              <option key={a.id} value={a.id}>
                {a.name} · {a.source_task_id ? "本镜生成" : "上传"} #{a.id}
              </option>
            ))}
        </select>
        <div className="form-footer">
          <button
            className="button secondary small"
            disabled={blocked(video, "video") || !shot.video_ready}
            onClick={() => adopt("video", video)}
          >
            采用视频
          </button>
          {shot.selection.selected_video_asset_id && (
            <button className="text-button" onClick={() => askClear("video")}>
              取消视频采用
            </button>
          )}
        </div>
        {(frame || video) && (
          <label className="confirm-choice">
            <input
              type="checkbox"
              checked={confirmed}
              onChange={(e) => setConfirmed(e.target.checked)}
            />
            确认上传素材适用于当前镜头输入
          </label>
        )}
        {clear && (
          <div className="notice">
            确认取消采用此{clear.kind === "image" ? "首帧" : "视频"}（素材#
            {clear.assetId}）？原文件会保留。
            {clearStale && (
              <p>取消确认已失效，镜头或采用版本发生变化，请返回重新核对。</p>
            )}
            <button
              className="button secondary small"
              disabled={clearStale}
              onClick={() => {
                if (!clearStale) adopt(clear.kind, null, clear);
              }}
            >
              确认取消采用
            </button>
            <button className="text-button" onClick={() => setClear(null)}>
              返回
            </button>
          </div>
        )}
      </fieldset>
      {((frame &&
        (frame.shotVersion !== shot.version ||
          frame.selectionVersion !== shot.selection.version ||
          frame.inputHash !== shot.image_input_hash)) ||
        (video &&
          (video.shotVersion !== shot.version ||
            video.selectionVersion !== shot.selection.version ||
            video.inputHash !== shot.video_input_hash))) && (
        <p className="notice">镜头或采用版本已更新，请核对后重新选择。</p>
      )}
      {(action.error || action.message) && (
        <p
          role={action.error ? "alert" : "status"}
          className={`notice ${action.error ? "error" : "success"}`}
        >
          {action.error || action.message}
        </p>
      )}
    </section>
  );
}
