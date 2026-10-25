import { beforeEach, expect, test, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ProjectList } from "@/features/projects/ProjectList";
import { ProjectForm } from "@/features/projects/ProjectForm";
import { EpisodeForm } from "@/features/projects/EpisodeForm";
import { useWorkbenchStore } from "@/stores/workbench";

const first = {
  id: 1,
  name: "沿途",
  style_anchor: "自然光",
  characters: [],
  archived_at: null,
  version: 1,
  created_at: "2026-10-25T00:00:00Z",
  updated_at: "2026-10-25T00:00:00Z",
};
const second = { ...first, id: 2, name: "山中", style_anchor: "水墨" };
const episode = {
  ...first,
  id: 3,
  project_id: 1,
  title: "清晨",
  script_text: "原始剧本",
};

beforeEach(() => {
  useWorkbenchStore.setState({ drafts: {} });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith("/bootstrap"))
        return Response.json({
          token: "local-token",
          capabilities: { generation: false },
        });
      if (url.endsWith("/projects") && init?.method === "POST") {
        const body = JSON.parse(String(init.body)) as { name: string };
        return Response.json({ ...first, name: body.name }, { status: 201 });
      }
      if (url.endsWith("/projects")) return Response.json([]);
      if (url.endsWith("/episodes") && init?.method === "POST") {
        return Response.json(
          { ...episode, ...JSON.parse(String(init.body)) },
          { status: 201 },
        );
      }
      return Response.json(
        { error: { message: "内容已被更新，请重新核对最新版本" } },
        { status: 409 },
      );
    }),
  );
});

test("创建项目后展示可进入的项目入口", async () => {
  const user = userEvent.setup();
  render(<ProjectList />);
  await screen.findByText("还没有项目");
  await user.click(screen.getByRole("button", { name: "新建项目" }));
  await user.type(screen.getByLabelText("项目名称"), "沿途");
  await user.click(screen.getByRole("button", { name: "创建项目" }));
  const link = await screen.findByRole("link", { name: /沿途/ });
  expect(link).toHaveAttribute("href", "/projects/1");
});

test("剧集表单提交原创剧本并显示已创建结果", async () => {
  const user = userEvent.setup();
  const onSaved = vi.fn();
  render(<EpisodeForm projectId={1} onSaved={onSaved} />);
  await user.type(screen.getByLabelText("剧集标题"), "清晨");
  await user.type(
    screen.getByLabelText("原始剧本"),
    "街灯熄灭，小林推开木门。",
  );
  await user.click(screen.getByRole("button", { name: "创建剧集" }));
  expect(await screen.findByRole("status")).toHaveTextContent("剧集已创建");
  expect(onSaved).toHaveBeenCalledWith(
    expect.objectContaining({
      project_id: 1,
      title: "清晨",
      script_text: "街灯熄灭，小林推开木门。",
    }),
  );
});

test("切换项目不串表单，返回时保留本项目未保存草稿", async () => {
  const view = render(<ProjectForm project={first} onSaved={() => {}} />);
  fireEvent.change(screen.getByLabelText("项目名称"), {
    target: { value: "沿途的修改" },
  });
  view.rerender(<ProjectForm project={second} onSaved={() => {}} />);
  expect(screen.getByLabelText("项目名称")).toHaveValue("山中");
  expect(screen.getByLabelText("全局风格描述")).toHaveValue("水墨");
  view.rerender(<ProjectForm project={first} onSaved={() => {}} />);
  expect(screen.getByLabelText("项目名称")).toHaveValue("沿途的修改");
});

test("409保留人工修改和原已查看版本", async () => {
  render(<ProjectForm project={first} onSaved={() => {}} />);
  fireEvent.change(screen.getByLabelText("全局风格描述"), {
    target: { value: "改成暖色自然光" },
  });
  fireEvent.click(screen.getByRole("button", { name: "保存项目设置" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("内容已被更新");
  expect(screen.getByLabelText("全局风格描述")).toHaveValue("改成暖色自然光");
  await waitFor(() =>
    expect(screen.getByRole("button", { name: "保存项目设置" })).toBeEnabled(),
  );
  const calls = vi.mocked(fetch).mock.calls;
  const write = calls.find(([, init]) => init?.method === "PATCH");
  expect(JSON.parse(String(write?.[1]?.body))).toMatchObject({
    expected_version: 1,
  });
});

test("角色表单新增及移除角色，归档项目保持只读", async () => {
  const view = render(<ProjectForm project={first} onSaved={() => {}} />);
  fireEvent.click(screen.getByRole("button", { name: "添加角色" }));
  fireEvent.change(screen.getByLabelText("角色名称 1"), {
    target: { value: "小林" },
  });
  expect(screen.getByLabelText("角色名称 1")).toHaveValue("小林");
  fireEvent.click(screen.getByRole("button", { name: "移除角色 1" }));
  expect(screen.queryByLabelText("角色名称 1")).not.toBeInTheDocument();
  view.rerender(
    <ProjectForm
      project={{ ...second, archived_at: "2026-10-25T00:00:00Z" }}
      onSaved={() => {}}
    />,
  );
  expect(screen.getByLabelText("项目名称")).toBeDisabled();
  expect(screen.getByRole("button", { name: "保存项目设置" })).toBeDisabled();
});

test("冲突后明确核对最新内容，保留草稿并重新保存", async () => {
  const user = userEvent.setup();
  const onSaved = vi.fn();
  vi.mocked(fetch).mockImplementation(async (input, init) => {
    if (String(input).endsWith("/bootstrap"))
      return Response.json({ token: "local-token" });
    if (!init?.method || init.method === "GET")
      return Response.json({ ...first, name: "另一页面的新片名", version: 2 });
    const body = JSON.parse(String(init.body)) as {
      expected_version: number;
      name: string;
    };
    if (body.expected_version === 1)
      return Response.json(
        { error: { message: "内容已被更新，请重新核对最新版本" } },
        { status: 409 },
      );
    return Response.json({ ...first, ...body, version: 3 });
  });
  render(<ProjectForm project={first} onSaved={onSaved} />);
  fireEvent.change(screen.getByLabelText("项目名称"), {
    target: { value: "保留本页片名" },
  });
  await user.click(screen.getByRole("button", { name: "保存项目设置" }));
  await screen.findByRole("alert");
  await user.click(screen.getByRole("button", { name: "核对最新版本" }));
  expect(await screen.findByText("另一页面的新片名")).toBeVisible();
  expect(screen.getByLabelText("项目名称")).toHaveValue("保留本页片名");
  await user.click(
    screen.getByRole("button", { name: "保留草稿并使用此版本" }),
  );
  await user.click(screen.getByRole("button", { name: "保存项目设置" }));
  expect(await screen.findByRole("status")).toHaveTextContent("项目设置已保存");
  expect(onSaved).toHaveBeenCalledWith(
    expect.objectContaining({ name: "保留本页片名", version: 3 }),
  );
});
