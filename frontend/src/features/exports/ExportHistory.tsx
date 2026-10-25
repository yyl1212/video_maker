"use client";

import { useEffect, useState } from "react";
import { apiRequest, type AssetRead } from "@/lib/api";
import { MediaPreview, mediaUrl } from "@/features/assets/MediaPreview";

export function ExportHistory({
  episodeId,
  projectId,
  revision,
}: {
  episodeId: number;
  projectId: number;
  revision: number;
}) {
  const [assets, setAssets] = useState<AssetRead[]>([]);
  const [error, setError] = useState("");
  const [refresh, setRefresh] = useState(0);
  useEffect(() => {
    const controller = new AbortController();
    apiRequest<AssetRead[]>(`/episodes/${episodeId}/exports`, {
      signal: controller.signal,
    })
      .then((result) => {
        if (!controller.signal.aborted) {
          setAssets(result.filter((asset) => asset.project_id === projectId));
          setError("");
        }
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted)
          setError(
            reason instanceof Error ? reason.message : "无法读取成片历史",
          );
      });
    return () => controller.abort();
  }, [episodeId, projectId, revision, refresh]);
  return (
    <section className="panel export-history" aria-label="成片历史">
      <div className="section-heading">
        <div>
          <p className="eyebrow">每次导出独立保留</p>
          <h2>成片历史</h2>
        </div>
        <button
          className="button secondary"
          onClick={() => setRefresh((n) => n + 1)}
        >
          刷新成片历史
        </button>
      </div>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {!assets.length && (
        <p className="muted">导出完成后可在这里播放、下载 MP4 和来源清单。</p>
      )}
      <div className="export-results">
        {assets.map((asset) => (
          <article key={asset.id} className="export-result">
            <h3>{asset.name}</h3>
            <p className="muted">
              {asset.metadata_json.clip_count
                ? `${asset.metadata_json.clip_count} 个片段 · `
                : ""}
              资产 #{asset.id}
            </p>
            <MediaPreview asset={asset} />
            <div className="button-row">
              <a
                className="button secondary"
                href={`${mediaUrl(asset)}&download=1`}
              >
                下载 MP4
              </a>
              <a
                className="text-button"
                href={`/api/v1/assets/${asset.id}/manifest?project_id=${projectId}`}
              >
                下载来源清单
              </a>
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}
