import { beforeEach, expect, test, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ImageGenerationPanel } from "@/features/shots/ImageGenerationPanel";
import { useWorkbenchStore } from "@/stores/workbench";
import { project, shot } from "./shot-fixtures";

const settings = {
  version: 1,
  text: { base_url: "http://127.0.0.1:1234/v1", model: "" },
  image: {
    host: "127.0.0.1",
    port: 7859,
    tls: true,
    compression: true,
    model: "flux_2_klein_9b_kv_i8x.ckpt",
    width: 768,
    height: 1280,
    steps: 20,
    guidance_scale: 1,
    seed: 42,
    negative_prompt: "",
  },
  video: {
    base_url: "http://127.0.0.1:11234",
    model: "h3",
    preset: "quality",
    seed: 42,
  },
};
const idle = {
  task: null,
  runtime_state: {
    version: 1,
    state: "IDLE",
    active_task_id: null,
    reason: "",
    affected_backend: null,
  },
};
const readyShot = { ...shot, image_input_hash: "a".repeat(64) };
const done = {
  id: 12,
  shot_id: 3,
  kind: "IMAGE",
  status: "DONE",
  output_asset_id: 8,
  stage: "已完成",
  progress: 1,
  result_summary: "首帧已保存",
  result_json: {},
};

beforeEach(() => {
  sessionStorage.clear();
  useWorkbenchStore.setState({ drafts: {} });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input, init) => {
      const path = String(input);
      if (path.endsWith("/bootstrap")) return Response.json({ token: "test" });
      if (path.endsWith("/settings")) return Response.json(settings);
      if (path.endsWith("/jobs/active")) return Response.json(idle);
      if (path.endsWith("/image-jobs"))
        return Response.json(
          { job_id: 12, status: "PENDING", poll_url: "/api/v1/jobs/12" },
          { status: 202 },
        );
      if (path.endsWith("/jobs/12")) return Response.json(done);
      throw new Error(`unexpected ${path} ${init?.method}`);
    }),
  );
});

function panel(onCompleted = vi.fn(), value = readyShot) {
  return (
    <ImageGenerationPanel
      project={project}
      episodeVersion={2}
      shot={value}
      onCompleted={onCompleted}
    />
  );
}

test("首帧提交包含冻结的版本与幂等键；完成仅刷新，不自动采用", async () => {
  const completed = vi.fn();
  render(panel(completed));
  const button = await screen.findByRole("button", { name: "生成首帧" });
  await waitFor(() => expect(button).toBeEnabled());
  fireEvent.click(button);
  await waitFor(() => expect(completed).toHaveBeenCalledTimes(1));
  const writes = vi
    .mocked(fetch)
    .mock.calls.filter(([, i]) => i?.method === "POST");
  expect(writes).toHaveLength(1);
  expect(JSON.parse(String(writes[0]?.[1]?.body))).toMatchObject({
    expected_project_version: 1,
    expected_episode_version: 2,
    expected_shot_version: 1,
    expected_image_input_hash: "a".repeat(64),
  });
  expect(JSON.parse(String(writes[0]?.[1]?.body)).idempotency_key).toMatch(
    /^[0-9a-f-]{36}$/,
  );
  expect(vi.mocked(fetch).mock.calls.some(([, i]) => i?.method === "PUT")).toBe(
    false,
  );
  expect(await screen.findByText(/请在当前媒体版本中采用/)).toBeVisible();
});

test("提交响应丢失时保留原始意图，显式重试复用完全相同的请求", async () => {
  let tries = 0;
  const original = vi.mocked(fetch).getMockImplementation()!;
  vi.mocked(fetch).mockImplementation(async (input, init) => {
    if (String(input).endsWith("/image-jobs") && tries++ === 0)
      throw new TypeError("offline");
    return original(input, init);
  });
  const view = render(panel());
  const generate = await screen.findByRole("button", { name: "生成首帧" });
  await waitFor(() => expect(generate).toBeEnabled());
  fireEvent.click(generate);
  const retry = await screen.findByRole("button", { name: "查询或重试原提交" });
  view.rerender(panel(vi.fn(), { ...readyShot, version: 4 }));
  fireEvent.click(retry);
  await waitFor(() =>
    expect(
      vi
        .mocked(fetch)
        .mock.calls.filter(([p]) => String(p).endsWith("/image-jobs")),
    ).toHaveLength(2),
  );
  const writes = vi
    .mocked(fetch)
    .mock.calls.filter(([p]) => String(p).endsWith("/image-jobs"));
  expect(writes[0]?.[1]?.body).toBe(writes[1]?.[1]?.body);
});

