import { beforeEach, expect, test, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
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
