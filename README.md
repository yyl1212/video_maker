# 本地 AI 短视频制作平台

面向 Apple Silicon Mac 的单机、单用户制作工作台。使用本机 LLM 解析剧本、Draw Things 生成首帧、MiniMax-H3 同时生成视频和声音，最后用本地 FFmpeg 拼接 MP4。

当前版本为 **M1：项目与剧集基础工作台**，可创建项目、编辑全局风格与角色、维护剧集和剧本、归档与恢复。新库自动通过 Alembic 初始化，数据保存在本机。

## 本机启动

需要 Python 3.13、uv、Node.js 24。先在仓库根目录复制配置：

```bash
cp .env.example .env
```

终端一启动后端：

```bash
cd backend
uv sync --locked
uv run uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000 --env-file ../.env
```

终端二启动前端：

```bash
cd frontend
npm ci
npm run build
npm run start
```

打开 [本机工作台](http://127.0.0.1:3000)。两个终端均用 Ctrl+C 停止。开发页面可用 `npm run dev`；实际制作采用生产构建。端口如需调整，前端转发地址使用 `VM_BACKEND_URL`，后端允许的前端来源使用 `VM_FRONTEND_ORIGINS` JSON 数组配置。

M1 不需要启动模型服务。制作执行器、资产与分镜、模型接入按后续模块交付，页面仅提供当前可用操作。

## 回归与接口类型

在仓库根目录执行，单个命令最多540秒：

```bash
python3 scripts/run-check.py --cwd backend -- uv run pytest -q
python3 scripts/run-check.py --cwd backend -- uv run ruff check app tests migrations ../scripts
python3 scripts/run-check.py --cwd frontend -- npm test
python3 scripts/run-check.py --cwd frontend -- npm run typecheck
python3 scripts/run-check.py --cwd frontend -- npm run lint
python3 scripts/run-check.py --cwd frontend -- npm run build
```

两个服务运行时，可额外验证真实代理链的本机来源边界：

```bash
python3 scripts/run-check.py -- python3 scripts/check-proxy-boundary.py
```

更新后端接口后，生成契约和前端类型，不必启动服务：

```bash
backend/.venv/bin/python scripts/export-openapi.py
npm run api:types --prefix frontend
```

后端导入和契约生成不会创建数据库；只在服务启动时迁移和初始化。`data/`、`.env`及测试临时文件不进入Git；备份前停止服务，再复制完整`data/`目录。资产恢复到新绝对路径的工具留待后续。

## 设计与进度

- [完整产品、架构与数据模型设计](docs/superpowers/specs/2026-10-25-local-video-platform-design.md)
- [分阶段开发计划与验收标准](docs/superpowers/plans/2026-10-25-local-video-platform.md)
- [敏捷迭代、待办和当前进度](docs/superpowers/plans/2026-10-25-agile-delivery.md)
- [M1 实施与验收记录](docs/M1项目剧集验收.md)

已确认边界：从空数据开始、不迁移旧 DrawThings 数据；可复用已验证的模型接入协议；全局单任务、BackgroundTasks 后台执行、轮询进度；不依赖云服务，不做内存检测，不自动卸载或恢复其他模型。

每次只实施用户指定的一个阶段，代码审查和回归验收后，等待确认再继续。
