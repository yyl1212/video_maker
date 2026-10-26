import { act, fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, afterEach, expect, test, vi } from "vitest";
import { TaskBar } from "@/features/jobs/TaskBar";
import { useTaskPolling } from "@/features/jobs/useTaskPolling";
import { useWorkbenchStore } from "@/stores/workbench";
import type { TaskDetail, RuntimeRead } from "@/lib/api";

const task: TaskDetail = {
  id: 8,
  project_id: 1,
  episode_id: 2,
  shot_id: 3,
  kind: "IMAGE",
  status: "RUNNING",
  stage: "等待本机模型",
  progress: null,
  output_asset_id: null,
  error_code: null,
  error_message: null,
  result_summary: null,
  result_json: null,
  heartbeat_at: "2026-10-25T00:00:00Z",
  deadline_at: "2026-10-25T00:09:30Z",
  started_at: "2026-10-25T00:00:00Z",
  finished_at: null,
  created_at: "2026-10-25T00:00:00Z",
  updated_at: "2026-10-25T00:00:00Z",
};
const runtime: RuntimeRead = {
  version: 1,
  state: "ACTIVE",
  active_task_id: 8,
  reason: "",
  affected_backend: null,
  updated_at: "2026-10-25T00:00:00Z",
};

function Probe({ id }: { id: number | null }) {
  const polling = useTaskPolling(id);
  return (
    <>
      <span>{polling.task?.stage ?? "正在读取"}</span>
      <span>{polling.connection}</span>
      <button onClick={polling.refresh}>明确刷新</button>
    </>
  );
}
async function tick(ms = 0) {
  await act(async () => {
    await vi.advanceTimersByTimeAsync(ms);
  });
}

beforeEach(() => {
  vi.useFakeTimers();
  sessionStorage.clear();
  vi.spyOn(document, "hidden", "get").mockReturnValue(false);
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => Response.json(task)),
  );
  useWorkbenchStore.setState({ drafts: {} });
});
afterEach(() => {
  vi.useRealTimers();
  vi.restoreAllMocks();
});

test("每2秒轮询，终态停止，明确刷新可再次读取", async () => {
  render(<Probe id={8} />);
  await tick();
  expect(fetch).toHaveBeenCalledTimes(1);
  await tick(1999);
  expect(fetch).toHaveBeenCalledTimes(1);
  vi.mocked(fetch).mockResolvedValue(
    Response.json({ ...task, status: "DONE", stage: "已完成" }),
  );
  await tick(1);
  expect(fetch).toHaveBeenCalledTimes(2);
  await tick(10000);
  expect(fetch).toHaveBeenCalledTimes(2);
  fireEvent.click(screen.getByRole("button", { name: "明确刷新" }));
  await tick();
  expect(fetch).toHaveBeenCalledTimes(3);
});

test("隐藏页使用5秒间隔，卸载后清理计时器", async () => {
  const view = render(<Probe id={8} />);
  await tick();
  vi.spyOn(document, "hidden", "get").mockReturnValue(true);
  fireEvent(document, new Event("visibilitychange"));
  await tick(4999);
  expect(fetch).toHaveBeenCalledTimes(1);
  await tick(1);
  expect(fetch).toHaveBeenCalledTimes(2);
  view.unmount();
  await tick(10000);
  expect(fetch).toHaveBeenCalledTimes(2);
});

test("切换任务丢弃旧响应，网络错误只GET重连，不重发生成", async () => {
  let old: ((response: Response) => void) | undefined;
  vi.mocked(fetch).mockImplementation(async (input) => {
    if (String(input).endsWith("/8"))
      return new Promise<Response>((resolve) => {
        old = resolve;
      });
    return Response.json({ ...task, id: 9, stage: "新任务" });
  });
  const view = render(<Probe id={8} />);
  await tick();
  view.rerender(<Probe id={9} />);
  await tick();
  await act(async () => {
    old?.(Response.json({ ...task, stage: "旧任务晚响应" }));
  });
  expect(screen.getByText("新任务")).toBeVisible();
  expect(screen.queryByText("旧任务晚响应")).not.toBeInTheDocument();
  vi.mocked(fetch).mockRejectedValue(new TypeError("offline"));
  await tick(2000);
  expect(screen.getByText("disconnected")).toBeVisible();
  await tick(2000);
  expect(
    vi
      .mocked(fetch)
      .mock.calls.every(([, init]) => !init?.method || init.method === "GET"),
  ).toBe(true);
});

