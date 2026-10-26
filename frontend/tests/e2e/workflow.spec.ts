import {
  expect,
  test,
  type APIRequestContext,
  type Page,
} from "@playwright/test";
import { createHash, randomUUID } from "node:crypto";
import { readFile } from "node:fs/promises";
import { resolve } from "node:path";
import type { components } from "../../src/lib/api-types";

type Project = components["schemas"]["ProjectRead"];
type Episode = components["schemas"]["EpisodeRead"];
type Shot = components["schemas"]["ShotRead"];
type Task = components["schemas"]["TaskDetail"];
type Asset = components["schemas"]["AssetRead"];

async function api<T>(
  request: APIRequestContext,
  method: string,
  path: string,
  data?: unknown,
): Promise<T> {
  const token = (await (await request.get("/api/v1/bootstrap")).json())
    .token as string;
  const result = await request.fetch(`/api/v1${path}`, {
    method,
    data,
    headers: { "X-Local-Token": token, Origin: "http://127.0.0.1:3000" },
  });
  expect(result.ok(), `${method} ${path}: ${await result.text()}`).toBeTruthy();
  return (await result.json()) as T;
}
async function seed(request: APIRequestContext, withShot = true) {
  const project = await api<Project>(request, "POST", "/projects", {
    name: `原创验收 ${randomUUID().slice(0, 8)}`,
    style_anchor: "暖光微缩摄影",
    characters: [
      { id: "robot", name: "小木", description: "橄榄绿木制机器人，橙围巾" },
    ],
  });
  let episode = await api<Episode>(
    request,
    "POST",
    `/projects/${project.id}/episodes`,
    { title: "第一集 · 问候", script_text: "小木在门前挥手说：“你好。”" },
  );
  const shot = withShot
    ? await api<Shot>(request, "POST", `/episodes/${episode.id}/shots`, {
        raw_description: "小木在门前挥手",
        character_ids: ["robot"],
        image_prompt: "An original wooden robot beside a door.",
        video_prompt: "The wooden robot gently waves.",
        audio_prompt: "Soft wooden clicks.",
        dialogue: "你好。",
        duration_seconds: 1,
      })
    : null;
  episode = await api<Episode>(request, "GET", `/episodes/${episode.id}`);
  return {
    project,
    episode,
    shot,
    url: `/projects/${project.id}/episodes/${episode.id}`,
  };
}
async function waitDone(request: APIRequestContext, id: number) {
  await expect
    .poll(async () => (await api<Task>(request, "GET", `/jobs/${id}`)).status, {
      timeout: 20000,
    })
    .toMatch(/DONE|FAILED/);
  const result = await api<Task>(request, "GET", `/jobs/${id}`);
  expect(result.status, result.error_message ?? "").toBe("DONE");
  return result;
}
async function clickJob(
  page: Page,
  click: () => Promise<unknown>,
  suffix: string,
) {
  const response = page.waitForResponse(
    (r) => r.request().method() === "POST" && r.url().endsWith(suffix),
  );
  await click();
  const accepted = await response;
  expect(accepted.status()).toBe(202);
  return (await accepted.json()).job_id as number;
}
const sha = (bytes: Buffer) => createHash("sha256").update(bytes).digest("hex");

test.beforeEach(async ({ context, request }) => {
  await expect
    .poll(
      async () =>
        (
          await api<components["schemas"]["ActiveJobRead"]>(
            request,
            "GET",
            "/jobs/active",
          )
        ).runtime_state.state,
      { timeout: 20000 },
    )
    .toBe("IDLE");
  context.on("request", (request) => {
    const url = new URL(request.url());
    expect(["127.0.0.1", "localhost", "[::1]"]).toContain(url.hostname);
  });
});

