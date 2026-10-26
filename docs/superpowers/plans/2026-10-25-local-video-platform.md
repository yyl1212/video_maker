# 本地 AI 短视频制作平台分步开发计划

> 面向实施代理：执行时使用 `superpowers:executing-plans`；仅在用户明确选择代理实施方式时使用 `superpowers:subagent-driven-development`。按复选步骤逐项实施，不跨越阶段确认。本文件不包含业务实现代码。

**目标：** 从空数据构建 Apple Silicon Mac 上完全本地、全局单任务、支持 H3 原生音画与整集 MP4 导出的制作工作台。

**架构：** Next.js 同源转发至 FastAPI；SQLite 保存业务与任务状态，磁盘保存不可变媒体。BackgroundTasks 负责制作执行，条件唯一约束负责全局占位，输入快照及启动恢复负责一致性。

**技术栈：** Next.js App Router、TypeScript strict、Tailwind CSS、Zustand；FastAPI、SQLAlchemy 2.x、SQLite、Alembic；独立 LLM/Draw Things/MiniMax-H3 客户端及 FFmpeg。

**设计依据：** [完整设计](../specs/2026-10-25-local-video-platform-design.md)。M1、M3、M2、M5、M6、M7、M4已验收，M8（任务12）已获开工授权，当前证据见[M8验收记录](../../验收记录.md)。

**交付编排：** [敏捷进度与看板](2026-10-25-agile-delivery.md)。M1～M8作为模块标识保留，实际按 M1→M3→M2→M5→M6→M7→M4→M8 交付；优先用手工分镜贯通音画制作，再交付AI辅助。模块及任务内的验收条件保持不变。

## 全局约束

- 新平台空数据起步；不迁移旧 DrawThings 数据，只复用已验证协议和测试思路。
- 全局同时最多一个耗时任务；PENDING/RUNNING 均占位；忙时 429；不排队、不自动重试。
- 必须使用 FastAPI BackgroundTasks；不引入 Celery、Redis、消息队列或浏览器 SSE/WebSocket。
- H3 原生视频和音频同时保存；生成视频默认必须有有效音轨；上传的无声视频导出时补静音。
- 不做内存检测、内存容量估算或内存门禁；不自动卸载/恢复其他模型。
- Python 3.13、Node.js 24 为初始运行目标；Draw Things SDK 初始固定 0.4.0；其他兼容稳定版本在 M1 记录并锁定。
- Python PEP8、TypeScript strict、关键逻辑中文注释；本地目录保存媒体，数据库只保存绝对路径和元数据。
- 所有模型调用只允许本机回环服务；无外部 CDN、远程字体、云调用或云端回退。
- 所有测试命令总超时 540 秒；真实模型每次只测一个任务，540 秒内结束。
- 每阶段完成代码审查、针对性回归和可演示验收后，等待用户确认，再进入下一阶段。
- 当前默认分支为 main。每阶段开发先同步实际默认分支，再建立或更新 codex/ 分支；最终通过 Git 提交并创建 MR/PR。
- 任何协议或兼容性变化先审查；不在代码阶段默默更改本设计的状态、字段或业务边界。

## 阶段概览

| 阶段 | 交付内容 | 用户可检查的结果 | 依赖 |
|---|---|---|---|
| M1 | 基础运行、完整新库模型、项目与剧集 | 空库启动，创建项目和剧集，保存风格/角色/剧本 | 本设计获确认 |
| M2 | 单任务引擎、恢复、轮询任务条 | 模拟任务证明只接一个、刷新可跟踪、异常不重发 | M1 |
| M3 | 资产库、分镜编辑与依赖选择 | 上传媒体、排序镜头、绑定参考、采用历史版本 | M1；先实现非生成能力，不要求任务执行器已接通 |
| M4 | 本地 LLM 解析和三种 Prompt 扩写 | 草稿生成、人工编辑、版本检查后采纳 | M2、M3；实际安排在成片链路之后 |
| M5 | Draw Things 首帧制作 | 参考图实际入请求，生成历史并采用首帧 | M2、M3；Prompt可手填，不依赖M4 |
| M6 | MiniMax-H3 视频与声音制作 | 首帧 Base64 生音画 MP4，播放与重生成 | M5 |
| M7 | 成片导出 | 顺序拼接选定视频并保留声音，下载最终 MP4 | M6 |
| M8 | 整体验收和交付 | 本机启动说明、完整回归、真实模型闭环、MR/PR | M1～M7 |

