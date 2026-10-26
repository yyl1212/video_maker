import { beforeEach, expect, test, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { VideoGenerationPanel } from "@/features/shots/VideoGenerationPanel";
import { useWorkbenchStore } from "@/stores/workbench";
import { project, shot } from "./shot-fixtures";

const value = {
  ...shot,
  video_ready: true,
  video_input_hash: "b".repeat(64),
  selection: { ...shot.selection, version: 4, first_frame_asset_id: 4 },
};
const idle = {
  task: null,
  runtime_state: { state: "IDLE", version: 1, active_task_id: null },
};
const settings = {
  version: 1,
  text: { base_url: "http://127.0.0.1:1234/v1", model: "" },
  image: {
    host: "127.0.0.1",
    port: 7859,
    tls: true,
    compression: true,
    model: "",
    width: 768,
    height: 1280,
    steps: 20,
    seed: 42,
    guidance_scale: 1,
    negative_prompt: "",
  },
  video: {
    base_url: "http://127.0.0.1:11234",
    model: "ddalcu/MiniMax-H3-FL2VA-MLX-Serve-8bit",
    preset: "quality",
    seed: 42,
  },
};
const done = {
  id: 20,
  shot_id: 3,
  kind: "VIDEO",
  status: "DONE",
  stage: "已完成",
  output_asset_id: 10,
  result_json: {},
  error_message: null,
};
beforeEach(() => {
  sessionStorage.clear();
  useWorkbenchStore.setState({ drafts: {} });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input) => {
      const path = String(input);
      if (path.endsWith("/bootstrap")) return Response.json({ token: "local" });
      if (path.endsWith("/settings")) return Response.json(settings);
      if (path.endsWith("/jobs/active")) return Response.json(idle);
      if (path.endsWith("/video-jobs"))
        return Response.json(
          { job_id: 20, status: "PENDING", poll_url: "/api/v1/jobs/20" },
          { status: 202 },
        );
      if (path.endsWith("/jobs/20")) return Response.json(done);
      throw new Error(`unexpected ${path}`);
    }),
  );
});
const panel = (onCompleted = vi.fn(), s = value) => (
  <VideoGenerationPanel
    project={project}
    episodeVersion={2}
    shot={s}
    onCompleted={onCompleted}
  />
);

test("提交音画包含选择版本和视频指纹，不允许浏览器传首帧数据，完成不自动采用", async () => {
  const completed = vi.fn();
  render(panel(completed));
  const button = await screen.findByRole("button", { name: "生成视频与声音" });
  await waitFor(() => expect(button).toBeEnabled());
  fireEvent.click(button);
  await waitFor(() => expect(completed).toHaveBeenCalledTimes(1));
  const writes = vi
    .mocked(fetch)
    .mock.calls.filter(([, i]) => i?.method === "POST");
  expect(writes).toHaveLength(1);
  const body = JSON.parse(String(writes[0]?.[1]?.body));
  expect(body).toMatchObject({
    expected_shot_version: 1,
    expected_selection_version: 4,
    expected_video_input_hash: "b".repeat(64),
  });
  expect(body).not.toHaveProperty("first_frame_image");
  expect(body).not.toHaveProperty("file_path");
  expect(vi.mocked(fetch).mock.calls.some(([, i]) => i?.method === "PUT")).toBe(
    false,
  );
  expect(
    await screen.findByText(/有声视频已生成，请播放检查并在当前媒体版本中采用/),
  ).toBeVisible();
});

test("首帧过期或未配置视频时禁止音画提交", async () => {
  render(
    panel(vi.fn(), {
      ...value,
      video_ready: false,
      selection: {
        ...value.selection,
        first_frame_stale: true,
        first_frame_issue: "首帧对应旧输入",
      },
    }),
  );
  expect(
    await screen.findByRole("button", { name: "生成视频与声音" }),
  ).toBeDisabled();
  expect(screen.getByText(/首帧对应旧输入/)).toBeVisible();
});

test("未保存草稿阻止音画生成，保持人工输入", async () => {
  useWorkbenchStore.setState({
    drafts: {
      "shot:3": {
        kind: "shot",
        value: {
          raw_description: "草稿",
          dialogue: "",
          character_ids: [],
          image_prompt: "",
          video_prompt: "",
          audio_prompt: "",
          duration_seconds: 1,
          image_settings: {},
          video_settings: { width: 384, height: 672 },
        },
        baseVersion: 1,
        dirty: true,
      },
    },
  });
  render(panel());
  expect(
    await screen.findByRole("button", { name: "生成视频与声音" }),
  ).toBeDisabled();
  expect(screen.getByText(/先保存本镜文字与参考草稿/)).toBeVisible();
});

test("未知音画提交遇到403后仍使用原选择版本与同一幂等键", async () => {
  const original = vi.mocked(fetch).getMockImplementation()!;
  let attempts = 0;
  vi.mocked(fetch).mockImplementation(async (input, init) => {
    if (String(input).endsWith("/video-jobs")) {
      attempts++;
      if (attempts === 1) throw new TypeError("response lost");
      if (attempts === 2)
        return Response.json(
          { error: { code: "SESSION_EXPIRED", message: "会话已变化" } },
          { status: 403 },
        );
    }
    return original(input, init);
  });
  const view = render(panel());
  const button = await screen.findByRole("button", { name: "生成视频与声音" });
  await waitFor(() => expect(button).toBeEnabled());
  fireEvent.click(button);
  fireEvent.click(
    await screen.findByRole("button", { name: "查询或重试原提交" }),
  );
  expect(await screen.findByRole("alert")).toHaveTextContent("会话已变化");
  view.rerender(
    panel(vi.fn(), { ...value, selection: { ...value.selection, version: 5 } }),
  );
  fireEvent.click(screen.getByRole("button", { name: "查询或重试原提交" }));
  await waitFor(() => expect(attempts).toBe(3));
  const bodies = vi
    .mocked(fetch)
    .mock.calls.filter(([p]) => String(p).endsWith("/video-jobs"))
    .map(([, i]) => i?.body);
  expect(new Set(bodies).size).toBe(1);
  expect(sessionStorage.getItem("vm:video-intent:3")).toBeNull();
});

test("H3连接检查不触发生成", async () => {
  const original = vi.mocked(fetch).getMockImplementation()!;
  vi.mocked(fetch).mockImplementation(async (input, init) =>
    String(input).endsWith("/settings/test-video-connection")
      ? Response.json({
          model: settings.video.model,
          state: "unloaded",
          message: "本机H3已连接，未生成",
        })
      : original(input, init),
  );
  render(panel());
  fireEvent.click(await screen.findByText("H3 连接与默认参数"));
  fireEvent.click(await screen.findByRole("button", { name: "检查H3连接" }));
  expect(await screen.findByText("本机H3已连接，未生成")).toBeVisible();
  expect(
    vi
      .mocked(fetch)
      .mock.calls.some(([p]) => String(p).endsWith("/video-jobs")),
  ).toBe(false);
});
