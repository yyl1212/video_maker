# 本地 AI 短视频制作平台

面向 Apple Silicon Mac 的单机、单用户制作工作台。使用本机 LLM 解析剧本、Draw Things 生成首帧、MiniMax-H3 同时生成视频和声音，最后用本地 FFmpeg 拼接 MP4。

当前处于设计阶段，仓库尚无业务实现。本轮不安装依赖、不初始化数据库、不调用真实模型。

- [完整产品、架构与数据模型设计](docs/superpowers/specs/2026-10-25-local-video-platform-design.md)
- [分阶段开发计划与验收标准](docs/superpowers/plans/2026-10-25-local-video-platform.md)

已确认边界：从空数据开始、不迁移旧 DrawThings 数据；可复用已验证的模型接入协议；全局单任务、BackgroundTasks 后台执行、轮询进度；不依赖云服务，不做内存检测，不自动卸载或恢复其他模型。

每次只实施用户指定的一个阶段，代码审查和回归验收后，等待确认再继续。