各阶段以纵向可验收模块交付；不在 M1 一次创建全部业务文件，不把未来模型功能用生产环境假实现填满。

## 文件分工与接口总约定

目录和职责以设计第 9 节为准，下列任务只创建其明确列出的文件。路径均相对 video_maker。

跨模块类型在 `backend/app/schemas/` 定义，数据库模型不作为 API 响应直接返回：

- `ProjectRead/EpisodeRead/ShotRead/AssetRead/TaskRead/RuntimeRead`：对应设计字段的响应模型；ShotRead 包含聚合参考、选择版本和派生过期标记。
- `TaskSubmitCommand`：kind、项目/剧集/可空镜头、已查看版本、idempotency_key、生成覆盖项或导出列表。浏览器不传任意路径，不传最终首帧 Base64。
- `TaskSubmission`：task: TaskRead、created: bool。仅 created=true 时注册 BackgroundTasks。
- `TaskSnapshot`：不可变 JSON 输入、编译器版本、input_hash、冻结输入所需的资产 ID/绝对路径/哈希列表。
- `TaskOutcome`：可选 PreparedAsset 与文本 result_json；不直接提交数据库。
- `PreparedAsset`：已核验、已原子发布的文件与 Asset 字段，source_task_id 固定为当前任务。
- `TaskContext`：task_id、snapshot、deadline；提供异步 `report(stage, progress=None)`、`mark_remote_started(backend)`、`mark_remote_finished()` 及受限任务目录。
- `TaskHandler`：同步 `prepare_snapshot(session, command) -> TaskSnapshot`；异步 `execute(context) -> TaskOutcome`。前者在提交短事务内校验 DB，后者不得复用该 Session。
- `TaskService.submit(command: TaskSubmitCommand) -> TaskSubmission`；`TaskRunner.run(task_id: int) -> None`；`TaskService.get(task_id: int) -> TaskRead`。
- 客户端请求/返回用 Pydantic 模型；协议错误统一为 `BackendError(code, message, execution_uncertain)`。不同客户端不引用旧 WorkflowManager。
- 前端从本地 OpenAPI 生成 api-types.ts，业务表单类型引用生成类型；Zustand 不保存服务端执行锁或完整媒体内容。

`Task.execution_json` 持久化远程请求是否发出/结束及本地子进程身份信息；此为运行记录，不修改 input_snapshot。Schema 名称、方法签名和错误码变更需同步本计划及调用者。

## 优先审查的五类情况

1. 两个标签页、不同接口同时生成：只能创建一个活跃 Task，重发同一幂等请求不能再次调用模型。对应任务 3。
2. 模型已接收请求但应用断连/重启：Task 可失败，执行槽不能在上游仍可能运行时自动开放。对应任务 4、10。
3. 生成期间换项目、改 Prompt、换图或编辑草稿：旧响应不得污染当前编辑，结果保留但不可被误认为当前版本。对应任务 5、7、8、9。
4. 文件发布与数据库提交之间崩溃：不能出现成功资产指向半文件，也不能覆盖旧版本或误登记孤立文件。对应任务 6、9。
5. 有声和无声、不同规格片段混排：导出声音不能丢失、时长不能在拼接点漂移。对应任务 10、11。

---

## M1：基础运行与项目剧集

### 任务 1：运行配置、数据库模型和项目剧集 API

**M1审查补充：** `backend/app/local_http.py`及对应转发Host回归，确保后端能校验代理携带的原始地址。

