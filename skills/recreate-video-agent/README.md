# LuluLab.AI · 复刻爆款视频

<p align="center"><img src="assets/lululab-logo.png" alt="LuluLab.AI" width="240"></p>

由 **LuluLab.AI** 开发与维护。

Skill调用名 `recreate-video-agent`，版本1.1.5。拆解、图片和视频均通过LuluLab CLI，工作流由CLI自动触发。

- 拆解：RecreateVideoPromptV3，保持原预处理、分段规划和恢复协议。
- 图片：ImageGenV2，gpt-image-2-5-sunburst、1K。
- 视频：VideoGenV2，Seedance2 Mini（seedance-2-mini）、720p、每段4–15秒；20秒任务保留11秒+9秒规划。

不调用原生图片工具，也不自动换视频平台。工作流ID内置，不要求客户端导入或部署工作流。

解压客户交付ZIP，把 `recreate-video-agent` 文件夹复制到Codex skills目录（macOS `~/.codex/skills/`，Windows `%USERPROFILE%\.codex\skills\`）。需要Python3、FFmpeg、FFprobe；内置macOS arm64与Windows x64的LuluLab CLI 0.0.2。

Key使用LULULAB_API_KEY或本机私有 `~/.recreate-video-lululab/config.json` 的apiKey，不写入聊天、manifest或交付物。可用LULULAB_CLI指定可执行路径。工作流ID环境变量覆盖为可选项。

一次启动确认后执行已授权的首次生成。拆解仅在服务端明确终态失败时自动重试一次；图片和视频不自动付费重试，已有taskId只恢复查询。

调用：`$recreate-video-agent 帮我复刻这个视频`。详细流程见SKILL.md；任务接口见references/lululab_cli_contract.md。

本地回归：`python3 -m unittest discover -s tests -p 'test_*.py'`。Windows包按原附件SHA256校验，未在Windows运行。