test("刷新页面从全局任务恢复ID且不覆盖表单草稿", async () => {
  useWorkbenchStore.getState().setDraft("episode:2", {
    kind: "episode",
    value: { title: "未保存标题", script_text: "原创草稿" },
    baseVersion: 1,
    dirty: true,
  });
  vi.mocked(fetch).mockImplementation(async (input) =>
    Response.json(
      String(input).endsWith("/active")
        ? { task, runtime_state: runtime }
        : task,
    ),
  );
  const view = render(<TaskBar />);
  await tick();
  expect(screen.getByText("等待本机模型")).toBeVisible();
  expect(screen.queryByRole("progressbar")).not.toBeInTheDocument();
  view.unmount();
  render(<TaskBar />);
  await tick();
  expect(screen.getByText("等待本机模型")).toBeVisible();
  expect(useWorkbenchStore.getState().drafts["episode:2"]?.dirty).toBe(true);
});

test("NEEDS_RESET显示原因，只有明确确认才POST，确认不自动升级版本", async () => {
  let current = {
    ...runtime,
    state: "NEEDS_RESET" as const,
    affected_backend: "mlx",
    reason: "模型可能仍在执行，请先停止服务",
  };
  vi.mocked(fetch).mockImplementation(async (input) => {
    if (String(input).endsWith("/active"))
      return Response.json({ task: null, runtime_state: current });
    if (String(input).endsWith("/bootstrap"))
      return Response.json({ token: "local-token" });
    return Response.json({
      ...task,
      status: "FAILED",
      error_message: "执行未知",
    });
  });
  render(<TaskBar />);
  await tick();
  expect(screen.getByText(current.reason)).toBeVisible();
  const button = screen.getByRole("button", { name: "恢复执行状态" });
  expect(button).toBeDisabled();
  fireEvent.click(
    screen.getByRole("checkbox", {
      name: "我已停止或重启受影响服务，并确认任务已停止",
    }),
  );
  expect(button).toBeEnabled();
  current = { ...current, version: 2 };
  await tick(2000);
  expect(button).toBeDisabled();
  expect(
    vi.mocked(fetch).mock.calls.some(([, init]) => init?.method === "POST"),
  ).toBe(false);
});

test("明确恢复只提交一次，后续轮询不会重复POST", async () => {
  let current: RuntimeRead = {
    ...runtime,
    state: "NEEDS_RESET",
    affected_backend: "mlx",
    reason: "上次模型执行未知",
  };
  vi.mocked(fetch).mockImplementation(async (input, init) => {
    const path = String(input);
    if (path.endsWith("/active"))
      return Response.json({ task: null, runtime_state: current });
    if (path.endsWith("/bootstrap"))
      return Response.json({ token: "local-token" });
    if (init?.method === "POST") {
      current = {
        ...current,
        version: 2,
        state: "IDLE",
        active_task_id: null,
        reason: "",
        affected_backend: null,
      };
      return Response.json(current);
    }
    return Response.json({ ...task, status: "FAILED", stage: "未完成" });
  });
  render(<TaskBar />);
  await tick();
  fireEvent.click(
    screen.getByRole("checkbox", {
      name: "我已停止或重启受影响服务，并确认任务已停止",
    }),
  );
  fireEvent.click(screen.getByRole("button", { name: "恢复执行状态" }));
  await tick();
  expect(screen.getByRole("status")).toHaveTextContent("执行状态已恢复");
  await tick(10000);
  const posts = vi
    .mocked(fetch)
    .mock.calls.filter(([, init]) => init?.method === "POST");
  expect(posts).toHaveLength(1);
  expect(JSON.parse(String(posts[0]?.[1]?.body))).toEqual({
    expected_version: 1,
    backend: "mlx",
    confirmed_stopped: true,
  });
});

test("连接失败不宣称空闲，失效的本页任务记录可以清除", async () => {
  sessionStorage.setItem("video-maker:last-job-id", "999");
  vi.mocked(fetch).mockImplementation(async (input) =>
    String(input).endsWith("/active")
      ? Response.json({
          task: null,
          runtime_state: { ...runtime, state: "IDLE", active_task_id: null },
        })
      : Response.json({ error: { message: "任务不存在" } }, { status: 404 }),
  );
  render(<TaskBar />);
  await tick();
  fireEvent.click(screen.getByRole("button", { name: "清除本页任务记录" }));
  await tick();
  expect(sessionStorage.getItem("video-maker:last-job-id")).toBeNull();
  vi.mocked(fetch).mockRejectedValue(new TypeError("offline"));
  await tick(2000);
  expect(screen.getByText("连接中断，状态待确认")).toBeVisible();
  expect(screen.queryByText("当前空闲")).not.toBeInTheDocument();
});