**新增文件：** `backend/pyproject.toml`、`backend/uv.lock`、`backend/alembic.ini`、`backend/migrations/env.py`、`backend/migrations/versions/0001_initial.py`；`backend/app/{__init__,main,config,db,errors}.py`；`backend/app/models/{__init__,project,episode,shot,asset,task,runtime,settings}.py`；`backend/app/schemas/{common,projects,episodes,tasks,clients}.py`；`backend/app/api/{bootstrap,projects,episodes,settings}.py`；`backend/app/services/projects.py`；`backend/tests/{conftest,test_schema,test_projects}.py`；`.gitignore`、`.env.example`、`scripts/run-check.py`。

**接口：** `create_app(settings: AppSettings) -> FastAPI`；`session_scope() -> Session` 上下文；`ProjectService.create_project(data: ProjectCreate) -> ProjectRead`；`create_episode(project_id: int, data: EpisodeCreate) -> EpisodeRead`。提供设计中的 bootstrap/health、项目、剧集、设置读写 API；连接测试先不开放。

- [x] 核对依赖支持 Python 3.13/Node.js 24，确定并锁定版本；记录已验证 SDK 和 FFmpeg 可执行路径，不安装未使用框架。
- [x] 先写失败测试：`test_schema_has_no_fk_cycle`；`test_foreign_keys_enabled_on_every_connection`；`test_project_episode_scope_and_optimistic_version`；`test_empty_database_starts_without_old_project_files`；`test_checks_timeout_terminates_process_group`。断言新库可建可升、跨项目拒绝、过期版本409、有界测试能终止整个测试进程组。
- [x] 运行对应测试确认失败原因，然后只实现配置、完整初始表结构、迁移、项目/剧集服务、最小 API 和 540 秒测试包装器。不得引用旧项目目录作为运行依赖。
- [x] 执行 `python scripts/run-check.py --cwd backend -- uv run pytest tests/test_schema.py tests/test_projects.py -q`。预期全通过；使用两次全新临时库运行迁移，确认 foreign_keys=ON、无外键环及活动唯一约束存在。
- [x] 代码审查并提交 `feat: add local project and episode foundation`。

### 任务 2：前端基础与项目剧集页面

**M1审查补充：** `frontend/src/proxy.ts`、`frontend/tests/local-host.test.ts`和`scripts/check-proxy-boundary.py`，覆盖真实Next→FastAPI入口；初始列表加载期间禁止创建，避免旧快照覆盖新项目。

**新增文件：** `frontend/package.json`、`package-lock.json`、`tsconfig.json`、`next.config.ts`、`src/app/{layout,page,globals.css}`、`src/app/projects/[projectId]/page.tsx`、`src/app/projects/[projectId]/episodes/[episodeId]/page.tsx`；`src/features/projects/{ProjectList,ProjectForm,EpisodeForm}.tsx`；`src/lib/{api,api-types}.ts`；`src/stores/workbench.ts`；`frontend/tests/projects.test.tsx`；`scripts/export-openapi.py`。

**消费：** 任务 1 API 与本地导出的 OpenAPI。

**产出：** `apiRequest<T>(path, options): Promise<T>`，统一令牌、错误和 AbortSignal；项目/剧集导航、版本保存、未保存编辑提示。所有 API 通过 Next.js rewrites 同源访问。

- [x] 先写 `projects.test.tsx`：创建项目后可新增剧集；切项目不串表单；409 保留编辑；前端不依赖远程字体/CDN。
- [x] 运行组件测试确认失败，再实现页面、中文字段和版本控制；初期首页明确显示模型功能尚未接入，不添加假生成按钮。
- [x] 从 OpenAPI 生成 api-types.ts，检查字段与后端同源；执行 `python scripts/run-check.py --cwd frontend -- npm run test -- projects`。
- [x] 执行 `python scripts/run-check.py --cwd frontend -- npm run typecheck` 和 `python scripts/run-check.py --cwd frontend -- npm run build`，预期均成功。
- [x] 审查并提交 `feat: add project and episode workspace`，演示空库创建项目/角色/剧集/剧本，等待 M1 确认。

