import { beforeEach, expect, test, vi } from "vitest";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { ExportTimeline } from "@/features/exports/ExportTimeline";
import type { components } from "@/lib/api-types";
import { image } from "./shot-fixtures";
import { useWorkbenchStore } from "@/stores/workbench";

type Preview = components["schemas"]["ExportPreviewRead"];
const video = {
  ...image,
  id: 10,
  type: "video" as const,
  name: "原创片段",
  duration_seconds: 1,
  has_audio: true,
};
const clip = (id: number) => ({
  shot_id: id,
  order_index: id,
  description: `画面${id}`,
  shot_version: 1,
  selection_version: 1,
  asset: video,
  available: true,
  stale: false,
  issues: [],
  source_duration: 1,
  frame_count: 24,
  normalized_duration: 1,
  review_token: `token${id}`,
});
let preview: Preview;
const idle = {
  task: null,
  runtime_state: { state: "IDLE", version: 1, active_task_id: null },
};
beforeEach(() => {
  sessionStorage.clear();
  useWorkbenchStore.setState({ drafts: {} });
  preview = {
    project_id: 1,
    episode_id: 2,
    project_name: "原创故事",
    episode_title: "清晨",
    project_version: 2,
    episode_version: 3,
    read_only: false,
    clips: [clip(1), clip(2)],
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input) => {
      const path = String(input);
      if (path.endsWith("/bootstrap")) return Response.json({ token: "local" });
      if (path.endsWith("/jobs/active")) return Response.json(idle);
      if (path.endsWith("/export-preview")) return Response.json(preview);
      if (path.endsWith("/exports")) return Response.json([]);
      if (path.endsWith("/export-jobs"))
        return Response.json(
          { job_id: 20, status: "PENDING", poll_url: "/api/v1/jobs/20" },
          { status: 202 },
        );
      if (path.endsWith("/jobs/20"))
        return Response.json({
          id: 20,
          project_id: 1,
          episode_id: 2,
          shot_id: null,
          kind: "EXPORT",
          status: "DONE",
          stage: "已完成",
          output_asset_id: 11,
          result_json: {},
          error_message: null,
        });
      throw new Error(`unexpected ${path}`);
    }),
  );
});
const panel = () => <ExportTimeline projectId={1} episodeId={2} />;
const writes = () =>
  vi
    .mocked(fetch)
    .mock.calls.filter(([p]) => String(p).endsWith("/export-jobs"));

test("默认包含全部镜头，可排序且提交冻结顺序、来源令牌和规格，完成不重置草稿", async () => {
  render(panel());
  const submit = await screen.findByRole("button", { name: "导出 MP4" });
  await waitFor(() => expect(submit).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "上移镜头2" }));
  fireEvent.change(screen.getByLabelText("成片比例"), {
    target: { value: "landscape" },
  });
  fireEvent.click(submit);
  expect(await screen.findByText(/成片已保存/)).toBeVisible();
  expect(writes()).toHaveLength(1);
  expect(JSON.parse(String(writes()[0]?.[1]?.body))).toMatchObject({
    expected_project_version: 2,
    expected_episode_version: 3,
    output_profile: "landscape",
    clips: [
      { shot_id: 2, review_token: "token2", confirm_stale: false },
      { shot_id: 1, review_token: "token1", confirm_stale: false },
    ],
  });
  expect(screen.getByLabelText("成片比例")).toHaveValue("landscape");
  expect(screen.getAllByTestId("timeline-row")[0]).toHaveTextContent("画面2");
});

test("缺失片段不会静默跳过；明确取消勾选后才能导出", async () => {
  preview.clips[0] = {
    ...clip(1),
    asset: null,
    available: false,
    issues: ["缺少视频"],
  };
  render(panel());
  const submit = await screen.findByRole("button", { name: "导出 MP4" });
  expect(submit).toBeDisabled();
  expect(screen.getByLabelText("包含镜头1")).toBeChecked();
  fireEvent.click(screen.getByLabelText("包含镜头1"));
  await waitFor(() => expect(submit).toBeEnabled());
  fireEvent.click(submit);
  await waitFor(() => expect(writes()).toHaveLength(1));
  expect(JSON.parse(String(writes()[0]?.[1]?.body)).clips).toHaveLength(1);
});

