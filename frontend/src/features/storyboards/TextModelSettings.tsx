"use client";
import { useEffect, useState } from "react";
import { apiRequest } from "@/lib/api";
import type { components } from "@/lib/api-types";
import { useFormAction } from "@/components/useFormAction";

type Settings = components["schemas"]["SettingsRead"];
function SettingsForm() {
  const [base, setBase] = useState<Settings | null>(null);
  const [text, setText] = useState<Settings["text"]>({
    base_url: "http://127.0.0.1:1234/v1",
    model: "",
  });
  const [models, setModels] = useState<string[]>([]);
  const [error, setError] = useState("");
  const save = useFormAction<Settings>("text-settings", (result) => {
    setBase(result);
    setText(result.text);
  });
  const test = useFormAction<components["schemas"]["TextConnectionRead"]>(
    "text-test",
    (result) => setModels(result.models),
  );
  const refresh = useFormAction<Settings>("text-settings-refresh", setBase);
  useEffect(() => {
    const controller = new AbortController();
    apiRequest<Settings>("/settings", { signal: controller.signal })
      .then((result) => {
        if (!controller.signal.aborted) {
          setBase(result);
          setText(result.text);
        }
      })
      .catch((reason: unknown) => {
        if (!controller.signal.aborted)
          setError(reason instanceof Error ? reason.message : "读取设置失败");
      });
    return () => controller.abort();
  }, []);
  if (!base)
    return <p className="notice">{error || "正在读取本机文本设置…"}</p>;
  return (
    <div className="text-settings">
      <p className="muted">
        仅访问本机回环服务。检查只读取模型列表，保存不会加载或卸载模型。
      </p>
      <label>
        文本服务地址
        <input
          aria-label="文本服务地址"
          disabled={save.pending}
          value={text.base_url}
          onChange={(e) => setText({ ...text, base_url: e.target.value })}
        />
      </label>
      <label>
        文本模型ID
        <input
          aria-label="文本模型ID"
          disabled={save.pending}
          maxLength={200}
          value={text.model}
          onChange={(e) => setText({ ...text, model: e.target.value })}
        />
      </label>
      {models.length > 0 && (
        <label>
          可用文本模型
          <select
            aria-label="可用文本模型"
            disabled={save.pending}
            value={models.includes(text.model) ? text.model : ""}
            onChange={(e) => setText({ ...text, model: e.target.value })}
          >
            <option value="">请选择模型</option>
            {models.map((model) => (
              <option key={model} value={model}>
                {model}
              </option>
            ))}
          </select>
        </label>
      )}
      <div className="button-row">
        <button
          className="button secondary small"
          disabled={test.pending}
          onClick={() =>
            void test.run(
              (signal) =>
                apiRequest("/settings/test-text-connection", {
                  method: "POST",
                  signal,
                  body: { text },
                }),
              "本机文本服务已连接，未生成内容",
            )
          }
        >
          检查文本连接
        </button>
        <button
          className="button primary small"
          disabled={save.pending}
          onClick={() =>
            void save.run(
              (signal) =>
                apiRequest("/settings", {
                  method: "PUT",
                  signal,
                  body: {
                    expected_version: base.version,
                    text,
                    image: base.image,
                    video: base.video,
                  },
                }),
              "文本设置已保存",
            )
          }
        >
          保存文本设置
        </button>
      </div>
      {(save.error || test.error || refresh.error) && (
        <p role="alert" className="notice error">
          {save.error || test.error || refresh.error}
        </p>
      )}
      {(save.message || test.message || refresh.message) && (
        <p className="success" role="status">
          {save.message || test.message || refresh.message}
        </p>
      )}
      {save.error && (
        <button
          className="text-button"
          disabled={refresh.pending}
          onClick={() =>
            void refresh.run(
              (signal) => apiRequest("/settings", { signal }),
              "已核对最新设置，文本输入保留，请检查后重新保存",
            )
          }
        >
          保留文本输入并核对最新设置
        </button>
      )}
    </div>
  );
}
export function TextModelSettings() {
  const [open, setOpen] = useState(false);
  return (
    <details className="panel" onToggle={(e) => setOpen(e.currentTarget.open)}>
      <summary>文本模型连接与配置</summary>
      {open && <SettingsForm />}
    </details>
  );
}
