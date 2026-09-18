# 视频CLI路由与手动交付

本文件在Prompt和最终Storyboard已登记、即将进入视频生成时读取。灵智工坊CLI只用于原片拆解，不得用于图片或视频生成。

## 固定检测顺序

运行：

```text
python3 scripts/video_cli_preflight.py --manifest <manifest>
```

优先级始终是：

1. `libtv_cli`
2. `xiaoyunque_cli`
3. `dreamina_cli`

只选择第一个可用CLI。LibTV需存在可执行文件且`libtv --help`成功；小云雀和即梦还需在帮助信息中明确支持当前精确模型ID、`multimodal2video`所需参数和查询参数。检测本身不安装、更新或登录CLI。

检测与提交是两个阶段。在任何付费提交发生前，可按优先级选择首个可用CLI。一旦某个Segment已经返回画布、节点或taskId，provider锁定；失败、超时、网络错误、登录或余额异常都不得自动切到下一家重新提交。

## LibTV CLI

选中`libtv_cli`后，完整读取已安装的`libtv-cli` Skill，并以当前CLI的`--help`为权威接口。先做只读账户和模型检查，再创建或复用画布。每段的参考图按下述固定顺序上传、连接到视频节点，Prompt中使用CLI支持的`{{Node …}}`占位符引用这些上游图片。

执行层不再手工逐条调用`upload`/`node create`/`node --run`；统一使用`scripts/libtv_batch_generate.py`。脚本从Manifest读取每段的实际参考图，去重上传共享产品图和人物图，将CLI返回的nodeKey直接写入占位符及`--left`，先原子保存编排状态，再并行运行各Segment。编排命令失去终态时，下次只查询已有节点，不重新触发生成。

`libtv node ... --run`会自行提交、轮询并等待终态；调用方直接等待进程返回，不额外轮询，不因stderr中出现taskId而提前结束。

已内置的容错（不要再手工试探参数，避免浪费往返）：

- **模型名自动解析**：manifest 的逻辑模型ID（`seedance-2-mini/fast/2/2-5`）和 LibTV modelKey（`star-video2-mini`）都会自动映射为 CLI 只接受的展示名（`Seedance 2.0 Mini` 等），并在提交前用`libtv model search`实时校验。不要手动传 modelKey。
- **工作区与画布自动创建**：首次运行不必先手工取`workspaceId`，未传`--workspace-id`时脚本自动创建工作区；`libtv project create`返回的`projectMeta.uuid`也已兼容解析。已有画布时传`--project-uuid`复用。

## 小云雀与即梦CLI

小云雀：

```text
python3 scripts/run_generation.py --manifest <manifest> --video-provider xiaoyunque_cli --generation-approved
```

即梦：

```text
python3 scripts/run_generation.py --manifest <manifest> --video-provider dreamina_cli --generation-approved
```

这两个CLI直接读取本地图片。传入的精确provider模型ID由`scripts/model_capabilities.py`与`local_video_cli.py`共同校验，不得静默降级。默认5秒轮询；获得taskId后只恢复原provider的原任务。

## 参考图规则

每段固定顺序：

1. 该Segment的最终Storyboard。
2. 该Segment需要的新产品身份图；如沿用原产品则不添加。
3. 该Segment策略要求的人物身份图：用户提供的达人图，或多Segment跨段人物的无产品三视图（正面+单侧面+背面）。单Segment且用户未提供达人图时不添加人物参考图。

原视频及其任何直接抽帧都不作为达人身份图。若图片数量超过模型上限，多产品图可确定性合成一张产品身份板；仍超限则停止，不得静默丢图。

V7参考集合按完整镜头和replacementBindings确定，不要求每个角色都进入九格。产品图仅使用本段实际替换产品的referenceImages，原人物与目标creatorId依映射区分。具体数据见entity_bindings.md；图片数量策略及fast/strict质检不变。

## 无CLI手动交付

当`selected`为`null`时，先运行：

```text
python3 scripts/prepare_manual_video_handoff.py --manifest <manifest>
```

然后用下列结构友好告知用户：

```markdown
## 视频生成已就绪

当前未检测到可用的 LibTV CLI、小云雀 CLI 或即梦 CLI，所以没有提交付费视频任务。

你可以选择：

1. 安装并配置 LibTV CLI、小云雀 CLI 或即梦 CLI，然后继续生成。
2. 把下方每段Prompt和对应参考图一起交给你常用的视频生成工具。

### Segment 01｜<title>

- Prompt：[打开纯文本](</absolute/path/segment-01-prompt.txt>)
- 参考图 1｜最终Storyboard：[打开图片](</absolute/path/storyboard.png>)
- 参考图 2｜产品身份图：[打开图片](</absolute/path/product.png>)
- 参考图 3｜人物身份图：[打开图片](</absolute/path/creator.png>)

上传顺序：最终Storyboard → 产品身份图 → 人物身份图。
```

只列出脚本返回的实际文件；某段不需要产品图或人物图时，明确写“本段无需额外产品参考图”或“本段无需额外人物参考图”，不显示虚构占位路径。Prompt正文仍按`prompt_display_format.md`在聊天中完整展示，文件链接只是备用。