## M2：单任务引擎、恢复与轮询

### 任务 3：事务占位、幂等和后台执行

**新增文件：** `backend/app/services/{tasks,runner}.py`、`backend/app/schemas/tasks.py` 完善、`backend/app/api/jobs.py`、`backend/tests/{test_task_submission,test_task_runner}.py`。**修改：** `app/main.py` 注册任务查询路由；`app/db.py` 事务控制。

**消费：** Task/RuntimeState 模型、独立 session_scope、错误类型。

**产出：** 总接口约定中的 TaskService、TaskRunner、TaskHandler、TaskContext。任务读取 API、BackgroundTasks 注册帮助函数 `register_submission(background_tasks, submission) -> None`。生产接口不开放任意 kind 的通用伪任务入口；模拟处理器只在测试注入。

- [x] 先写失败测试：`test_two_connections_accept_only_one_task`、`test_cross_endpoint_kinds_share_slot`、`test_same_key_reuses_job_without_second_dispatch`、`test_same_key_different_body_conflicts`、`test_response_returns_before_model_finishes`、`test_exception_sets_failed_and_releases_known_idle_slot`。并发测试使用两个真正独立连接和提交屏障，不能只串行调用两次。
- [x] 跑失败测试；实现 BEGIN IMMEDIATE 短事务、活跃条件唯一索引、request_hash、CAS 启动、实际后台响应后执行、独立Session和5秒心跳。数据库忙返回503，忙任务返回429。
- [x] 实现 TaskOutcome 统一发布事务；处理器只能返回准备好的结果，不能自行更改当前 Shot 选择。
- [x] 执行 `python scripts/run-check.py --cwd backend -- uv run pytest tests/test_task_submission.py tests/test_task_runner.py -q`。真实 TCP HTTP 测试必须证明生成未完成时已取得202及job_id，不能只依赖会等待BackgroundTasks的内存测试客户端。
- [x] 审查并提交 `feat: add atomic single task execution`。

### 任务 4：启动恢复、执行未知状态和截止

**新增文件：** `backend/app/services/recovery.py`、`backend/app/api/runtime.py`、`backend/tests/{test_task_recovery,test_task_deadline}.py`。**修改：** `app/main.py` lifespan、`services/runner.py`。

**消费：** TaskService、Task.execution_json、RuntimeState 和 TaskContext。

**产出：** `RecoveryService.startup() -> None`、`inspect_pending() -> None`、`confirm_reset(expected_version: int, backend: str) -> RuntimeRead`；数据根目录实例锁、异常巡检、总截止。

- [x] 先写 `test_second_backend_instance_cannot_open_workspace`；`test_restart_fails_pending_without_replay`；`test_restart_after_remote_dispatch_requires_reset`；`test_local_ffmpeg_identity_not_pid_only`；`test_deadline_does_not_reset_on_progress`；`test_stale_heartbeat_does_not_release_live_runner`。
- [x] 跑失败测试；实现实例UUID与锁、持久化执行标记、30秒PENDING CAS失败、RUNNING心跳提示及NEEDS_RESET。模型连通不能自动清除执行未知状态。
- [x] 按设计实现480/570/540/540秒绝对截止；测试用可注入的短预算，不能真的等待数分钟。FFmpeg子进程组身份必须可核验，无法证明停止则阻止新任务。
- [x] 执行 `python scripts/run-check.py --cwd backend -- uv run pytest tests/test_task_recovery.py tests/test_task_deadline.py -q`，预期全通过，无孤立子进程。
- [x] 审查并提交 `feat: recover interrupted local tasks safely`。

### 任务 5：前端任务条与作用域安全轮询

**新增文件：** `frontend/src/features/jobs/{TaskBar,useTaskPolling}.tsx`、`frontend/tests/jobs.test.tsx`。**修改：** `src/app/layout.tsx`、`src/lib/api-types.ts`。

