import { beforeEach, expect, test, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { ScriptParser } from "@/features/storyboards/ScriptParser";
import { PromptAssistant } from "@/features/storyboards/PromptAssistant";
import { DraftReview } from "@/features/storyboards/DraftReview";
import { useWorkbenchStore } from "@/stores/workbench";
import { project, shot } from "./shot-fixtures";

const episode = {
  id: 2,
  project_id: 1,
  title: "清晨",
  script_text: "小林说早上好",
  version: 2,
  archived_at: null,
  created_at: "2026-10-26T00:00:00Z",
  updated_at: "2026-10-26T00:00:00Z",
};
const parsed = {
  shots: [
    {
      order_index: 1,
      raw_description: "小林推门",
      dialogue: "早上好",
      character_ids: ["a"],
      audio_prompt: "Door creaks.",
      duration_seconds: 3,
    },
  ],
};
const prompts = {
  image_prompt: "A person beside a door.",
  video_prompt: "The person opens the door.",
  audio_prompt: "Soft wooden creaks.",
};
let kind: "PARSE_SCRIPT" | "OPTIMIZE_PROMPTS";
const task = () => ({
  id: 20,
  project_id: 1,
  episode_id: 2,
  shot_id: kind === "PARSE_SCRIPT" ? null : 3,
  kind,
  status: "DONE",
  stage: "已完成",
  output_asset_id: null,
  result_json: { draft: kind === "PARSE_SCRIPT" ? parsed : prompts },
  error_message: null,
});
const review = (id = 20) => ({
  task_id: id,
  project_id: 1,
  episode_id: 2,
  shot_id: kind === "PARSE_SCRIPT" ? null : 3,
  kind,
  value: kind === "PARSE_SCRIPT" ? parsed : prompts,
  expected_version: kind === "PARSE_SCRIPT" ? 2 : 1,
  characters: project.characters,
  existing_shot_ids: [3],
  applied_at: null,
  apply_allowed: true,
  conflict_reason: null,
});
beforeEach(() => {
  sessionStorage.clear();
  useWorkbenchStore.setState({ drafts: {} });
  kind = "PARSE_SCRIPT";
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input, init) => {
      const path = String(input);
      if (path.endsWith("/bootstrap")) return Response.json({ token: "local" });
      if (path.endsWith("/jobs/active"))
        return Response.json({
          task: null,
          runtime_state: { state: "IDLE", version: 1 },
        });
      if (path.includes("/projects/1/jobs")) return Response.json([task()]);
      if (path.endsWith("/parse-jobs") || path.endsWith("/prompt-jobs"))
        return Response.json(
          { job_id: 20, status: "PENDING", poll_url: "/api/v1/jobs/20" },
          { status: 202 },
        );
      if (/\/jobs\/20$/.test(path)) return Response.json(task());
      if (path.includes("/draft?"))
        return Response.json(review(path.includes("/21/") ? 21 : 20));
      if (path.endsWith("/apply"))
        return Response.json(
          kind === "PARSE_SCRIPT"
            ? { ...episode, version: 3 }
            : {
                ...shot,
                version: 2,
                ...JSON.parse(String(init?.body)).edited_value,
              },
        );
      throw new Error(`unexpected ${path}`);
    }),
  );
});
const writes = (suffix: string) =>
  vi
    .mocked(fetch)
    .mock.calls.filter(
      ([p, init]) => String(p).endsWith(suffix) && init?.method === "POST",
    );
const parser = (onApplied = vi.fn()) => (
  <ScriptParser project={project} episode={episode} onApplied={onApplied} />
);

test("解析完成不自动写分镜；明确打开、编辑和确认替换后才采纳", async () => {
  const applied = vi.fn();
  render(parser(applied));
  const button = await screen.findByRole("button", {
    name: "AI 拆解已保存剧本",
  });
  await waitFor(() => expect(button).toBeEnabled());
  fireEvent.click(button);
  expect(await screen.findByText(/草稿已生成，打开审阅后再采纳/)).toBeVisible();
  expect(screen.queryByLabelText("分镜1画面描述")).toBeNull();
  expect(writes("/apply")).toHaveLength(0);
  fireEvent.click(screen.getByRole("button", { name: "审阅草稿 #20" }));
  fireEvent.change(await screen.findByLabelText("分镜1画面描述"), {
    target: { value: "人工修订的画面" },
  });
  expect(screen.getByRole("button", { name: "采纳分镜草稿" })).toBeDisabled();
  fireEvent.click(screen.getByLabelText("确认替换当前分镜，保留旧镜头与素材"));
  fireEvent.click(screen.getByRole("button", { name: "采纳分镜草稿" }));
  await waitFor(() => expect(applied).toHaveBeenCalledTimes(1));
  expect(screen.getByLabelText("分镜1画面描述")).toHaveValue("人工修订的画面");
  expect(JSON.parse(String(writes("/apply")[0]?.[1]?.body))).toMatchObject({
    expected_version: 2,
    replace_confirm: true,
    edited_value: {
      shots: [{ raw_description: "人工修订的画面", dialogue: "早上好" }],
    },
  });
});

test("未保存剧本禁止解析，人工稿保留", async () => {
  useWorkbenchStore.setState({
    drafts: {
      "episode:2": {
        kind: "episode",
        value: { title: "清晨", script_text: "人工未保存" },
        baseVersion: 2,
        dirty: true,
      },
    },
  });
  render(parser());
  expect(
    await screen.findByRole("button", { name: "AI 拆解已保存剧本" }),
  ).toBeDisabled();
  expect(useWorkbenchStore.getState().drafts["episode:2"]?.value).toMatchObject(
    { script_text: "人工未保存" },
  );
});

