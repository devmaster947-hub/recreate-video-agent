# 复刻爆款视频

## 概述

本 Skill 根据**已获授权**的带货原视频、产品和达人参考素材，生成经审核的分段方案、分镜和提示词，再通过 LuluLab 生成新视频。适合拥有原素材使用权的创作者和商家。Agent Skills 根目录是本仓库的 `skills/recreate-video-agent/` 文件夹，其中包含 `SKILL.md`。

## 主要能力

- 预处理原视频，通过 `RecreateVideoPromptV3` 拆解。
- 制作分段分镜、绑定产品及达人参考，并在生成前展示提示词供确认。
- 使用 `ImageGenV2` 制图和 `VideoGenV2` 的 Seedance2 Mini 生视频，保存任务 ID 以便恢复，避免盲目重复付费提交。
- 按现有规则独立处理交互语言和目标视频语言。

## 环境要求

需要能够读取 Agent Skills 并操作文件的智能体（如 Codex、Claude Code）、Python 3、FFmpeg/FFprobe、Node.js 20+、macOS 或 Windows、LuluLab 网络连接，以及在首次服务端调用前配置 LuluLab API Key。其他智能体需要等效的文件型 Skill 机制。Node.js CLI 已包含在 `scripts/lululab_cli.mjs`，无需 npm 或 `npx` 安装。

## 安装

可向智能体发送：

> 请将这个 LuluLab Skill 安装到我当前使用的 AI 智能体环境：https://github.com/LuluLab-AI/recreate-video-agent 。请遵循项目的官方安装说明。

智能体先检查仓库，再把完整的 `recreate-video-agent/` 文件夹复制到当前智能体的用户 Skill 目录，名称保持 `recreate-video-agent`。各智能体目录不同，须按其当前文档确认。`SKILL.md`、`scripts/`、`references/`、`core/`、`utils/`、`assets/` 应在同一根目录；不能只复制 `SKILL.md`，也不能把仓库的 `skills/` 包装目录当作 Skill 根目录。已有同名 Skill 时先比较版本，替换前征求用户确认；不覆盖其他 Skill。在 Skill 根目录运行 `node scripts/lululab_cli.mjs --help` 与 `python3 scripts/video_cli_preflight.py --help`，重新加载 Skill 列表并确认可识别。

## 快速开始

- “用 $recreate-video-agent 按这条已授权的 20 秒产品视频和我的新产品照片制作新片，付费生成前先展示方案。”
- “将这条已授权的带货视频改作面向美国市场的英文版本，但请用中文和我交流。”

首次确认涵盖原片上传和首次付费图片、视频生成；后续付费重试需要重新决策。详见 [SKILL.md](SKILL.md)。

## 配置

随包 CLI 支持 `upload <文件>`、`user --credits`、`task submit --workflow-id <ID> --input <JSON>` 和 `task fetch --id <ID>`。在本机设置 `LULULAB_API_KEY`，或在已有私有文件 `~/.recreate-video-lululab/config.json` 中设置 `apiKey`。不要把 Key 写入 GitHub、manifest、提示词或命令参数。Node 不在 `PATH` 时可用 `LULULAB_NODE` 指定；`LULULAB_CLI` 只能覆盖为经过检查的 `.mjs` 入口。详见 [CLI 契约](references/lululab_cli_contract.md)。

## 支持语言

中英文交互遵循 Skill 现有的语言优先级。用户指定的目标视频语言与对话语言独立。

## 常见问题

| 问题 | 检查方法 |
| --- | --- |
| 无法识别 Skill | 确认 `recreate-video-agent` 根目录及其中的 `SKILL.md`，重新加载列表。 |
| 找不到 CLI | 检查 Node.js 20+、`scripts/lululab_cli.mjs` 和 `--help`。 |
| 缺少 API Key | 首次服务端调用前在本机配置 `LULULAB_API_KEY`。 |
| 网络请求失败 | 检查服务连接，重新提交前先恢复已保存的任务 ID。 |
| 模型调用失败 | 查看终态任务状态，不静默切换模型或重复付费任务。 |
| 缺少依赖 | 安装报告指出的 Python、FFmpeg 或 FFprobe 依赖，再运行预检。 |

## 支持

网站：https://lululab.ai  
邮箱：contact@lululab.ai
