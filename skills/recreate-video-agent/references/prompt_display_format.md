# Prompt 展示格式

本文件规范Prompt登记后的聊天展示与复制交付。数据源始终是`analysis/local-video-prompts.json`中已登记的`segments[]`，不从聊天展示反向解析Prompt。

## 保真与复制原则

- 按`segments[]`原始顺序展示全部Segment，不折叠、不截断、不以“同上”、JSON、附件或文件链接代替。
- 每个`prompt`逐字复制到单独的`text` Markdown代码块；不改写、翻译、摘要、重排或补全。展示标题和元数据放在代码块外，不混入Prompt。
- 代码块必须只包含Prompt正文，使用者点击代码块的复制按钮后可直接粘贴到生成工具。如Prompt本身含三个连续反引号，用更长的外层围栏，不改动Prompt内容。
- 额外导出每段纯文本，但链接只是备用复制通道，不得取代聊天中的完整Prompt。

## 导出纯文本备份

登记Prompt后执行：

```text
python3 scripts/export_prompt_texts.py --manifest <manifest> --output-dir <task>/prompts
```

该命令为每段生成`segment-NN-prompt.txt`。展示时使用命令返回的绝对路径创建可点击的本地Markdown链接，不手写或猜测路径。

## 聊天展示模板

先给出简短总览，只显示已有字段，缺失值显示“未提供”，不推断：

```markdown
## 复刻视频生成提示词

> 已生成 <segment-count> 段Prompt。每个代码块都可直接复制；下方纯文本文件作为备用。

| Segment | 标题 | 时长 | 原片时间窗 |
|---|---|---:|---:|
| 01 | <title> | <duration>秒 | <globalStart>–<globalEnd>秒 |
```

然后为每个Segment输出一张卡片，编号补足两位：

````markdown
### Segment 01｜<title>

`<duration>秒` · `原片 <globalStart>–<globalEnd>秒` · `Storyboard <storyboardIds>`

```text
<完整prompt原文>
```

[打开 Segment 01 纯文本备份](</absolute/path/segment-01-prompt.txt>)
````

所有Segment展示完后，只用一句话说明后续状态，不重复Prompt，不新增确认门：

```markdown
Prompt已全部展示并导出备份；流程继续进入已授权的首次视频生成。
```

展示完成后按`generation_rules.md`检测并调用第一个可用视频CLI。四家CLI都不可用时，将末句改为无CLI的手动交付说明，仍然完整展示Prompt，并按Segment列出所需参考图。