test("Prompt草稿审阅不覆盖手工编辑，未保存来源须先处理", async () => {
  kind = "OPTIMIZE_PROMPTS";
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
  render(
    <PromptAssistant
      project={project}
      episodeVersion={2}
      shot={shot}
      onApplied={vi.fn()}
    />,
  );
  fireEvent.click(await screen.findByRole("button", { name: "审阅草稿 #20" }));
  const field = await screen.findByLabelText("AI 生图Prompt草稿");
  fireEvent.change(field, {
    target: { value: "My carefully edited image prompt." },
  });
  expect(screen.getByRole("button", { name: "采纳Prompt草稿" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "刷新草稿状态" }));
  await waitFor(() =>
    expect(field).toHaveValue("My carefully edited image prompt."),
  );
  expect(writes("/apply")).toHaveLength(0);
});

test("已过期来源保留可编辑结果，禁止采纳且不自动重生", async () => {
  const original = vi.mocked(fetch).getMockImplementation()!;
  vi.mocked(fetch).mockImplementation(async (input, init) =>
    String(input).includes("/draft?")
      ? Response.json({
          ...review(),
          apply_allowed: false,
          conflict_reason: "镜头已变化",
        })
      : original(input, init),
  );
  render(<DraftReview taskId={20} projectId={1} onApplied={vi.fn()} />);
  expect(await screen.findByLabelText("分镜1画面描述")).toHaveValue("小林推门");
  expect(screen.getByText("镜头已变化")).toBeVisible();
  expect(screen.getByRole("button", { name: "采纳分镜草稿" })).toBeDisabled();
  expect(writes("/parse-jobs")).toHaveLength(0);
});

test("未知提交经403、重新挂载后只重试同一意图，不自动POST", async () => {
  const original = vi.mocked(fetch).getMockImplementation()!;
  let attempts = 0;
  vi.mocked(fetch).mockImplementation(async (input, init) => {
    if (String(input).endsWith("/parse-jobs")) {
      attempts++;
      if (attempts === 1) throw new TypeError("lost");
      if (attempts === 2)
        return Response.json(
          { error: { code: "SESSION_EXPIRED", message: "会话已变化" } },
          { status: 403 },
        );
    }
    return original(input, init);
  });
  const view = render(parser());
  const button = await screen.findByRole("button", {
    name: "AI 拆解已保存剧本",
  });
  await waitFor(() => expect(button).toBeEnabled());
  fireEvent.click(button);
  fireEvent.click(
    await screen.findByRole("button", { name: "查询或重试原文本提交" }),
  );
  expect(await screen.findByRole("alert")).toHaveTextContent("会话已变化");
  view.unmount();
  render(parser());
  const retry = await screen.findByRole("button", {
    name: "查询或重试原文本提交",
  });
  expect(attempts).toBe(2);
  fireEvent.click(retry);
  await waitFor(() => expect(attempts).toBe(3));
  expect(
    new Set(writes("/parse-jobs").map(([, init]) => init?.body)).size,
  ).toBe(1);
});

test("切换草稿后旧响应不进入当前编辑，已采纳结果不再次写入", async () => {
  let release: ((r: Response) => void) | undefined;
  const original = vi.mocked(fetch).getMockImplementation()!;
  vi.mocked(fetch).mockImplementation(async (input, init) =>
    String(input).includes("/20/draft?")
      ? new Promise<Response>((r) => {
          release = r;
        })
      : String(input).includes("/21/draft?")
        ? Response.json({
            ...review(21),
            value: {
              shots: [{ ...parsed.shots[0], raw_description: "新的草稿" }],
            },
            applied_at: "2026-10-26T00:00:00Z",
            apply_allowed: false,
            conflict_reason: "已采纳",
          })
        : original(input, init),
  );
  const view = render(
    <DraftReview taskId={20} projectId={1} onApplied={vi.fn()} />,
  );
  view.rerender(<DraftReview taskId={21} projectId={1} onApplied={vi.fn()} />);
  expect(await screen.findByLabelText("分镜1画面描述")).toHaveValue("新的草稿");
  release?.(Response.json(review()));
  await waitFor(() =>
    expect(screen.getByLabelText("分镜1画面描述")).toHaveValue("新的草稿"),
  );
  expect(screen.getByRole("button", { name: "采纳分镜草稿" })).toBeDisabled();
});

test("文本连接和配置不生成内容，保存期间禁止继续编辑输入", async () => {
  const { TextModelSettings } =
    await import("@/features/storyboards/TextModelSettings");
  const settings = {
    version: 1,
    text: { base_url: "http://127.0.0.1:1234/v1", model: "local-text" },
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
  const original = vi.mocked(fetch).getMockImplementation()!;
  let complete: ((response: Response) => void) | undefined;
  vi.mocked(fetch).mockImplementation(async (input, init) => {
    if (String(input).endsWith("/settings/test-text-connection"))
      return Response.json({ models: ["local-text"], message: "已连接" });
    if (String(input).endsWith("/settings"))
      return init?.method === "PUT"
        ? new Promise<Response>((resolve) => {
            complete = resolve;
          })
        : Response.json(settings);
    return original(input, init);
  });
  render(<TextModelSettings />);
  fireEvent.click(screen.getByText("文本模型连接与配置"));
  const field = await screen.findByLabelText("文本模型ID");
  fireEvent.click(screen.getByRole("button", { name: "检查文本连接" }));
  expect(await screen.findByLabelText("可用文本模型")).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "保存文本设置" }));
  await waitFor(() => expect(field).toBeDisabled());
  complete?.(Response.json({ ...settings, version: 2 }));
  await waitFor(() => expect(field).toBeEnabled());
  expect(writes("/parse-jobs")).toHaveLength(0);
  expect(writes("/prompt-jobs")).toHaveLength(0);
});