test("真实浏览器从解析草稿到首帧、音画、采用和成片下载", async ({
  page,
  request,
}, testInfo) => {
  const work = await seed(request, false);
  await page.goto(`/projects/${work.project.id}/assets`);
  await page
    .getByLabel("上传素材文件")
    .setInputFiles(resolve(__dirname, "../../../docs/images/m5-reference.png"));
  const upload = page.waitForResponse(
    (r) =>
      r.request().method() === "POST" &&
      r.url().endsWith(`/projects/${work.project.id}/assets`),
  );
  await page.getByRole("button", { name: "导入素材", exact: true }).click();
  expect((await upload).status()).toBe(201);
  await page.goto(work.url);
  const parser = page.getByRole("region", { name: "AI 剧本解析", exact: true });
  const parsedId = await clickJob(
    page,
    () => parser.getByRole("button", { name: "AI 拆解已保存剧本" }).click(),
    "/parse-jobs",
  );
  await parser
    .getByRole("button", { name: `审阅草稿 #${parsedId}`, exact: true })
    .click();
  await parser
    .getByLabel("分镜1画面描述", { exact: true })
    .fill("小木在门前轻轻挥手，保持原角色。");
  await parser
    .getByRole("button", { name: "采纳分镜草稿", exact: true })
    .click();
  await expect(page.getByLabel("画面描述", { exact: true })).toHaveValue(
    "小木在门前轻轻挥手，保持原角色。",
  );
  const prompts = page.getByRole("region", {
    name: "AI Prompt辅助",
    exact: true,
  });
  const promptId = await clickJob(
    page,
    () => prompts.getByRole("button", { name: "AI 扩写三类 Prompt" }).click(),
    "/prompt-jobs",
  );
  await prompts
    .getByRole("button", { name: `审阅草稿 #${promptId}`, exact: true })
    .click();
  await prompts
    .getByRole("button", { name: "采纳Prompt草稿", exact: true })
    .click();
  await expect(page.getByLabel("生图Prompt", { exact: true })).not.toHaveValue(
    "",
  );
  await page
    .getByRole("checkbox", { name: "参考 m5-reference.png", exact: true })
    .check();
  await page.getByLabel("参考用途 1").selectOption("character");
  const refs = page.waitForResponse(
    (r) => r.request().method() === "PUT" && r.url().endsWith("/references"),
  );
  await page.getByRole("button", { name: "保存参考图", exact: true }).click();
  expect((await refs).status()).toBe(200);
  const imageJob = await clickJob(
    page,
    () => page.getByRole("button", { name: "生成首帧", exact: true }).click(),
    "/image-jobs",
  );
  const image = await waitDone(request, imageJob);
  await page
    .getByLabel("首帧版本", { exact: true })
    .selectOption(String(image.output_asset_id));
  const firstSelection = page.waitForResponse(
    (r) => r.request().method() === "PUT" && r.url().endsWith("/selection"),
  );
  await page.getByRole("button", { name: "采用首帧", exact: true }).click();
  expect((await firstSelection).status()).toBe(200);
  const videoJob = await clickJob(
    page,
    () =>
      page.getByRole("button", { name: "生成视频与声音", exact: true }).click(),
    "/video-jobs",
  );
  const video = await waitDone(request, videoJob);
  await page
    .getByLabel("视频版本", { exact: true })
    .selectOption(String(video.output_asset_id));
  const videoSelection = page.waitForResponse(
    (r) => r.request().method() === "PUT" && r.url().endsWith("/selection"),
  );
  await page.getByRole("button", { name: "采用视频", exact: true }).click();
  expect((await videoSelection).status()).toBe(200);
  const media = page.locator("video").first();
  await media.evaluate(async (element: HTMLVideoElement) => {
    element.muted = true;
    await element.play();
  });
  await expect
    .poll(() => media.evaluate((element: HTMLVideoElement) => element.ended))
    .toBe(true);
  await page.getByRole("link", { name: "时间线与导出", exact: true }).click();
  await page.getByLabel("成片比例").selectOption("square");
  const exportJob = await clickJob(
    page,
    () => page.getByRole("button", { name: "导出 MP4", exact: true }).click(),
    "/export-jobs",
  );
  const film = await waitDone(request, exportJob);
  const history = page.getByRole("region", { name: "成片历史", exact: true });
  const downloadPromise = page.waitForEvent("download");
  await history.getByRole("link", { name: "下载 MP4", exact: true }).click();
  const download = await downloadPromise;
  const videoPath = await download.path();
  expect(videoPath).toBeTruthy();
  const assets = await api<Asset[]>(
    request,
    "GET",
    `/episodes/${work.episode.id}/exports`,
  );
  expect(assets[0]?.id).toBe(film.output_asset_id);
  expect(assets[0]?.has_audio).toBe(true);
  expect(sha(await readFile(videoPath!))).toBe(assets[0]?.sha256);
  const manifestPromise = page.waitForEvent("download");
  await history
    .getByRole("link", { name: "下载来源清单", exact: true })
    .click();
  const manifestPath = await (await manifestPromise).path();
  const manifest = JSON.parse(await readFile(manifestPath!, "utf8"));
  expect(manifest.clips[0].asset_id).toBe(video.output_asset_id);
  expect(manifest.output.sha256).toBe(assets[0]?.sha256);
  await page.screenshot({
    path: testInfo.outputPath("workflow-completed.png"),
  });
});

test("双标签同时提交只接收一个任务，其余返回429", async ({
  page,
  context,
  request,
}) => {
  const work = await seed(request);
  const second = await context.newPage();
  await Promise.all([page.goto(work.url), second.goto(work.url)]);
  const a = page.getByRole("button", { name: "生成首帧", exact: true }),
    b = second.getByRole("button", { name: "生成首帧", exact: true });
  await expect(a).toBeEnabled();
  await expect(b).toBeEnabled();
  const one = page.waitForResponse(
    (r) => r.request().method() === "POST" && r.url().endsWith("/image-jobs"),
  );
  const two = second.waitForResponse(
    (r) => r.request().method() === "POST" && r.url().endsWith("/image-jobs"),
  );
  await Promise.all([a.click(), b.click()]);
  const results = await Promise.all([one, two]);
  expect(results.map((r) => r.status()).sort()).toEqual([202, 429]);
  const accepted = results.find((r) => r.status() === 202)!;
  await waitDone(request, (await accepted.json()).job_id);
  const jobs = await api<Task[]>(
    request,
    "GET",
    `/projects/${work.project.id}/jobs`,
  );
  expect(jobs.filter((j) => j.kind === "IMAGE")).toHaveLength(1);
});

