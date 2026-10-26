# 本地 AI 短视频制作平台

面向 Apple Silicon Mac 的单机、单用户制作工作台。使用本机 LLM 解析剧本、Draw Things 生成首帧、MiniMax-H3 同时生成视频和声音，最后用本地 FFmpeg 拼接 MP4。

当前交付 **M8：整体验收与首版交付**。M1～M7均已获验收，完整创作链路已实现；M8回归、累计审查修正和真实模型分项演示已完成，并于2026-10-26获用户整体验收。进度与证据见[整体验收记录](docs/验收记录.md)、[交付PR #9](https://github.com/yyl1212/video_maker/pull/9)。

## 本机启动

需要Python 3.13、uv、Node.js 24、FFmpeg和ffprobe。首次在仓库根目录准备：

```bash
cp .env.example .env
(cd backend && uv sync --locked)
(cd frontend && npm ci)
./scripts/start-local.sh --check
./scripts/start-local.sh --build
```

打开 [本机工作台](http://127.0.0.1:3000)。已有生产构建时使用`./scripts/start-local.sh`；在启动终端按Ctrl+C停止本次前后端。脚本解析.env而不执行其中的shell文本，固定8000/3000端口，不检测内存，不启动或停止模型。模型服务由用户自行准备。实际制作使用生产构建，不开热重载或多worker。

完整说明：[本机运行、停止、备份与原路径恢复](docs/本机运行与备份.md)。本版数据库保存绝对路径，停机备份整个数据目录，恢复时回到原VM_DATA_DIR绝对路径。

需要分别调试服务时，在backend运行`uv run uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000 --env-file ../.env`，在frontend运行`npm run dev`。手动生产启动使用`npm run build`后`npm run start`。自定义端口时同步VM_BACKEND_URL和VM_FRONTEND_ORIGINS；Next转发地址在构建时读取，改变后须重新构建。

文本辅助前，在剧集的“文本模型连接与配置”中填写本机OpenAI兼容服务地址（默认1234）并检查连接、选择模型后保存；检查只读模型列表。已实测 `qwen3.8-27b-mlx@8bit`。模型正文须为完整严格JSON；正文为空时，仅旧已验证别名 `qwen3.8-27b-mlx` 与上述精确8bit ID允许整体解析reasoning_content，不截取思考文本，不向4bit或其他模型泛化。

保存剧本后点击“AI 拆解已保存剧本”，完成后打开草稿，编辑画面、台词、角色、声音与时长，明确采纳；有旧分镜时勾选替换确认，旧镜头和资产仍保留。新镜头初始DRAFT。为镜头点击“AI 扩写三类 Prompt”，审阅并采纳后仅更新三类Prompt，台词原文和引用保持。来源存在未保存编辑或已变化时不能采纳；解析后编辑已有镜头也会使旧解析失效。结果刷新只更新状态，保留人工审阅稿。

生图前，在Draw Things中启用本机gRPC API（默认7859）及TLS，安装已验证模型 `flux_2_klein_9b_kv_i8x.ckpt`。在分镜“Draw Things连接与默认参数”中检查连接、填入模型名并保存。首版只启用已验证的Klein 9B配置；宽高须为64的倍数，最多8张有序参考，不自动下载模型。响应压缩由Draw Things服务设置，客户端兼容两种张量格式。连接仅允许本机回环地址。

保存画面描述、生图Prompt与参考图片后，点击“生成首帧”。API立即返回job_id，顶部任务条与本镜显示实际阶段；断线仅恢复查询，不自动重放模型调用。生成成功加入历史，需在“当前采用的版本”中选择并点击“采用首帧”。重新生成不会覆盖旧文件或自动替换选择；输入变化后的旧生成结果不能作为当前版本采用。生视频前启动本机MLX-Serve（默认11234），安装 `ddalcu/MiniMax-H3-FL2VA-MLX-Serve-8bit`。在“H3连接与默认参数”检查连接；保存动作、声音Prompt及台词，采用有效首帧后点击“生成视频与声音”。quality为20步，fast为4步Turbo；镜头参数优先于默认配置。目标1～8秒，实际按17k+5帧/24fps量化；尺寸为32倍数，短边≤384、长边≤672。

H3的RGB8/PCM s16le经过校验并封装为H.264/AAC MP4；缺失音频或音画差超过0.5秒失败，合法长度差会补齐/裁切。生成成功只入历史，需播放检查并明确采用；重生保留旧文件和选择。音画任务使用540秒固定截止，断流停止未知时保持恢复限制。不读取内存指标，不自动卸载或恢复模型。

在剧集顶部进入“时间线与导出”，默认包含全部活动镜头。缺片段需补齐或取消勾选；旧片段逐项确认。输出可选竖屏720×1280、横屏1280×720或方形720×720，统一24fps和AAC48k双声道，等比补边，无声段补静音。导出后在历史播放、下载MP4与来源清单；重复导出各自保留。手动刷新来源后须明确采纳，保留重排顺序但清空旧片段确认；轮询不覆盖草稿。来源预览在后端重启后需显式刷新，已接受的原提交仍可按同一幂等键查询。

共用同一数据目录只能启动一个后端实例，即使换端口也会拒绝第二实例。发生执行未知状态时，先停止或重启任务条中对应的本机服务，再明确确认恢复；刷新或连通性检查不会自动解锁。提交响应丢失时使用“查询或重试原提交”，保留同一幂等键，避免重复生成。

进入项目的“打开资产库”导入素材，再进入剧集下方的分镜工作台。文字参数与参考图片分别保存；素材采用需确认适用于当前已保存输入。刷新保留未保存草稿，版本冲突时先核对最新内容，再明确保留草稿重试。

## 回归与接口类型

在仓库根目录执行，单个命令最多540秒：

```bash
python3 scripts/run-check.py --cwd backend -- uv run pytest -q
python3 scripts/run-check.py -- backend/.venv/bin/ruff check --config backend/pyproject.toml backend scripts
python3 scripts/run-check.py --cwd frontend -- npm test
python3 scripts/run-check.py --cwd frontend -- npm run typecheck
python3 scripts/run-check.py --cwd frontend -- npm run lint
python3 scripts/run-check.py --cwd frontend -- npm run build
(cd frontend && npx playwright install chromium)
python3 scripts/run-check.py --cwd frontend -- npm run test:e2e
```

两个服务运行时，可额外验证真实代理链的本机来源边界：

```bash
python3 scripts/run-check.py -- python3 scripts/check-proxy-boundary.py
```

媒体链路脚本会创建原创验收项目。仅对使用临时/隔离`VM_DATA_DIR`启动的服务执行：

```bash
python3 scripts/run-check.py -- backend/.venv/bin/python scripts/check-media-workspace.py
```

此脚本验证超过10MiB的真实MP4穿过Next代理后完整入库、SHA一致、音轨保留、Range读取、参考绑定、版本采用和过期保留。不会调用模型。

更新后端接口后，生成契约和前端类型，不必启动服务：

```bash
backend/.venv/bin/python scripts/export-openapi.py
npm run api:types --prefix frontend
backend/.venv/bin/python scripts/export-openapi.py --check
```

后端导入和契约生成不会创建数据库；只在服务启动时迁移和初始化。`data/`、`.env`及测试临时文件不进入Git。Playwright需8000/3000空闲，测试工厂使用独立临时空库；不会复用或停止已有服务。资产恢复到新绝对路径的工具留待后续。

## 设计与进度

- [M8 整体验收与交付记录](docs/验收记录.md)
- [本机运行与备份说明](docs/本机运行与备份.md)
- [完整产品、架构与数据模型设计](docs/superpowers/specs/2026-10-25-local-video-platform-design.md)
- [分阶段开发计划与验收标准](docs/superpowers/plans/2026-10-25-local-video-platform.md)
- [敏捷迭代、待办和当前进度](docs/superpowers/plans/2026-10-25-agile-delivery.md)
- [M1 实施与验收记录](docs/M1项目剧集验收.md)
- [M3 资产与分镜验收记录](docs/M3资产分镜验收.md)
- [M2 单任务引擎验收记录](docs/M2任务引擎验收.md)
- [M5 首帧生成验收记录](docs/M5首帧生成验收.md)
- [M6 音画生成验收记录](docs/M6音画生成验收.md)
- [M7 成片导出验收记录](docs/M7成片导出验收.md)
- [M4 剧本与Prompt辅助验收记录](docs/M4剧本与Prompt辅助验收.md)

已确认边界：从空数据开始、不迁移旧 DrawThings 数据；可复用已验证的模型接入协议；全局单任务、BackgroundTasks 后台执行、轮询进度；不依赖云服务，不做内存检测，不自动卸载或恢复其他模型。

M8已获用户整体验收，用户已授权合并交付PR #9。发布及新增需求由用户另行决定。