**接口：** `useTaskPolling(jobId: number | null)` 返回 TaskRead、连接状态和明确刷新操作；TaskBar 读取 /jobs/active 及 runtime_state，不在前端模拟锁。

- [x] 先写测试：2秒轮询、终态停止、隐藏页5秒、刷新恢复job_id、网络失败不重新POST、切作用域的晚响应被丢弃、NEEDS_RESET显示恢复原因。
- [x] 跑失败测试后实现；计时器、AbortController和作用域标识均在卸载时清理，轮询不覆盖本地表单。
- [x] 执行 `python scripts/run-check.py --cwd frontend -- npm run test -- jobs` 和 typecheck。
- [x] 以模拟API和独立后端测试展示全局任务行为，不为演示加入生产环境假任务接口。
- [x] 审查并提交 `feat: show persistent local task progress`，等待 M2 确认。

## M3：资产和分镜工作台

### 任务 6：受管文件、资产登记、上传与媒体访问

**新增文件：** `backend/app/services/{storage,assets}.py`、`backend/app/media/ffmpeg.py`（仅probe和进程基础）、`backend/app/schemas/assets.py`、`backend/app/api/assets.py`、`backend/tests/{test_storage,test_assets_http}.py`；`frontend/src/features/assets/{AssetLibrary,AssetUpload,MediaPreview}.tsx`、`frontend/src/app/projects/[projectId]/assets/page.tsx`、`frontend/tests/assets.test.tsx`。

**接口：** `StorageService.freeze_inputs(task_id, assets) -> list[FrozenAsset]`、`publish_file(temp_path, project_id) -> PublishedFile`；`AssetService.upload(project_id, upload) -> AssetRead`；`FFmpegService.probe(path: Path) -> MediaInfo`。

- [x] 先写测试：`test_path_escape_and_symlink_rejected`、`test_upload_limits_and_real_media_type`、`test_atomic_publish_does_not_register_partial_file`、`test_orphan_is_quarantined_without_becoming_done`、`test_cross_project_content_and_range_access`；自制图片/声音/MP4，不使用真实作品。
- [x] 跑失败测试；实现绝对路径白名单、图片规范化、哈希、ffprobe、缩略图、Range及下载、软删除引用检查。AssetService.upload 遇DB失败不留成功记录。
- [x] 实现资产筛选、上传、预览和错误提示；用户选择文件只复制入本平台目录，不引用外部易变文件。
- [x] 执行 `python scripts/run-check.py --cwd backend -- uv run pytest tests/test_storage.py tests/test_assets_http.py -q`；前端执行 assets 组件测试及typecheck。
- [x] 审查并提交 `feat: add managed local asset library`。

### 任务 7：分镜、参考绑定、采用与过期判定

**新增文件：** `backend/app/services/shots.py`、`backend/app/prompts/compiler.py`、`backend/app/schemas/shots.py`、`backend/app/api/shots.py`、`backend/tests/{test_shots,test_selection,test_fingerprints}.py`；`frontend/src/features/shots/{ShotList,ShotEditor,ReferencePicker,SelectionPanel}.tsx`、`frontend/tests/shots.test.tsx`。**修改：** 剧集页面接入工作台。

**接口：** `ShotService.replace_references(shot_id, refs, expected_version) -> ShotRead`；`select_assets(shot_id, selection, shot_version, selection_version) -> ShotRead`；`reorder(episode_id, shot_ids, expected_version) -> list[ShotRead]`；纯函数 `compile_image(snapshot) -> CompiledPrompt`、`compile_video(snapshot) -> CompiledPrompt` 和输入哈希函数。

- [x] 先写测试：同项目图片引用、顺序固定、重复和缺失拒绝、重排无唯一冲突、24镜上限、无参考仍可READY；采用检查类型/项目/镜头来源/双版本。
- [x] 增加指纹测试：风格/实际参考/首帧变化会使相关结果过期；标题、重排及未使用资产变化不误伤；上传采用绑定当前输入，旧生成结果不可绕过当前指纹采纳。
- [x] 跑失败测试后实现；ShotRead聚合Selection和references，不重复持久化reference_asset_ids；UI保留未保存修改并明确展示“旧版本”。
- [x] 执行 `python scripts/run-check.py --cwd backend -- uv run pytest tests/test_shots.py tests/test_selection.py tests/test_fingerprints.py -q`；前端执行 shots 测试及typecheck。
- [x] 审查并提交 `feat: add shot references and explicit asset selection`，等待 M3 确认。

