import { beforeEach, expect, test, vi } from "vitest";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { ShotEditor } from "@/features/shots/ShotEditor";
import { ReferencePicker } from "@/features/shots/ReferencePicker";
import { SelectionPanel } from "@/features/shots/SelectionPanel";
import { ShotList } from "@/features/shots/ShotList";
import { useWorkbenchStore } from "@/stores/workbench";
import { image, project, shot } from "./shot-fixtures";

beforeEach(() => {
  useWorkbenchStore.setState({ drafts: {} });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input, init) => {
      if (String(input).endsWith("/bootstrap"))
        return Response.json({ token: "local-token" });
      if (init?.method && init.method !== "GET")
        return Response.json(
          { error: { message: "内容已被更新，请核对最新版本" } },
          { status: 409 },
        );
      return Response.json(shot);
    }),
  );
});

test("文字草稿跨镜头保留，409不覆盖或自动升级基线", async () => {
  const view = render(
    <ShotEditor shot={shot} project={project} onSaved={() => {}} />,
  );
  fireEvent.change(screen.getByLabelText("画面描述"), {
    target: { value: "人工修改" },
  });
  view.rerender(
    <ShotEditor
      shot={{ ...shot, id: 8, raw_description: "另一镜头" }}
      project={project}
      onSaved={() => {}}
    />,
  );
  expect(screen.getByLabelText("画面描述")).toHaveValue("另一镜头");
  view.rerender(
    <ShotEditor
      shot={{ ...shot, version: 2 }}
      project={project}
      onSaved={() => {}}
    />,
  );
  expect(screen.getByLabelText("画面描述")).toHaveValue("人工修改");
  fireEvent.click(screen.getByRole("button", { name: "保存分镜文字与参数" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("内容已被更新");
  const call = vi
    .mocked(fetch)
    .mock.calls.find(([, init]) => init?.method === "PATCH");
  expect(JSON.parse(String(call?.[1]?.body))).toMatchObject({
    expected_version: 1,
    raw_description: "人工修改",
  });
});

test("有序参考绑定角色并提交，归档时禁止修改", async () => {
  const onSaved = vi.fn();
  vi.mocked(fetch).mockImplementation(async (input) =>
    String(input).endsWith("/bootstrap")
      ? Response.json({ token: "local-token" })
      : Response.json({
          ...shot,
          version: 2,
          references: [{ asset_id: 4, position: 0, role: "character" }],
        }),
  );
  const view = render(
    <ReferencePicker shot={shot} assets={[image]} onSaved={onSaved} />,
  );
  fireEvent.click(screen.getByRole("checkbox", { name: "参考 原创角色" }));
  fireEvent.change(screen.getByLabelText("参考用途 1"), {
    target: { value: "character" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存参考图" }));
  await waitFor(() => expect(onSaved).toHaveBeenCalled());
  const call = vi
    .mocked(fetch)
    .mock.calls.find(([, init]) => init?.method === "PUT");
  expect(JSON.parse(String(call?.[1]?.body))).toEqual({
    expected_version: 1,
    references: [{ asset_id: 4, role: "character" }],
  });
  view.rerender(
    <ReferencePicker
      shot={{ ...shot, read_only: true }}
      assets={[image]}
      onSaved={onSaved}
    />,
  );
  expect(screen.getByRole("button", { name: "保存参考图" })).toBeDisabled();
});

test("采用上传首帧须明确确认，旧视频不会被清除", async () => {
  const current = {
    ...shot,
    selection: {
      ...shot.selection,
      selected_video_asset_id: 7,
      video_stale: true,
      video_issue: "视频对应旧版本输入",
    },
  };
  render(<SelectionPanel shot={current} assets={[image]} onSaved={() => {}} />);
  expect(screen.getByText(/视频对应旧版本输入/)).toBeVisible();
  fireEvent.change(screen.getByLabelText("首帧版本"), {
    target: { value: "4" },
  });
  expect(screen.getByRole("button", { name: "采用首帧" })).toBeDisabled();
  fireEvent.click(
    screen.getByRole("checkbox", { name: "确认上传素材适用于当前镜头输入" }),
  );
  fireEvent.click(screen.getByRole("button", { name: "采用首帧" }));
  await screen.findByRole("alert");
  const call = vi
    .mocked(fetch)
    .mock.calls.find(([, init]) => init?.method === "PUT");
  expect(JSON.parse(String(call?.[1]?.body))).toEqual({
    shot_version: 1,
    selection_version: 1,
    first_frame_asset_id: 4,
    confirm_uploaded: true,
    expected_image_input_hash: "input1",
  });
});

test("分镜列表使用读取到的剧集版本重排", async () => {
  const other = { ...shot, id: 8, order_index: 2, raw_description: "走入院中" };
  vi.mocked(fetch).mockImplementation(async (input) => {
    if (String(input).endsWith("/bootstrap"))
      return Response.json({ token: "local-token" });
    if (String(input).endsWith("/shots/reorder"))
      return Response.json([
        { ...other, order_index: 1 },
        { ...shot, order_index: 2 },
      ]);
    if (String(input).endsWith("/assets")) return Response.json([]);
    return Response.json({ episode_version: 6, shots: [shot, other] });
  });
  render(<ShotList project={project} episodeId={2} readonly={false} />);
  await screen.findByRole("button", { name: "上移镜头 2" });
  fireEvent.click(screen.getByRole("button", { name: "上移镜头 2" }));
  await waitFor(() =>
    expect(
      vi
        .mocked(fetch)
        .mock.calls.some(([input]) => String(input).endsWith("/shots/reorder")),
    ).toBe(true),
  );
  const call = vi
    .mocked(fetch)
    .mock.calls.find(([input]) => String(input).endsWith("/shots/reorder"));
  expect(JSON.parse(String(call?.[1]?.body))).toEqual({
    expected_version: 6,
    shot_ids: [8, 3],
  });
});

test("旧刷新响应不得回退刚保存的分镜和已清除的草稿", async () => {
  let completeRefresh: ((value: Response) => void) | undefined;
  let listReads = 0;
  const updated = { ...shot, raw_description: "刚保存的新画面", version: 2 };
  vi.mocked(fetch).mockImplementation(async (input, init) => {
    const url = String(input);
    if (url.endsWith("/bootstrap"))
      return Response.json({ token: "local-token" });
    if (init?.method === "PATCH") return Response.json(updated);
    if (url.endsWith("/assets")) return Response.json([]);
    if (url.endsWith("/shots")) {
      listReads += 1;
      if (listReads === 2)
        return new Promise<Response>((resolve) => {
          completeRefresh = resolve;
        });
      return Response.json({ episode_version: 1, shots: [shot] });
    }
    return Response.json(updated);
  });
  render(<ShotList project={project} episodeId={2} readonly={false} />);
  await screen.findByLabelText("画面描述");
  fireEvent.change(screen.getByLabelText("画面描述"), {
    target: { value: "刚保存的新画面" },
  });
  fireEvent.click(screen.getByRole("button", { name: "刷新素材与分镜" }));
  fireEvent.click(screen.getByRole("button", { name: "保存分镜文字与参数" }));
  await screen.findByText("分镜文字与参数已保存");
  expect(useWorkbenchStore.getState().drafts["shot:3"]).toBeUndefined();
  await act(async () => {
    completeRefresh?.(Response.json({ episode_version: 1, shots: [shot] }));
  });
  expect(screen.getByLabelText("画面描述")).toHaveValue("刚保存的新画面");
});

test("取消采用确认不得升级到后来选中的素材版本", () => {
  const first = {
    ...shot,
    selection: {
      ...shot.selection,
      version: 2,
      first_frame_asset_id: image.id,
    },
  };
  const view = render(
    <SelectionPanel shot={first} assets={[image]} onSaved={() => {}} />,
  );
  fireEvent.click(screen.getByRole("button", { name: "取消首帧采用" }));
  view.rerender(
    <SelectionPanel
      shot={{
        ...first,
        selection: {
          ...first.selection,
          version: 3,
          first_frame_asset_id: 9,
        },
      }}
      assets={[image, { ...image, id: 9, name: "另一首帧" }]}
      onSaved={() => {}}
    />,
  );
  expect(screen.getByRole("button", { name: "确认取消采用" })).toBeDisabled();
  expect(screen.getByText(/取消确认已失效/)).toBeVisible();
});

test("刷新旧选择快照不得撤回刚采用的首帧", async () => {
  let completeRefresh: ((value: Response) => void) | undefined;
  let listReads = 0;
  const adopted = {
    ...shot,
    selection: {
      ...shot.selection,
      version: 2,
      first_frame_asset_id: image.id,
    },
  };
  vi.mocked(fetch).mockImplementation(async (input, init) => {
    const url = String(input);
    if (url.endsWith("/bootstrap"))
      return Response.json({ token: "local-token" });
    if (init?.method === "PUT") return Response.json(adopted);
    if (url.endsWith("/assets")) return Response.json([image]);
    if (url.endsWith("/shots")) {
      listReads += 1;
      if (listReads === 2)
        return new Promise<Response>((resolve) => {
          completeRefresh = resolve;
        });
      return Response.json({ episode_version: 1, shots: [shot] });
    }
    return Response.json(adopted);
  });
  render(<ShotList project={project} episodeId={2} readonly={false} />);
  await screen.findByLabelText("首帧版本");
  fireEvent.change(screen.getByLabelText("首帧版本"), {
    target: { value: "4" },
  });
  fireEvent.click(
    screen.getByRole("checkbox", { name: "确认上传素材适用于当前镜头输入" }),
  );
  fireEvent.click(screen.getByRole("button", { name: "刷新素材与分镜" }));
  fireEvent.click(screen.getByRole("button", { name: "采用首帧" }));
  await screen.findByText("已明确采用此版本");
  await act(async () => {
    completeRefresh?.(Response.json({ episode_version: 1, shots: [shot] }));
  });
  expect(screen.queryByText("尚未采用首帧")).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "取消首帧采用" })).toBeEnabled();
});

test("解析采纳后的自动刷新晚到时不得回退新镜头已保存的内容", async () => {
  const parsed = { ...shot, id: 5, raw_description: "解析后的新画面" };
  let shotReads = 0,
    assetReads = 0;
  let finishSlowAssets: ((response: Response) => void) | undefined;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input, init) => {
      const path = String(input);
      if (path.endsWith("/bootstrap")) return Response.json({ token: "local" });
      if (path.endsWith("/jobs/active"))
        return Response.json({
          task: null,
          runtime_state: { state: "IDLE", version: 1, active_task_id: null },
        });
      if (path.includes("/jobs?limit=")) return Response.json([]);
      if (path.endsWith("/episodes/2/shots")) {
        shotReads++;
        return Response.json(
          shotReads === 1
            ? { episode_version: 2, shots: [shot] }
            : { episode_version: 3, shots: [parsed] },
        );
      }
      if (path.endsWith("/projects/1/assets")) {
        assetReads++;
        return assetReads === 2
          ? new Promise<Response>((resolve) => {
              finishSlowAssets = resolve;
            })
          : Response.json(assetReads > 2 ? [image] : []);
      }
      if (path.endsWith("/shots/5") && init?.method === "PATCH")
        return Response.json({
          ...parsed,
          version: 2,
          raw_description: "人工保存的新画面",
        });
      throw new Error(`unexpected ${path}`);
    }),
  );
  const view = render(
    <ShotList
      project={project}
      episodeId={2}
      readonly={false}
      refreshRevision={0}
    />,
  );
  await screen.findByLabelText("画面描述");
  view.rerender(
    <ShotList
      project={project}
      episodeId={2}
      readonly={false}
      refreshRevision={1}
    />,
  );
  await waitFor(() => expect(assetReads).toBe(2));
  fireEvent.click(screen.getByRole("button", { name: "刷新素材与分镜" }));
  await waitFor(() =>
    expect(screen.getByLabelText("画面描述")).toHaveValue("解析后的新画面"),
  );
  fireEvent.change(screen.getByLabelText("画面描述"), {
    target: { value: "人工保存的新画面" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存分镜文字与参数" }));
  await waitFor(() => expect(screen.getByText("版本 2")).toBeVisible());
  expect(screen.getByRole("checkbox", { name: "参考 原创角色" })).toBeVisible();
  await act(async () => {
    finishSlowAssets?.(Response.json([]));
  });
  expect(screen.getByLabelText("画面描述")).toHaveValue("人工保存的新画面");
  expect(useWorkbenchStore.getState().drafts["shot:5"]).toBeUndefined();
  expect(screen.getByRole("checkbox", { name: "参考 原创角色" })).toBeVisible();
});

test("旧采用成功响应晚到，不得回退已保存文字和已清除草稿", async () => {
  let finishSelection: ((value: Response) => void) | undefined;
  const adopted = {
    ...shot,
    selection: {
      ...shot.selection,
      version: 2,
      first_frame_asset_id: image.id,
    },
  };
  const edited = {
    ...adopted,
    version: 2,
    raw_description: "已保存的新画面",
  };
  vi.mocked(fetch).mockImplementation(async (input, init) => {
    const url = String(input);
    if (url.endsWith("/bootstrap"))
      return Response.json({ token: "local-token" });
    if (url.endsWith("/selection") && init?.method === "PUT")
      return new Promise<Response>((resolve) => {
        finishSelection = resolve;
      });
    if (init?.method === "PATCH") return Response.json(edited);
    if (url.endsWith("/assets")) return Response.json([image]);
    if (url.endsWith("/shots"))
      return Response.json({ episode_version: 1, shots: [shot] });
    return Response.json(shot);
  });
  render(<ShotList project={project} episodeId={2} readonly={false} />);
  await screen.findByLabelText("画面描述");
  fireEvent.change(screen.getByLabelText("首帧版本"), {
    target: { value: String(image.id) },
  });
  fireEvent.click(
    screen.getByRole("checkbox", { name: "确认上传素材适用于当前镜头输入" }),
  );
  fireEvent.click(screen.getByRole("button", { name: "采用首帧" }));
  await waitFor(() => expect(finishSelection).toBeDefined());
  fireEvent.change(screen.getByLabelText("画面描述"), {
    target: { value: edited.raw_description },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存分镜文字与参数" }));
  await screen.findByText("分镜文字与参数已保存");
  expect(useWorkbenchStore.getState().drafts["shot:3"]).toBeUndefined();
  await act(async () => {
    finishSelection!(Response.json(adopted));
  });
  expect(screen.getByLabelText("画面描述")).toHaveValue(edited.raw_description);
  expect(screen.getByRole("button", { name: "取消首帧采用" })).toBeEnabled();
  expect(screen.getByText("选择版本 2")).toBeVisible();
  // 下一次写入继续使用较新版本，不能被旧采用响应退回version=1。
  fireEvent.change(screen.getByLabelText("画面描述"), {
    target: { value: "再次编辑" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存分镜文字与参数" }));
  await waitFor(() => {
    const writes = vi
      .mocked(fetch)
      .mock.calls.filter(([, init]) => init?.method === "PATCH");
    expect(writes).toHaveLength(2);
    expect(JSON.parse(String(writes[1]?.[1]?.body)).expected_version).toBe(2);
  });
});