test("离开再返回保留人工草稿和全局任务，轮询不自动采用", async ({
  page,
  request,
}) => {
  const work = await seed(request);
  await page.goto(work.url);
  const job = await clickJob(
    page,
    () => page.getByRole("button", { name: "生成首帧", exact: true }).click(),
    "/image-jobs",
  );
  await page
    .getByLabel("画面描述", { exact: true })
    .fill("未保存的人工画面，离开后保留");
  await page.getByRole("link", { name: "资产库 →", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "故事的视觉素材" }),
  ).toBeVisible();
  await expect(
    page.getByRole("region", { name: "全局制作任务" }),
  ).toContainText(`#${job}`);
  await page.goBack();
  await expect(page.getByLabel("画面描述", { exact: true })).toHaveValue(
    "未保存的人工画面，离开后保留",
  );
  await waitDone(request, job);
  await page
    .getByRole("button", { name: "刷新素材与分镜", exact: true })
    .click();
  await expect(page.getByLabel("画面描述", { exact: true })).toHaveValue(
    "未保存的人工画面，离开后保留",
  );
  const shot = await api<Shot>(request, "GET", `/shots/${work.shot!.id}`);
  expect(shot.selection.first_frame_asset_id).toBeNull();
});

test("响应丢失和403后不自动重发，显式重试始终复用同一意图", async ({
  page,
  request,
}) => {
  const work = await seed(request);
  const bodies: string[] = [];
  let jobId = 0;
  await page.route("**/api/v1/shots/*/image-jobs", async (route) => {
    bodies.push(route.request().postData()!);
    if (bodies.length === 2) {
      await route.fulfill({
        status: 403,
        json: { error: { code: "SESSION_EXPIRED", message: "会话已变化" } },
      });
      return;
    }
    const result = await route.fetch({ maxRetries: 0 });
    if (bodies.length === 1) {
      jobId = (await result.json()).job_id;
      await route.abort("connectionreset");
    } else await route.fulfill({ response: result });
  });
  await page.goto(work.url);
  await page.getByRole("button", { name: "生成首帧", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "查询或重试原提交", exact: true }),
  ).toBeVisible();
  await expect.poll(() => bodies.length).toBe(1);
  await expect(
    page.getByRole("button", { name: "查询或重试原提交", exact: true }),
  ).toBeEnabled();
  await api<Shot>(request, "PATCH", `/shots/${work.shot!.id}`, {
    expected_version: work.shot!.version,
    raw_description: "提交后改变正式输入",
  });
  await page.reload();
  await expect(
    page.getByRole("button", { name: "查询或重试原提交", exact: true }),
  ).toBeVisible();
  expect(bodies).toHaveLength(1);
  await page
    .getByRole("button", { name: "查询或重试原提交", exact: true })
    .click();
  await expect(
    page.getByRole("alert").filter({ hasText: "会话已变化" }),
  ).toBeVisible();
  await page
    .getByRole("button", { name: "查询或重试原提交", exact: true })
    .click();
  await expect.poll(() => bodies.length).toBe(3);
  expect(new Set(bodies).size).toBe(1);
  await waitDone(request, jobId);
  const jobs = await api<Task[]>(
    request,
    "GET",
    `/projects/${work.project.id}/jobs`,
  );
  expect(jobs.filter((j) => j.kind === "IMAGE")).toHaveLength(1);
});

test("切换项目后延迟旧响应不能污染当前表单", async ({ page, request }) => {
  const first = await seed(request),
    second = await seed(request);
  let release: (() => void) | undefined,
    arrived = false;
  const gate = new Promise<void>((resolve) => {
    release = resolve;
  });
  await page.route(`**/api/v1/projects/${first.project.id}`, async (route) => {
    const result = await route.fetch();
    arrived = true;
    await gate;
    // 导航可能已经Abort旧读取；该场景的旧响应无需再交付。
    await route.fulfill({ response: result }).catch(() => {});
  });
  await page.goto(`/projects/${first.project.id}`);
  await expect.poll(() => arrived).toBe(true);
  await page.getByRole("link", { name: "我的项目", exact: true }).click();
  await page
    .locator(`a[href="/projects/${second.project.id}"]`)
    .first()
    .click();
  await expect(page.getByLabel("项目名称", { exact: true })).toHaveValue(
    second.project.name,
  );
  release?.();
  await expect(
    page.getByRole("heading", { name: second.project.name, exact: true }),
  ).toBeVisible();
  await expect(page.getByLabel("项目名称", { exact: true })).toHaveValue(
    second.project.name,
  );
});