## M4：本地 LLM 解析和 Prompt 扩写

### 任务 8：结构化客户端、文本处理器及草稿采纳

**新增文件：** `backend/app/clients/llm.py`、`backend/app/services/storyboards.py`、`backend/app/schemas/storyboards.py`、`backend/tests/{test_llm_client,test_storyboard_jobs,test_storyboard_apply}.py`；`frontend/src/features/storyboards/{ScriptParser,PromptAssistant,DraftReview}.tsx`、`frontend/tests/storyboards.test.tsx`。**修改：** jobs创建路由、settings连接测试、runner处理器注册及相关页面。

**接口：** 异步 `LLMClient.parse_script(request: ParseRequest) -> StoryboardDraft`、`optimize_prompts(request: PromptRequest) -> PromptDraft`；`StoryboardService.apply(task_id, edited_value, expected_version, replace_confirm) -> EpisodeRead | ShotRead`。

- [x] 先写协议测试：本机地址、超时/HTTP错误、重复JSON字段、截断、非法ID、1～24镜、12000字边界、已验证Qwen整体reasoning_content兼容，其他模型不沿用此兼容。所有HTTP用本地fixture。
- [x] 写业务测试：生成不直接写正式分镜；英文动作与中文台词原文分离；重复/过期采纳409；替换需要明确确认且保留旧镜头资产；轮询不覆盖人工改稿。
- [x] 跑失败测试；实现JSON Schema和业务校验、PARSE_SCRIPT/OPTIMIZE_PROMPTS handler、文本草稿编辑采纳UI。来源变化时保留结果并显示原因，不自动重发。
- [x] 执行 `python scripts/run-check.py --cwd backend -- uv run pytest tests/test_llm_client.py tests/test_storyboard_jobs.py tests/test_storyboard_apply.py -q`；前端执行 storyboards 测试与typecheck。另由用户运行本机模型完成1次小剧本解析验收，整次≤540秒。
- [x] 审查协议兼容点，提交 `feat: add local script and prompt assistance`，等待 M4 确认。

实施证据：[M4剧本与Prompt辅助验收](../../M4剧本与Prompt辅助验收.md)。协议、草稿采纳、真实Qwen解析和单镜扩写、浏览器人工修订、独立审查修正均完成并获用户验收。用户已授权M8任务12。

## M5：Draw Things 首帧

### 任务 9：参考驱动生图、版本保存与采用

**新增文件：** `backend/app/clients/drawthings.py`、`backend/app/services/generation.py`（IMAGE处理器）、`backend/tests/{test_drawthings_backend,test_image_jobs}.py`；`frontend/src/features/shots/ImageGenerationPanel.tsx`、`frontend/tests/image-generation.test.tsx`。**修改：** runner注册、创建路由和settings连接测试。

**接口：** 异步 `DrawThingsBackend.generate(request: ImageRequest, on_progress) -> ImageResult`；ImageRequest含最终Prompt、负面要求、冻结参考路径、实际配置和截止；ImageResult为待发布PNG及元数据。

- [x] 先写SDK调用测试：多个参考按position进入moodboard；显式false/0保留；无结果/gRPC超时/文件写入失败生成FAILED；上游是否停止未知时NEEDS_RESET。
- [x] 写发布故障测试：原图不覆写，生成后修改当前分镜仅产生过期历史；文件已rename但DB提交失败，重启隔离孤立结果；当前首帧选择不被自动更改。
- [x] 跑失败测试后抽取旧SDK协议，适配独立BackendError和异步截止；禁止导入旧drama全局目录或WorkflowManager。
- [x] 执行 `python scripts/run-check.py --cwd backend -- uv run pytest tests/test_drawthings_backend.py tests/test_image_jobs.py -q`；前端执行image-generation测试与typecheck。真实验收用自制参考完成一张小尺寸图并检查已记录的实际参考列表，整次≤540秒。
- [x] 审查SDK兼容修正与引用顺序，提交 `feat: generate reference driven first frames`，等待 M5 确认。


