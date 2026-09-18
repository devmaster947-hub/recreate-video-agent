# CHANGELOG

## 1.0.2 — 2026-09-18

- LibTV 逻辑模型 ID 和 modelKey 自动映射为 CLI 展示名，并在提交前查询校验。
- 未指定工作区时自动创建工作区，兼容 `projectMeta.uuid` 画布返回结构。
- Skill 标题无法提供版本时回退读取 frontmatter；SkillHub `1.x.y` 包版本按当前工作流能力处理。
- Storyboard 时间戳文件同时支持 JSON 和每行一个数字的纯文本。
- 保留当前安装版内置的 macOS ARM64 与 Windows x64 LZStudio CLI `0.0.5`。
- 增加上述修复的针对性回归测试，并更新受版本策略影响的旧测试夹具。

## 1.0.1 — 基线

人物与商品身份绑定版复刻工作流。
