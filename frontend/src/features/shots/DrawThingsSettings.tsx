"use client";

import { useEffect, useState } from "react";
import { apiRequest } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { useFormAction } from "@/components/useFormAction";

type Settings = components["schemas"]["SettingsRead"];
type ImageSettings = Settings["image"];
type Connection = components["schemas"]["ImageConnectionRead"];
const model = "flux_2_klein_9b_kv_i8x.ckpt";

export function DrawThingsSettings({ onSaved }: { onSaved: () => void }) {
  const [settings, setSettings] = useState<Settings | null>(null);
  const [image, setImage] = useState<ImageSettings | null>(null);
  const [loadError, setLoadError] = useState("");
  const [connection, setConnection] = useState<Connection | null>(null);
  const save = useFormAction<Settings>("drawthings-settings", (s) => {
    setSettings(s);
    setImage(s.image);
    onSaved();
  });
  const probe = useFormAction<Connection>("drawthings-probe", setConnection);
  useEffect(() => {
    const request = new AbortController();
    apiRequest<Settings>("/settings", { signal: request.signal })
      .then((s) => {
        if (!request.signal.aborted) {
          setSettings(s);
          setImage(s.image);
        }
      })
      .catch((error: unknown) => {
        if (!request.signal.aborted)
          setLoadError(error instanceof Error ? error.message : "设置读取失败");
      });
    return () => request.abort();
  }, []);
  function change(values: Partial<ImageSettings>) {
    if (image) setImage({ ...image, ...values });
    setConnection(null);
    probe.clearFeedback();
    save.clearFeedback();
  }
  const busy = save.pending || probe.pending;
  return (
    <details className="advanced-settings">
      <summary>Draw Things 连接与默认参数</summary>
      {loadError && (
        <p className="error" role="alert">
          {loadError}；重新打开本镜可重试。
        </p>
      )}
      {settings && image && (
        <form
          onSubmit={(event) => {
            event.preventDefault();
            const frozen = image;
            void save.run(
              (signal) =>
                apiRequest<Settings>("/settings", {
                  method: "PUT",
                  signal,
                  body: {
                    expected_version: settings.version,
                    text: settings.text,
                    video: settings.video,
                    image: frozen,
                  },
                }),
              "本机生图配置已保存，已刷新输入",
            );
          }}
        >
          <p className="muted">
            本机通用配置，影响使用默认值的镜头。修改后需保存。本期支持 Klein
            9B；其他模型待验证。
          </p>
          <fieldset disabled={busy}>
            <div className="parameter-grid">
              <label>
                本机地址
                <select
                  aria-label="Draw Things地址"
                  value={image.host}
                  onChange={(e) =>
                    change({ host: e.target.value as ImageSettings["host"] })
                  }
                >
                  <option>127.0.0.1</option>
                  <option>localhost</option>
                  <option>::1</option>
                </select>
              </label>
              <label>
                gRPC端口
                <input
                  aria-label="Draw Things端口"
                  type="number"
                  min={1}
                  max={65535}
                  required
                  value={image.port}
                  onChange={(e) => change({ port: Number(e.target.value) })}
                />
              </label>
            </div>
            <label className="checkbox-label">
              <input
                type="checkbox"
                checked={image.tls}
                onChange={(e) => change({ tls: e.target.checked })}
              />
              使用TLS（与Draw Things服务设置一致）
            </label>
            <p className="muted">
              响应压缩由 Draw Things 服务端控制，工作台兼容压缩及未压缩图片。
            </p>
            <label>
              本机模型文件
              <input
                aria-label="Draw Things模型"
                value={image.model}
                maxLength={200}
                onChange={(e) => change({ model: e.target.value })}
                placeholder={model}
              />
            </label>
            <button
              type="button"
              className="text-button"
              onClick={() => change({ model })}
            >
              填入已验证的 Klein 9B 模型名
            </button>
            <div className="parameter-grid">
              {(
                ["width", "height", "steps", "seed", "guidance_scale"] as const
              ).map((key) => (
                <label key={key}>
                  {
                    {
                      width: "默认宽度",
                      height: "默认高度",
                      steps: "默认步数",
                      seed: "默认种子",
                      guidance_scale: "默认引导强度",
                    }[key]
                  }
                  <input
                    aria-label={`默认生图${key}`}
                    type="number"
                    required
                    value={image[key]}
                    min={
                      key === "width" || key === "height"
                        ? 64
                        : key === "seed" || key === "guidance_scale"
                          ? 0
                          : 1
                    }
                    max={
                      key === "width" || key === "height"
                        ? 4096
                        : key === "seed"
                          ? 4294967295
                          : key === "steps"
                            ? 150
                            : 100
                    }
                    step={
                      key === "width" || key === "height"
                        ? 64
                        : key === "guidance_scale"
                          ? 0.1
                          : 1
                    }
                    onChange={(e) => change({ [key]: Number(e.target.value) })}
                  />
                </label>
              ))}
            </div>
            <p className="muted">
              宽高须为64的倍数；镜头自身参数优先。每次只生成一张图。
            </p>
            <div className="form-actions">
              <button
                type="button"
                className="button secondary small"
                onClick={() =>
                  void probe.run(
                    (signal) =>
                      apiRequest<Connection>("/settings/test-connection", {
                        method: "POST",
                        signal,
                        body: { image },
                      }),
                    "连接检查完成",
                  )
                }
              >
                检查连接与本机模型
              </button>
              <button type="submit" className="button secondary small">
                保存本机生图配置
              </button>
            </div>
          </fieldset>
          {(save.error || probe.error) && (
            <p role="alert" className="error">
              {save.error || probe.error}；若设置版本冲突，请重新打开本镜核对。
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
                {connection.models.some((m) => m.file === model)
                  ? "已安装可用的 Klein 9B 模型"
                  : "本机列表未发现已验证的 Klein 9B，请先在Draw Things安装"}
              </p>
              <p className="muted">连接检查不会生成图片或解除任务恢复限制。</p>
            </div>
          )}
        </form>
      )}
    </details>
  );
}
