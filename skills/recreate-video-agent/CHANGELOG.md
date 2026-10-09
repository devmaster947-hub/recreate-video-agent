## 1.1.5 — LuluLab 图片模型切换（2026-10-09）

- 图片生成与编辑仍使用 LuluLab CLI 的 ImageGenV2，模型改为 `gpt-image-2-5-sunburst`，分辨率保持 1K。
- 同步更新技能说明、工作流契约与提交测试。

## 1.1.4 — LuluLab共用生成协议（2026-10-08）

- 用户确认除CLI名称外契约不变；从原二进制核对ImageGenV2、VideoGenV2并内置。
- 图片image2、视频Seedance2 Mini均使用LuluLab通用任务，参考图先上传为媒体对象。
- 当前任务显式指定LuluLab时不检测或调用其他渠道。
- 继续保留taskId、重复提交门禁、恢复查询和视频预处理修复。

## 1.1.3 — 客户端安装修订（2026-10-08）

- 按用户确认，工作流由LuluLab CLI自动触发，客户端只负责上传、提交、查询及消费结果。
- 移除服务端工作流JSON、服务端JS及对应的服务端实现测试，不要求导入或部署工作流。
- 保留131项客户端回归测试。

## 1.1.2 — LuluLab CLI 迁移（2026-10-08）

- 内置客户提供的 LuluLab CLI 0.0.2：macOS arm64、Windows x64，更新安装路径与 SHA256。
- 拆解鉴权改为 user --credits；保留 task submit/fetch、upload、预处理、排他锁和恢复规则。
- 配置改为 LULULAB_CLI、LULULAB_API_KEY 与独立 .recreate-video-lululab/config.json。
- LuluLab 0.0.2 不提供 image/video；图片按文档schema接入可配置的通用task（需LULULAB_IMAGE_WORKFLOW_ID）；阻断未提供协议的视频生成入口，保留其他三个视频CLI。
- 拆解沿用V3及原输入与结果协议。

## 原版历史（以下记录来自附件，仅代表迁移前版本）

## 1.1.1 兼容修复包 — 2026-10-08

- 修复custom前N秒只写入提示、没有实际裁剪的问题，source模式保留全片及整数秒目标语义。
- 新增独立临时目录、FFmpeg同步精确裁剪、ffprobe和完整解码校验，裁剪后再按原20,000,000字节限制压缩。
- 直链执行公共IP校验、固定IP连接、安全下载、裁剪和上传，限制重定向、大小及超时。
- 保留原始sourceDuration和Planner/Webhook协议，analysisDuration仅在本地保存，过滤区间外候选切点。
- 增加真实媒体、并发和下载安全回归测试；不修改Gemini、Blueprint、生成模型、积分和替换逻辑。

## 1.1.1 — 2026-09-29

- 图片生成与编辑优先使用智能体原生能力；当前会话无该能力时，使用灵智工坊 `gpt-image-2` 1K 兜底。
- 新增可持久化 taskId、恢复查询与结果下载的灵智图片生成入口。
- 视频生成顺序扩展为 LibTV → 小云雀 → 即梦 → 灵智工坊，并保留 provider 锁定与禁止重复付费提交的规则。
- 补充图片、视频兜底的回归测试，并通过真实灵智CLI图片与5秒视频冒烟测试。

## 1.0.2 — 2026-09-18

- LibTV 逻辑模型 ID 和 modelKey 自动映射为 CLI 展示名，并在提交前查询校验。
- 未指定工作区时自动创建工作区，兼容 `projectMeta.uuid` 画布返回结构。
- Skill 标题无法提供版本时回退读取 frontmatter；SkillHub `1.x.y` 包版本按当前工作流能力处理。
- Storyboard 时间戳文件同时支持 JSON 和每行一个数字的纯文本。
- 保留当前安装版内置的 macOS ARM64 与 Windows x64 LZStudio CLI `0.0.5`。
- 增加上述修复的针对性回归测试，并更新受版本策略影响的旧测试夹具。

## 1.0.1 — 基线

人物与商品身份绑定版复刻工作流。

### 调用恢复修复
- 新任务默认 image2 / Seedance2 Mini，LuluLab 任务接口统一提交、查询与媒体解析。
- 旧 manifest 的 auto 路由也固定 LuluLab；CLI 不可用时保留素材并报错。
- 图片与视频排他锁、提交前持久记录；查询/下载错误仅恢复原 ID，终态失败不自动重提。
