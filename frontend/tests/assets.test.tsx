import { beforeEach, expect, test, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { AssetLibrary } from "@/features/assets/AssetLibrary";
import { AssetUpload } from "@/features/assets/AssetUpload";

export const asset = {
  id: 4,
  project_id: 1,
  shot_id: null,
  source_task_id: null,
  name: "原创角色",
  type: "image" as const,
  tags: ["reference"],
  file_path: "/data/media/1/a.png",
  thumbnail_path: "/data/media/1/b.png",
  sha256: "abc",
  size_bytes: 1024,
  mime_type: "image/png",
  width: 96,
  height: 64,
  duration_seconds: null,
  has_audio: false,
  audio_sample_rate: null,
  audio_channels: null,
  metadata_json: {},
  deleted_at: null,
  version: 1,
  created_at: "2026-10-25T00:00:00Z",
  updated_at: "2026-10-25T00:00:00Z",
};

beforeEach(() => {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input, init) => {
      if (String(input).endsWith("/bootstrap"))
        return Response.json({ token: "local-token" });
      if (init?.method === "POST") return Response.json(asset, { status: 201 });
      return Response.json([asset]);
    }),
  );
});

test("上传使用FormData和会话令牌，不手写multipart边界", async () => {
  const onUploaded = vi.fn();
  render(<AssetUpload projectId={1} onUploaded={onUploaded} />);
  const file = new File(["original"], "原创.png", { type: "image/png" });
  await userEvent.upload(screen.getByLabelText("上传素材文件"), file);
  fireEvent.click(screen.getByRole("button", { name: "导入素材" }));
  await waitFor(() => expect(onUploaded).toHaveBeenCalledWith(asset));
  const call = vi
    .mocked(fetch)
    .mock.calls.find(([, options]) => options?.method === "POST");
  expect(call?.[1]?.body).toBeInstanceOf(FormData);
  expect(call?.[1]?.headers).toMatchObject({ "X-Local-Token": "local-token" });
  expect(call?.[1]?.headers).not.toHaveProperty("Content-Type");
});

test("资产库显示预览下载，可按类型用途筛选，归档后禁止写入", async () => {
  const view = render(<AssetLibrary projectId={1} readonly={false} />);
  await screen.findByRole("button", { name: "预览 原创角色" });
  fireEvent.click(screen.getByRole("button", { name: "预览 原创角色" }));
  expect(screen.getByRole("link", { name: "下载素材" })).toHaveAttribute(
    "href",
    "/api/v1/assets/4/content?project_id=1&download=1",
  );
  fireEvent.change(screen.getByLabelText("素材类型"), {
    target: { value: "video" },
  });
  expect(
    screen.queryByRole("button", { name: "预览 原创角色" }),
  ).not.toBeInTheDocument();
  view.rerender(<AssetLibrary projectId={1} readonly />);
  expect(screen.getByLabelText("上传素材文件")).toBeDisabled();
});

test("导入失败保留已选文件并显示错误", async () => {
  vi.mocked(fetch).mockImplementation(async (input) =>
    String(input).endsWith("/bootstrap")
      ? Response.json({ token: "local-token" })
      : Response.json({ error: { message: "图片损坏" } }, { status: 422 }),
  );
  render(<AssetUpload projectId={1} onUploaded={() => {}} />);
  const input = screen.getByLabelText("上传素材文件") as HTMLInputElement;
  await userEvent.upload(
    input,
    new File(["bad"], "bad.png", { type: "image/png" }),
  );
  fireEvent.click(screen.getByRole("button", { name: "导入素材" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("图片损坏");
  expect(input.files).toHaveLength(1);
});