test("未保存草稿和归档阻止新生成，忙碌由服务端状态决定", async () => {
  useWorkbenchStore.setState({
    drafts: {
      "references:3": {
        kind: "references",
        value: [],
        baseVersion: 1,
        dirty: true,
      },
    },
  });
  const view = render(panel());
  expect(
    await screen.findByRole("button", { name: "生成首帧" }),
  ).toBeDisabled();
  expect(screen.getByText(/先保存本镜文字与参考草稿/)).toBeVisible();
  useWorkbenchStore.setState({ drafts: {} });
  view.rerender(panel(vi.fn(), { ...readyShot, read_only: true }));
  expect(screen.getByRole("button", { name: "生成首帧" })).toBeDisabled();
});

test("恢复未确认的提交不会自动POST", async () => {
  sessionStorage.setItem(
    "vm:image-intent:3",
    JSON.stringify({
      idempotency_key: crypto.randomUUID(),
      expected_project_version: 1,
      expected_episode_version: 2,
      expected_shot_version: 1,
      expected_image_input_hash: "a".repeat(64),
    }),
  );
  render(panel());
  expect(
    await screen.findByRole("button", { name: "查询或重试原提交" }),
  ).toBeVisible();
  expect(
    vi.mocked(fetch).mock.calls.some(([, i]) => i?.method === "POST"),
  ).toBe(false);
});

test("本机配置可以检查连接，保存冲突时保留人工输入", async () => {
  const original = vi.mocked(fetch).getMockImplementation()!;
  vi.mocked(fetch).mockImplementation(async (input, init) => {
    if (String(input).endsWith("/settings/test-connection"))
      return Response.json({
        models: [
          { file: settings.image.model, name: "Klein", supported: true },
        ],
        message: "已连接本机",
      });
    if (String(input).endsWith("/settings") && init?.method === "PUT")
      return Response.json(
        { error: { message: "设置已变化" } },
        { status: 409 },
      );
    return original(input, init);
  });
  render(panel());
  fireEvent.click(await screen.findByText("Draw Things 连接与默认参数"));
  const port = await screen.findByLabelText("Draw Things端口");
  fireEvent.change(port, { target: { value: "7860" } });
  fireEvent.click(screen.getByRole("button", { name: "检查连接与本机模型" }));
  expect(await screen.findByText("已连接本机")).toBeVisible();
  expect(
    vi
      .mocked(fetch)
      .mock.calls.filter(([p]) => String(p).endsWith("/image-jobs")),
  ).toHaveLength(0);
  fireEvent.click(screen.getByRole("button", { name: "保存本机生图配置" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("设置已变化");
  expect(port).toHaveValue(7860);
});

test("服务端忙碌或状态未知时禁止新生成", async () => {
  const original = vi.mocked(fetch).getMockImplementation()!;
  vi.mocked(fetch).mockImplementation(async (input, init) =>
    String(input).endsWith("/jobs/active")
      ? Response.json({
          ...idle,
          runtime_state: { ...idle.runtime_state, state: "NEEDS_RESET" },
        })
      : original(input, init),
  );
  render(panel());
  expect(await screen.findByText(/本机任务状态待恢复/)).toBeVisible();
  expect(screen.getByRole("button", { name: "生成首帧" })).toBeDisabled();
});

test("提交响应丢失并遇到会话403后，恢复仍复用原幂等键，只创建一个任务", async () => {
  const original = vi.mocked(fetch).getMockImplementation()!;
  const jobs = new Map<string, number>();
  let attempts = 0;
  vi.mocked(fetch).mockImplementation(async (input, init) => {
    if (!String(input).endsWith("/image-jobs")) return original(input, init);
    attempts++;
    const request = JSON.parse(String(init?.body)) as {
      idempotency_key: string;
    };
    if (attempts === 2)
      return Response.json(
        { error: { code: "SESSION_EXPIRED", message: "页面会话已失效" } },
        { status: 403 },
      );
    const id = jobs.get(request.idempotency_key) ?? 12 + jobs.size;
    jobs.set(request.idempotency_key, id);
    if (attempts === 1) throw new TypeError("response lost after accepted");
    return Response.json(
      { job_id: id, status: "DONE", poll_url: `/api/v1/jobs/${id}` },
      { status: 202 },
    );
  });
  render(panel());
  const generate = await screen.findByRole("button", { name: "生成首帧" });
  await waitFor(() => expect(generate).toBeEnabled());
  fireEvent.click(generate);
  fireEvent.click(
    await screen.findByRole("button", { name: "查询或重试原提交" }),
  );
  expect(await screen.findByRole("alert")).toHaveTextContent("页面会话已失效");
  expect(sessionStorage.getItem("vm:image-intent:3")).not.toBeNull();
  const retry = screen.getByRole("button", { name: "查询或重试原提交" });
  await waitFor(() => expect(retry).toBeEnabled());
  fireEvent.click(retry);
  await waitFor(() => expect(attempts).toBe(3));
  expect(jobs.size).toBe(1);
  const bodies = vi
    .mocked(fetch)
    .mock.calls.filter(([p]) => String(p).endsWith("/image-jobs"))
    .map(([, i]) => i?.body);
  expect(new Set(bodies).size).toBe(1);
});
