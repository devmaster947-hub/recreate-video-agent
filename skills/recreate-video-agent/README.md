# 复刻爆款视频

Skill 调用名：`recreate-video-agent`

SkillHub 版本：`1.0.3`

当前工作流：`V5.1 Identity Bindings`

用于在 Codex 中拆解并复刻 TikTok、抖音等爆款带货视频。Skill 会保留原片的节奏、动作、镜头和声音结构，同时按需替换商品、达人和目标市场内容。

## 工作流

1. 检查原视频和参考图，确认商品、达人、模型、时长、国家与语言。
2. 本地分析媒体和候选切镜，通过灵智工坊 `RecreateVideoPromptV3` 获得服务端复刻规划。
3. 按每个 Segment 的9个时间锚点生成唯一 3×3 真实帧 Storyboard，再优先使用当前智能体的原生图像能力去字和替换对象；无该能力时使用灵智 `gpt-image-2` 1K 兜底。
4. 显示并确认每段视频 Prompt、Storyboard 和引用图。
5. 按 `LibTV → 小云雀 CLI → 即梦 CLI → 灵智工坊 CLI` 顺序自动选择首个可用通道生成、拼接并交付成片。

详细的授权边界、失败恢复、分段规则和素材绑定见 [SKILL.md](SKILL.md)。

## 安装

```sh
git clone https://github.com/devmaster947-hub/recreate-video-agent.git
cp -R recreate-video-agent/skills/recreate-video-agent ~/.codex/skills/
```

安装后在新的 Codex 对话中调用：

```text
$recreate-video-agent 帮我复刻这个带货视频
```

请同时提供已授权的对标视频；如需替换商品或达人，附上对应参考图。

## 运行条件

- Python 3、FFmpeg 和 FFprobe。
- 有效的灵智工坊 API Key；Skill 只在首次调用服务端拆解前执行按需鉴权预检。
- 至少一个可用的视频生成通道：LibTV、小云雀 CLI、即梦 CLI 或灵智工坊 CLI。
- 生成任务会调用外部服务，费用与素材上传范围按实际服务和用户授权执行。

Skill 内置 macOS Apple Silicon 和 Windows x64 的 LZStudio CLI `0.0.5`。技能会依次检查显式 `--cli`、`LZSTUDIO_CLI`、内置 CLI 和系统 `PATH`；它用于 `RecreateVideoPromptV3` 拆解，也在符合固定路由条件时承担图片与视频兜底。

## 目录

| 路径 | 用途 |
| --- | --- |
| `SKILL.md` | Skill 入口、流程和授权边界 |
| `agents/openai.yaml` | Codex 展示名和默认调用提示 |
| `scripts/` | 分析、分段、抽帧、绑定、预检、生成与交付脚本 |
| `references/` | 按需读取的详细规则 |
| `assets/workflows/` | 配套的服务端工作流资产 |
| `cli/` | LZStudio CLI 内置发行文件 |
| `tests/` | 本地回归测试 |

License: Apache-2.0
