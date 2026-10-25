"use client";

import { useId, useRef, useState } from "react";
import { apiRequest, type AssetRead } from "@/lib/api";
import { useFormAction } from "@/components/useFormAction";

export function AssetUpload({
  projectId,
  onUploaded,
  readonly = false,
}: {
  projectId: number;
  onUploaded: (asset: AssetRead) => void;
  readonly?: boolean;
}) {
  const id = useId();
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const action = useFormAction<AssetRead>(`upload:${projectId}`, (asset) => {
    setFile(null);
    if (input.current) input.current.value = "";
    onUploaded(asset);
  });
  return (
    <form
      className="asset-upload"
      onSubmit={(e) => {
        e.preventDefault();
        if (!file) return;
        const body = new FormData();
        body.append("file", file);
        void action.run(
          (signal) =>
            apiRequest<AssetRead>(`/projects/${projectId}/assets`, {
              method: "POST",
              body,
              signal,
            }),
          "素材已导入",
        );
      }}
    >
      <fieldset disabled={readonly || action.pending}>
        <label htmlFor={id}>上传素材文件</label>
        <input
          ref={input}
          id={id}
          type="file"
          accept="image/png,image/jpeg,image/webp,video/mp4"
          onChange={(e) => {
            setFile(e.target.files?.[0] ?? null);
            action.clearFeedback();
          }}
        />
        <p className="muted">
          图片 PNG / JPEG / WebP，最多16 MiB；视频 MP4，最多512
          MiB、10分钟。导入副本保存在本机。
        </p>
        <button className="button primary" disabled={!file}>
          {action.pending ? "正在导入…" : "导入素材"}
        </button>
      </fieldset>
      {(action.error || action.message) && (
        <p
          className={`notice ${action.error ? "error" : "success"}`}
          role={action.error ? "alert" : "status"}
        >
          {action.error || action.message}
        </p>
      )}
    </form>
  );
}