test("刷新先显示候选来源，不改排序；显式采用来源后清除旧确认", async () => {
  preview.clips[0] = { ...clip(1), stale: true, issues: ["旧输入"] };
  render(panel());
  const submit = await screen.findByRole("button", { name: "导出 MP4" });
  expect(submit).toBeDisabled();
  fireEvent.click(screen.getByLabelText("确认使用镜头1的旧片段"));
  fireEvent.click(screen.getByRole("button", { name: "上移镜头2" }));
  preview = {
    ...preview,
    episode_version: 4,
    clips: [{ ...preview.clips[0]!, review_token: "changed" }, clip(2)],
  };
  fireEvent.click(screen.getByRole("button", { name: "刷新来源" }));
  const adopt = await screen.findByRole("button", {
    name: "使用最新来源并保留时间线排序",
  });
  expect(screen.getByLabelText("确认使用镜头1的旧片段")).toBeChecked();
  fireEvent.click(adopt);
  expect(screen.getByLabelText("确认使用镜头1的旧片段")).not.toBeChecked();
  expect(submit).toBeDisabled();
  expect(screen.getAllByTestId("timeline-row")[0]).toHaveTextContent("画面2");
  expect(writes()).toHaveLength(0);
});

test("丢失响应和403只可重试同一导出意图，重载不自动POST", async () => {
  const original = vi.mocked(fetch).getMockImplementation()!;
  let attempts = 0;
  vi.mocked(fetch).mockImplementation(async (input, init) => {
    if (String(input).endsWith("/export-jobs")) {
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
  const submit = await screen.findByRole("button", { name: "导出 MP4" });
  await waitFor(() => expect(submit).toBeEnabled());
  fireEvent.click(submit);
  fireEvent.click(
    await screen.findByRole("button", { name: "查询或重试原导出" }),
  );
  expect(await screen.findByRole("alert")).toHaveTextContent("会话已变化");
  view.unmount();
  render(panel());
  const retry = await screen.findByRole("button", { name: "查询或重试原导出" });
  expect(attempts).toBe(2);
  fireEvent.click(retry);
  await waitFor(() => expect(attempts).toBe(3));
  expect(new Set(writes().map(([, init]) => init?.body)).size).toBe(1);
  expect(sessionStorage.getItem("vm:export-intent:2")).toBeNull();
});

test("归档时间线保留历史预览和MP4、manifest下载，禁止新导出", async () => {
  preview.read_only = true;
  const original = vi.mocked(fetch).getMockImplementation()!;
  vi.mocked(fetch).mockImplementation(async (input, init) =>
    String(input).endsWith("/exports")
      ? Response.json([
          { ...video, id: 11, name: "成片 · 清晨", tags: ["final_video"] },
        ])
      : original(input, init),
  );
  render(panel());
  expect(
    await screen.findByRole("button", { name: "导出 MP4" }),
  ).toBeDisabled();
  const history = await screen.findByRole("region", { name: "成片历史" });
  expect(
    within(history).getByRole("link", { name: "下载来源清单" }),
  ).toHaveAttribute("href", "/api/v1/assets/11/manifest?project_id=1");
  expect(
    within(history).getByRole("link", { name: "下载 MP4" }),
  ).toHaveAttribute(
    "href",
    "/api/v1/assets/11/content?project_id=1&download=1",
  );
});

test("离开再返回保留时间线草稿，新的服务端来源须明确采纳", async () => {
  const view = render(panel());
  await screen.findByLabelText("成片比例");
  fireEvent.click(screen.getByRole("button", { name: "上移镜头2" }));
  fireEvent.change(screen.getByLabelText("成片比例"), {
    target: { value: "square" },
  });
  view.unmount();
  preview = { ...preview, episode_version: 4 };
  render(panel());
  expect(await screen.findByLabelText("成片比例")).toHaveValue("square");
  expect(screen.getAllByTestId("timeline-row")[0]).toHaveTextContent("画面2");
  expect(
    await screen.findByRole("button", { name: "使用最新来源并保留时间线排序" }),
  ).toBeVisible();
  expect(writes()).toHaveLength(0);
});
