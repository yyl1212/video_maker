"use client";

import type { AssetRead } from "@/lib/api";

export function mediaUrl(asset: AssetRead, thumbnail = false) {
  return `/api/v1/assets/${asset.id}/${thumbnail ? "thumbnail" : "content"}?project_id=${asset.project_id}`;
}

export function MediaPreview({ asset }: { asset: AssetRead }) {
  return (
    <section className="media-preview" aria-label={`预览 ${asset.name}`}>
      {asset.type === "image" ? (
        // 本机受保护媒体不能交由外部优化器读取。
        // eslint-disable-next-line @next/next/no-img-element
        <img src={mediaUrl(asset)} alt={asset.name} />
      ) : (
        <video
          key={asset.id}
          controls
          preload="metadata"
          src={mediaUrl(asset)}
          poster={asset.thumbnail_path ? mediaUrl(asset, true) : undefined}
        >
          当前浏览器无法播放该视频，请下载查看。
        </video>
      )}
      <div className="media-meta">
        <span>
          {asset.width} × {asset.height}
        </span>
        <span>{(asset.size_bytes / 1024 / 1024).toFixed(2)} MiB</span>
        {asset.duration_seconds !== null && (
          <span>
            {asset.duration_seconds.toFixed(2)}秒 ·{" "}
            {asset.has_audio ? "有声音" : "无声音"}
          </span>
        )}
        <a className="text-button" href={`${mediaUrl(asset)}&download=1`}>
          下载素材
        </a>
      </div>
    </section>
  );
}
