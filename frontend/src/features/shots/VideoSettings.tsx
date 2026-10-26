"use client";

import { useEffect, useState } from "react";
import { apiRequest } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { useFormAction } from "@/components/useFormAction";

type Settings = components["schemas"]["SettingsRead"];
type Connection = components["schemas"]["VideoConnectionRead"];

export function VideoSettings({ onSaved }: { onSaved: () => void }) {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [video, setVideo] = useState<Settings["video"] | null>(null);
  const [loadError, setLoadError] = useState("");
  const [connection, setConnection] = useState<Connection | null>(null);
  const save = useFormAction<Settings>("h3-settings", (s) => {
    setSettings(s);
    setVideo(s.video);
    onSaved();
  });
  const probe = useFormAction<Connection>("h3-probe", setConnection);
  useEffect(() => {
    const request = new AbortController();
    apiRequest<Settings>("/settings", { signal: request.signal })
      .then((s) => {
        if (!request.signal.aborted) {
          setSettings(s);
          setVideo(s.video);
        }
      })
      .catch((error: unknown) => {
        if (!request.signal.aborted)
          setLoadError(error instanceof Error ? error.message : "设置读取失败");
      });
    return () => request.abort();
  }, []);
  const busy = save.pending || probe.pending;
  return (
    <details className="advanced-settings">
      <summary>H3 连接与默认参数</summary>
      {loadError && (
        <p role="alert" className="error">
          {loadError}；重新打开本镜可重试。
        </p>
      )}
      {settings && video && (
        <form
          onSubmit={(e) => {
            e.preventDefault();
            void save.run(
              (signal) =>
                apiRequest<Settings>("/settings", {
                  method: "PUT",
                  signal,
                  body: {
                    expected_version: settings.version,
                    text: settings.text,
                    image: settings.image,
                    video,
                  },
                }),
              "H3默认配置已保存，已刷新视频输入",
            );
          }}
        >
          <p className="muted">
            本机模型默认配置；镜头内设置优先。H3同时生成视频与声音，缺失有效音轨会判定失败。
          </p>
          <fieldset disabled={busy}>
            <label>
              H3本机服务地址
              <input
                aria-label="H3服务地址"
                required
                value={video.base_url}
                onChange={(e) =>
                  setVideo({ ...video, base_url: e.target.value })
                }
              />
            </label>
            <label>
              H3模型文件
              <input
                aria-label="H3模型"
                required
                maxLength={200}
                value={video.model}
                onChange={(e) => setVideo({ ...video, model: e.target.value })}
              />
            </label>
            <div className="parameter-grid">
              <label>
                默认采样档位
                <select
                  aria-label="H3默认档位"
                  value={video.preset}
                  onChange={(e) =>
                    setVideo({
                      ...video,
                      preset: e.target.value as "quality" | "fast",
                    })
                  }
                >
                  <option value="quality">质量 · 20步</option>
                  <option value="fast">快速 · 4步Turbo</option>
                </select>
              </label>
              <label>
                默认视频种子
                <input
                  aria-label="H3默认种子"
                  type="number"
                  min={0}
                  max={4294967295}
                  step={1}
                  required
                  value={video.seed}
                  onChange={(e) =>
                    setVideo({ ...video, seed: Number(e.target.value) })
                  }
                />
              </label>
            </div>
            <div className="form-actions">
              <button
                type="button"
                className="button secondary small"
                onClick={() =>
                  void probe.run(
                    (signal) =>
                      apiRequest<Connection>(
                        "/settings/test-video-connection",
                        { method: "POST", signal, body: { video } },
                      ),
                    "H3连接检查完成",
                  )
                }
              >
                检查H3连接
              </button>
              <button className="button secondary small" type="submit">
                保存H3默认配置
              </button>
            </div>
          </fieldset>
          {(save.error || probe.error) && (
            <p className="error" role="alert">
              {save.error || probe.error}；设置冲突时请重新打开本镜核对。
            </p>
          )}
          {save.message && (
            <p className="success" role="status">
              {save.message}
            </p>
          )}
          {connection && (
            <div className="notice">
              <p>{connection.message}</p>
              <p>
                模型状态：
                {connection.state === "ready" ? "已就绪" : "已安装，生成时加载"}
              </p>
              <p className="muted">
                检查连接只查询模型，不生成或解除任务恢复限制。
              </p>
            </div>
          )}
        </form>
      )}
    </details>
  );
}