实施证据：[M5首帧生成验收](../../M5首帧生成验收.md)。已完成开发、独立审查修正、回归与真实模型演示并获用户验收；任务10已由用户确认开工。

## M6：MiniMax-H3 原生视频和声音

### 任务 10：首帧 Base64、音画协议与 MP4

**新增文件：** `backend/app/clients/minimax_h3.py`、`backend/app/media/video_profile.py`、`backend/tests/{test_minimax_protocol,test_av_encoding,test_video_jobs}.py`；`frontend/src/features/shots/VideoGenerationPanel.tsx`、`frontend/tests/video-generation.test.tsx`。**修改：** generation.py加入VIDEO处理器；ffmpeg.py加入encode_av；runner、路由和连接检查。

**接口：** 异步 `MiniMaxH3Backend.generate(request: VideoRequest, on_progress) -> RawAVResult`；`FFmpegService.encode_av(result: RawAVResult, target: Path, deadline) -> MediaInfo`；`video_profile(width, height, duration, preset) -> VideoProfile`。客户端内部从冻结首帧生成first_frame_image，不让浏览器提交Base64。

- [x] 先写协议测试：首帧字段解码后与选中资产SHA一致；首帧无效/过期拒绝；540秒总截止不被进度重置；HTTP/SSE中断、非法Base64、RGB字节数/格式不符均明确失败。
- [x] 写音频测试：PCM s16le、1/2声道和8000～96000 Hz边界；音频缺失失败；差异>0.5秒失败；合法短音频补齐、长音频裁切后封装AAC。自制画面配正弦波，ffprobe确认H.264/AAC、帧率、时长和双流存在。
- [x] 跑失败测试；抽取旧H3音画协议并改为异步HTTP和子进程。不得复制 `_prepare` 的内存估算、指标门禁、模型卸载/恢复；不新增任何可用内存检查。
- [x] 执行 `python scripts/run-check.py --cwd backend -- uv run pytest tests/test_minimax_protocol.py tests/test_av_encoding.py tests/test_video_jobs.py -q`；前端测试和typecheck。真实验收为一镜短视频，核对声音要求、实际首帧和音轨，≤540秒；声音自然度及台词准确度由人工播放确认。
- [x] 审查旧协议兼容和原生音频完整性，提交 `feat: generate H3 video clips with native audio`，等待 M6 确认。


实施证据：[M6音画生成验收](../../M6音画生成验收.md)。开发、独立审查修正、回归与真实模型/浏览器演示已完成并获用户验收，现按用户指令完成M7任务11。

## M7：成片导出

### 任务 11：冻结时间线和音画拼接

**新增文件：** `backend/app/services/exports.py`、`backend/app/schemas/exports.py`、`backend/tests/{test_export_snapshot,test_export_av}.py`；`frontend/src/features/exports/{ExportTimeline,ExportHistory}.tsx`、`frontend/src/app/projects/[projectId]/episodes/[episodeId]/export/page.tsx`、`frontend/tests/exports.test.tsx`。**修改：** ffmpeg.py加入normalize_clip/concat；runner注册EXPORT、创建路由。

**接口：** `ExportHandler.prepare_snapshot(session, command) -> TaskSnapshot`；异步 `FFmpegService.normalize_clip(input, output, profile, deadline) -> MediaInfo`、`concat(clips, target, profile, deadline) -> MediaInfo`；EXPORT输出final_video Asset及manifest.json。

