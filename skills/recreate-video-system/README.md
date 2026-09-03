# recreate-video-system

版本：**v5.13-server-core**

用于在 Codex 中复刻 TikTok、抖音等产品视频的技能。客户端从原视频提取分段真实帧故事板，按需替换产品和达人，由灵智服务端完成 Gemini 动态拆解与 GPT 多模态提示词重构，再由客户端校验、展示提示词并在用户确认后生成视频。

## 工作流程

1. 确认对标视频、产品、达人、模型、时长、目标国家和语言。
2. 按模型时长限制，为每段提取一张 4×4 真实帧故事板与 16 个时间锚点。
3. 按需替换故事板中的产品或达人，并锁定无需修改的画格。
4. 上传最终视觉资料，调用服务端核心工作流；本地校验返回的分段提示词。
5. 展示完整视频提示词，取得“确认生成”后生成、拼接并质检。

详细执行规则见 [SKILL.md](SKILL.md)。

## 安装到 Codex

克隆仓库，或通过 GitHub 下载仓库 ZIP 并解压：

```sh
git clone https://github.com/devmaster947-hub/viral-video-recreation-agent.git
```

将仓库中的 `skills/recreate-video-system` 完整目录复制到 Codex 的用户技能目录：

- 默认目录：`~/.codex/skills/recreate-video-system/`
- 若设置了 `CODEX_HOME`：`$CODEX_HOME/skills/recreate-video-system/`

确保目录内直接包含 `SKILL.md`、`agents/`、`scripts/`、`references/`、`core/`、`utils/` 和 `cli/`。替换已有同名技能前，请将旧目录完整备份到技能目录之外。

安装后在下一轮 Codex 对话中调用：

```text
$recreate-video-system 帮我复刻这个产品视频
```

同时提供对标视频；如需替换产品或达人，附上对应参考图。

## 运行条件

- Python 3；视频处理需要 FFmpeg，建议同时安装 FFprobe。
- 内置 LZStudio CLI 支持 macOS Apple Silicon 和 Windows x64。
- 有效的灵智工坊 API Key，以及可访问的服务端 `recreate-video-prompt` 工作流。
- 图片编辑和视频生成需要对应服务权限与额度；选用官方 Seedance 渠道时还需要配置 Dreamina CLI。

首次调用时，CLI 包装脚本会把内置 LZStudio CLI 安装到用户应用目录并配置用户 PATH。API Key 可通过环境变量 `RECREATE_VIDEO_API_KEY`、本机 `~/.recreate-video/config.json` 或交互输入提供。真实凭据应保存在本机。

## 服务端依赖

这个仓库目录提供客户端技能。Gemini 动态拆解和 GPT 提示词重构在灵智服务端执行，客户端不包含对应核心母提示词或 n8n 工作流部署文件。使用前需确保后端支持 [服务端接口协议](references/server_prompt_generation.md) 中的 `visualContext.segments`、最终故事板与人物替换映射。

安装技能不会自动部署服务端。上传素材和生成会调用外部服务，费用按实际服务规则执行。

## 文件结构

| 路径 | 用途 |
| --- | --- |
| `SKILL.md` | 技能入口与完整执行规则 |
| `agents/openai.yaml` | Codex 显示信息与默认调用提示 |
| `scripts/` | 抽帧、故事板、人物映射、服务调用、预检、生成与质检 |
| `references/` | 各流程的规则与接口文档 |
| `core/`、`utils/` | 产品资料与视频工具 |
| `cli/` | macOS ARM64 与 Windows x64 的 LZStudio CLI |
| `tests/` | 随技能提供的测试用例 |

本次发布的技能文件与用户提供的 v5.13-server-core 安装包一致，另增加此发布说明和 Git 忽略规则。