- [x] 先写快照测试：提交后改镜头顺序或选择不改变清单；缺片段拒绝；使用过期片段需逐项绑定确认；跨项目和哈希变动拒绝；多次导出路径独立。
- [x] 写音画回归：混合竖/横、有声/无声、不同帧率和采样率三段自制片段；统一尺寸补边、不拉伸；24fps、AAC48k双声道；上传片段量化到最近24fps整帧并记录原时长，单段变化≤一帧；时间戳从0开始，音轨按段对齐，输出时长与规范化片段之和误差≤0.15秒；解码检查有声段非静音、无声段静音。
- [x] 跑失败测试后实现；采用逐段规范化后拼接，不假设可以concat copy；FFmpeg参数以数组传递，禁止shell拼接用户文本。
- [x] 执行 `python scripts/run-check.py --cwd backend -- uv run pytest tests/test_export_snapshot.py tests/test_export_av.py -q`；前端exports测试、typecheck和build。小样本用真实ffmpeg，整体≤540秒。
- [x] 审查音画连续性，提交 `feat: export ordered clips with audio`，演示下载独立成片和来源清单，等待 M7 确认。

实施证据：[M7成片导出验收](../../M7成片导出验收.md)。已完成开发、独立审查修正、回归及三段原创音画导出与下载并获用户验收，用户已授权进入任务8（M4）。

## M8：整体验收与交付

### 任务 12：闭环回归、启动入口、中文说明与 MR/PR

**新增文件：** `backend/tests/test_workflow_e2e.py`、`frontend/tests/e2e/workflow.spec.ts`、`scripts/{start-local.sh,export-openapi.py}` 完善、`docs/验收记录.md`、`docs/本机运行与备份.md`。**修改：** README、.env.example、受测缺陷相关文件。只修已确认问题，不扩展新功能。

**消费：** 前11项完整接口。**产出：** 可重复启动、可回归验证、可审查的首版交付。

- [x] 编写闭环回归：空库→项目/角色→剧本解析草稿→采纳→绑定自制参考→生成首帧→采用→H3音画→采用→导出。自动闭环使用模型fixture和真实FFmpeg，验证没有云端请求。
- [x] 编写浏览器测试：双标签并发429、切页面保留编辑和任务、网络重试不重复POST、旧响应不串项目、音画可预览下载；使用Playwright真实浏览器，不把DOM桩测试当浏览器验收。
- [x] 提供先决条件检查和本机启动脚本，仅检查Python/Node/FFmpeg/端口及配置，不检测内存、不自动管理模型。README说明生产构建不开热重载、独立本机模型服务由用户启动。
- [x] 分组运行后端pytest、前端组件与浏览器测试、typecheck和build；每个命令均经540秒包装。真实模型闭环拆成独立请求逐项验收，不在一个>10分钟命令里串行跑完整流程。
- [x] 完成全分支代码审查、中文验收记录和备份说明；从最新默认分支处理必要冲突后复查受影响项，通过Git提交、推送并创建MR/PR。记录真实模型/SDK/FFmpeg版本和剩余限制，等待M8确认。

实施证据：[M8整体验收记录](../../验收记录.md)、[交付PR #9](https://github.com/yyl1212/video_maker/pull/9)。完整自动/真实浏览器回归、逐项真实模型闭环、生产启动、停机备份原路径恢复及累计审查修正均完成；任务12已完成，M8于2026-10-26获用户整体验收，并已授权合并PR #9。

## 计划自审及阶段停止条件

覆盖关系：产品项目剧集→任务1/2；单任务与恢复→任务3/4/5；资产和分镜→任务6/7；LLM→任务8；图片→任务9；H3音画→任务10；成片→任务11；交付→任务12。五类重点故障均有归属明确的测试。

以下情况暂停该阶段并更新设计，而不是继续猜测：真实H3协议与旧fixture不同、SDK无法保留必要参数、BackgroundTasks无法保持接口响应、模型断连后无法满足执行状态确认、外键或文件发布规则需要改变。一般实现细节在设计范围内自行解决。

M8任务12已获用户整体验收，本轮按用户明确指令合并PR #9，不自动发布或扩展新功能。
