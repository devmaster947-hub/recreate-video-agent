---
name: recreate-video-agent
description: 适用于复刻 TikTok、抖音等平台的爆款带货视频。通过灵智工坊拆解原片的动作、镜头、声音和营销节奏，再结合真实帧分镜替换商品、达人及目标市场内容，最后自动选择可用的视频生成通道完成成片。
license: Apache-2.0
metadata:
  skillhub:
    slug: recreate-video-agent
    version: 1.1.1
    displayName: 复刻爆款视频
    summary: 拆解爆款短视频，替换商品与达人后生成新的分镜、视频提示词和成片。
    tags:
      - 视频复刻
      - 爆款视频
      - 电商广告
      - AI视频
    homepage: https://github.com/devmaster947-hub/recreate-video-agent
---

# recreate-video-agent v5.1

## 技能概述

本技能用于将已授权的爆款带货视频复刻为新的商业视频。它会分析原片的镜头结构、人物动作、运镜、口播、声音和营销节奏，按分段生成真实帧分镜，并按需替换商品、达人、语言及目标市场内容。完成分镜和提示词确认后，技能会自动选择首个可用的视频生成通道，生成、拼接并交付成片。

## 工具边界（全流程最高优先级）

- 灵智工坊CLI始终用于`RecreateVideoPromptV3`原视频语义拆解；它同时是当前智能体没有原生图像能力时的图片兜底，以及其他三个视频CLI都不可用时的视频兜底。
- 任何图片生成或编辑，包括商品图、达人图、身份图、Storyboard去字、人物/商品替换与整板重绘，固定路由为`智能体原生图像能力 → lingzhi_cli`。只有当前会话没有原生图像工具时才调用灵智；原生工具存在但单次失败时不得自动切换。灵智图片固定使用`gpt-image-2`与`1K`，比例沿用当前任务。
- 视频生成阶段必须依次检测本机`libtv`、小云雀CLI、即梦CLI和灵智工坊CLI，固定优先级为`libtv_cli → xiaoyunque_cli → dreamina_cli → lingzhi_cli`。选中第一个存在且通过基础可用性检查的CLI后立即用它执行已授权的首次生成，不再要求用户手动选渠道。
- 使用LibTV时必须完整读取已安装的`libtv-cli` Skill，并以`libtv --help`及子命令`--help`为当前接口事实来源；不猜测参数、模型ID或私有HTTP地址。小云雀和即梦通过Skill内置的本地CLI适配器调用。
- 视频CLI预检必须按优先级短路：一旦发现可用LibTV，立即返回其绝对可执行路径并跳过小云雀、即梦、MCP资源枚举、插件市场搜索和网页探测。`libtv`不在`PATH`时必须检查官方默认安装位`~/.libtv/libtv`（Windows为`~/.libtv/libtv.exe`）；同一任务后续复用预检返回路径，不重新发现连接。
- 四家CLI都未检测到时，不安装、不调用其他平台。必须以友好方式同时告知用户两种后续方案：安装LibTV、小云雀、即梦或灵智工坊CLI；或复制已展示的Prompt并与指定参考图一起到其他工具生成。手动交付必须按Segment列出每张实际所需图片的角色和可点击绝对路径，不能只说“请带参考图”。
- 后续章节或参考文件如与本边界冲突，以本节为准。

## 0. 灵智 API Key 按需门禁（仅在首次服务端拆解前）

复刻任务开始时不得检查、索取或提醒用户配置灵智工坊 API Key，也不得把 Key、登录或授权状态加入启动确认单。先正常读取原视频、展示启动确认、初始化manifest并完成本地技术分析。只有流程即将首次执行第2节、调用`RecreateVideoPromptV3`进行服务端拆解时，才运行下面的零消耗远端鉴权预检：

```text
python3 scripts/lingzhi_key_preflight.py
```

预检会从`LZSTUDIO_API_KEY`、`RECREATE_VIDEO_API_KEY`或`~/.recreate-video/config.json`的`apiKey`读取非空 Key，并调用灵智工坊`account --credits`完成真实服务端鉴权。只有命令返回`{"ok": true, "authenticated": true}`才算通过；“本地存在非空 Key”、CLI 可启动或未经证实的网络错误都不算通过。

- 未配置 Key 时，只在这个按需检查节点提醒用户：“接下来需要调用灵智工坊拆解原视频，请前往 https://www.lingzhiai.com.cn/ 获取 API Key。”不得把这条提醒提前到任务开始、素材检查或启动确认阶段。
- 远端预检因 Key 无效、权限不足、网络失败、CLI 不可用等原因未通过时，停止即将进行的灵智拆解，保留已经完成的本地分析和manifest；只展示脱敏原因，请用户检查或更换 Key 后重试。
- 预检通过前不得上传原视频或提交灵智任务。每个新任务在首次灵智调用前都必须完成一次预检，不得因为之前任务曾通过而跳过；预检之前的本地读取、确认、初始化与技术分析不受此门禁限制。
- 如果用户提供 Key，只将其用于本地配置和鉴权；不在后续聊天、命令输出、日志、manifest或交付物中回显完整 Key。
- 本机已配置 Key 时不提示、不重复索取，直接在按需检查节点执行远端预检。预检只证明当前 Key 可被灵智服务鉴权，不把余额数值写入日志、manifest或交付物。

## 职责与授权

用户要求优先。使用最少Segment，每段唯一rawStoryboard。当前Planner V2策略输出9个anchors；Storyboard布局由客户端根据anchors数量派生，不作为服务端协议字段。原视频决定动态和声音，target Storyboard决定替换后的静态视觉。新任务默认`storyboardValidationMode=fast`；只有用户明确说“严格复刻”或要求区域锁定时才使用`strict`。旧Manifest缺少该字段时按`strict`处理。不要将用户人物微调要求推广为所有任务的默认要求。

沿用 references/start_confirmation_format.md 的一次启动确认，字段为产品、达人、模型、时长、国家、语言及其他要求。确认问题必须提供数字选项，让用户只回复`1`即可按当前配置开始，回复`2`则进入配置修改。确认开始授权本轮原视频上传、服务端拆解最多两次提交（首次明确终态失败时自动重试一次）、图片编辑及通过第一个可用视频CLI进行的首次视频生成。除这一次服务端拆解自动重试外，失败或质量不合格不自动授权其他付费重试。默认Seedance 2 Fast、原时长、原国家语言、原人物产品。逻辑模型与时长能力仍由 scripts/model_capabilities.py 决定；LibTV按实时schema解析模型，小云雀和即梦按Skill内精确provider模型ID映射，不猜测模型ID或30秒能力。

图片生成与编辑优先使用当前智能体原生图片工具；只有当前会话没有该能力时才使用Skill内的灵智图片脚本。服务端语义拆解仅通过LZStudio CLI的RecreateVideoPromptV3，保持现有模型路由。客户端不导出音频、不运行ASR、不做TTS或音频参考。服务端密钥严格按第0节延迟到首次灵智拆解前检查：启动阶段不检查、不索取、不提醒；密钥不在后续聊天或交付物中展示。服务端授权错误仅展示既有管理员提示，保留脱敏。

人物ID仍是Prompt语义绑定的硬门禁，但达人参考图按来源和Segment数量决定。绝不把原视频抽取的单帧登记或提交为达人参考图。用户提供达人图时直接使用用户图；用户未提供时，多Segment任务必须按上述图片路由生成每位持续人物的无产品多视图，单Segment任务不生成、不提交达人参考图。

## 1. 预检与初始化

当前安装版保留 macOS Apple Silicon 和 Windows x64 的内置 LZStudio CLI；`server_video_analysis.py`按 `--cli`、`LZSTUDIO_CLI`、内置 CLI、系统 `PATH` 的顺序发现可执行文件。只有用户明确要求安装到系统 `PATH` 时，才按 references/windows_lzstudio_cli.md 运行安装脚本。不从网络下载或自动替换内置二进制。

读取原文件时长（最长360秒），确认后运行generation_manifest.py init、benchmark_analysis.py和set-benchmark-analysis。源视频保持不变。技术分析阶段只产生媒体信息与technicalCutCandidates；最终Segment与Storyboard锚点由服务端确定，此时不抽板、不在客户端计算最终分段。原时长模式按视频模型要求取最近整数秒；当整数目标与真实媒体时长差值不超过0.5秒时，这是正常归一化，不得作为异常、阻塞或要求用户确认。

用户选择替换产品时，`init`后必须立即把每张用户产品图登记为独立生成输入，不得只复制到任务目录、写进Prompt或画进Storyboard：
```text
python3 scripts/generation_manifest.py set-product-references --manifest <manifest> --image <product-image> [--image <product-image-2>]
```
该命令必须把`product.useBenchmarkProduct`设为false并保存`product.productImages`。未完成登记不得进入Storyboard替换；产品图与Storyboard、人物身份图用途不同，生成时三类引用都必须提交。

## 2. Gemini先拆解

完整读取 references/server_video_analysis.md。执行：
```text
python3 scripts/server_video_analysis.py --manifest <manifest> --benchmark <video>
```
用户明确提供可访问的HTTP(S)原视频直链并要求跳过上传时，仍保留本地原片用于技术分析、Storyboard和成片对齐，但服务端提交改用：
```text
python3 scripts/server_video_analysis.py --manifest <manifest> --benchmark <local-video> --benchmark-url <video-url>
```
`--benchmark-url`只替换上传步骤，不放宽启动确认、最多两次提交、时间轴校验或失败重试规则。
只上传原视频；新任务请求携带`plannerVersion=2`（旧任务保留V1），userConfig附technicalCutCandidates、targetDuration、sourceDuration、targetDurationSource和blueprintSchemaVersion=7.0；服务端任务默认每5秒查询一次状态，避免任务已完成仍等待20秒轮询间隔；其中sourceDuration/targetDurationSource仅供服务端Code节点做确定性规划，不注入Gemini Prompt；不传rawStoryboards，不要求预先生成图片。线上需由管理员导入配套v3.2双版本工作流JSON（assets/workflows/replication-v2.json）；本地升级不代表线上启用。

同一manifest排他锁、上传前preparing_submission、每次taskId原子receipt必须保留。首次任务只有在服务端明确返回`failed/failure/error/cancelled/timeout`终态时才自动重提一次；第二次失败立即停止。鉴权/余额异常、CLI异常、客户端轮询超时、未知状态或提交结果不确定均不自动重提。后台session_id继续使用同一执行会话；functions cell与进程session不可混用。无输出/无exit code/无taskId不代表未提交。已有ID只用--resume-task-id恢复最新任务。旧蓝图仍可查看，不自动迁移或额外付费重拆。

## 3. 读取服务端复刻规划

RecreateVideoPromptV3在完成Gemini拆解后，由同一n8n工作流中的纯Code节点生成`replicationPlan`。该节点不调用任何大模型、不增加Token消耗，负责Segment与Storyboard anchors规划。服务端契约从本版起固定为`replicationPlan.schemaVersion=1.0`、新任务`plannerVersion=2`，旧任务兼容`plannerVersion=1`；Planner Engine与Policy分离。客户端不得重新计算、评分或修改规划规则。

执行：
```text
python3 scripts/blueprint_timeline.py --manifest <manifest> --output <task>/analysis/segment-anchors.json
```
该脚本只做协议适配：校验`replicationPlan`的schema/planner版本，把服务端结果写入既有`segment-anchors.json`兼容路径，并根据anchors数量派生本地Storyboard布局；本地不包含动态规划、切镜/对白停顿评分或keyframe锚点选择算法。

Planner V2沿用V1分段引擎与时长规则：最少Segment、整数秒模型限制、优先靠近真实cut/对白停顿、尽量不切断对白；当前V1 Policy使用9个anchors，优先使用Gemini keyframes并补足时间覆盖。V1内只允许兼容性参数调优；改变已发布模型的合法时长范围、anchor数量或协议字段必须新增Planner版本，不能覆盖V1。真实媒体时长与整数目标差异不超过0.5秒时，服务端钳制原片抽帧到`sourceDuration-0.04s`以内；超过0.5秒则规划失败。服务端返回`blueprintWarnings`和`unresolvedCuts`仅供复核，不触发再次付费拆解。

## 4. 生成唯一rawStoryboard

执行storyboard.py，传--video、--timestamps-file、--output-dir、--model（`--timestamps-file`既接受9个anchor对象的JSON，也接受每行一个时间戳的纯文本，不要再为格式往返试探）。当前Planner V2返回9个anchors，因此每段生成一张3×3 Storyboard；客户端不读取服务端layout字段，而是从anchors数量派生布局。抽帧成功、9格齐全、时间戳位于Segment窗口内由程序硬校验；不再为了模糊、闭眼、遮挡等逐阶段单独调用视觉质检。起点保持Segment开始状态。因最近整数秒取整而多出的尾部只延续或定格原片最后可观察状态，不得凭空新增镜头、动作、对白或剧情；这类不超过0.5秒的尾部补齐静默处理，无需向用户报警。

抽板后、首次图片编辑前，完整读取 references/entity_bindings.md。在原有分镜理解步骤对照原始九格与蓝图核对关键人物/产品，仅有具体疑问时补看相关原片帧；保存来源人物/产品到目标的 replacementBindings。不能把说话人当成人物清单，也不能把所有人物替换为同一达人。此来源核对不增加编辑后或视频生成后质检。

按 references/storyboard_editing.md 清理对白字幕、双语字幕及手机UI，仅保留确有剧情作用的金额特效。按固定图片路由把去字和产品/达人替换尽量合并到一次整板编辑。当前会话无原生图像能力时，将Prompt保存到文件并运行：
```text
python3 scripts/lingzhi_image_generate.py --prompt-file <prompt.txt> --reference-image <reference.png> --aspect-ratio <ratio> --output <output.png> --report <report.json>
```
纯文生图省略`--reference-image`；多张参考图重复传参。取得taskId后只能用`--resume-task-id <id>`恢复同一任务，不得重新提交。快速模式不做Storyboard视觉质检或自动重做；严格模式仍执行最终视觉质检。抽帧结果为处理暂存，最终板登记为storyboards.original/edited；不建立额外故事板类别。自动重试不默认授权。

清理完成后用generation_manifest.py add-storyboards-from-metadata登记。图片替换阶段沿用这张板，按KEEP/CHANGE/AUTO-DESIGN清单只修改用户指定人物或产品。人物在各段适度改形象但保持同一身份设定（仅用户提出此要求时）。

快速模式只生成一次整板候选图；不建Replacement Map，不运行`validate-map`、`lock-merge`、mask预览或视觉质检，不因残留、畸形或人物漂移自动重做。立即运行：
```text
python3 scripts/storyboard_cells.py fast-prepare --original <rawStoryboard> --edited <candidate> --output <finalStoryboard>
```
`fast-prepare`只检查文件可读、候选画布比例偏移不超过15%、3×3网格可识别，并恢复原始时间标签和输出尺寸。以`method=whole-board-fast-v1`、`validationLevel=mechanical`登记edited；`replacementVerified`只表示文件、SHA、布局和引用完整，不表示视觉质量合格。

严格模式才建立`mergeMode=object-regions-v1` Replacement Map，运行`validate-map`和`lock-merge`，要求`protectedPixelsRestored=true`、`outsideMaskChangedPixels=0`，并完成一次最终视觉质检。区域冲突时停止，不扩大mask绕过保护。

登记edited时复用同一9个anchors，布局固定3×3。对每格实际可见人物建立身份清单并把全部ID写回anchors；原视频画面只能用于人物理解和生成多视图时的内部视觉依据，不得把任何原片单帧复制、改名或直接登记为Creator图。

达人参考图策略在最终Segment数量锁定、来源映射确定后执行；需要人物图时先准备目标身份，再编辑相关分镜：

- 用户为某达人提供图片：原样登记为`user_provided`并在相关Segment提交；不得再用原片帧补充。
- 用户未提供且只有1个Segment：不生成、不登记、不提交达人图；creatorIds只用于Prompt中描述人物。
- 用户未提供且有多个Segment：按固定图片路由为每位跨段或需保持一致的人物生成一张干净多视图。整张图只需包含同一人物的正面、一个侧面和背面，不再重复生成左右两个侧面；三个视图的身份、发型、服装必须一致。使用纯净中性背景，不得出现产品、道具、场景、文字、水印或原视频画面。视觉检查确认无产品后才登记。

登记命令：
```text
python3 scripts/generation_manifest.py add-creator --manifest <manifest> --creator-id <id> --image <user-image> --source-type user_provided
python3 scripts/generation_manifest.py add-creator --manifest <manifest> --creator-id <id> --image <generated-sheet> --source-type generated_multiview --view front --view side_profile --view back --product-free-verified
```
`sourceType`缺失、原片抽帧来源、生成多视图角度不全或未通过无产品检查时，Prompt交接与视频生成都必须阻断。

## 5. Prompt与生成

执行prepare_prompt_handoff.py，直接复用最终板，不重绘或拆格生成。完整读取 references/local_prompt_pipeline.md，当前智能体一次生成最终Prompt。不得固定3～5宏观阶段；完整覆盖本段shots、cuts、beats及utterances，包括未进入Storyboard的镜头。人物/产品集合来自本段全部镜头，再经replacementBindings映射；九格实际可见人物只是其中的子集。

Prompt的指令、镜头、动作、画面、运镜和声音说明必须使用中文；对白使用用户指定的目标语言，默认沿用原语言。指定语言与原片不同时，在最终Prompt阶段翻译并保留utteranceId/lineId关联；原片蓝图对白不改写。不增加语言校验。

保存local-video-prompts.json并用set-prompts登记（沿用该文件名以兼容现有Manifest）。登记后必须完整读取 references/prompt_display_format.md，运行`export_prompt_texts.py`导出逐段纯文本备份，并在同一条聊天消息中按“概览 + Segment卡片 + 独立text代码块”展示全部Prompt。Prompt正文必须从已登记数据逐字复制，不改写、不摘要、不折叠；代码块便于一键复制，纯文本链接只作辅助，不得代替聊天中的完整正文。该展示不新增中间确认门；已授权的首次生成继续执行。

完整读取 references/generation_rules.md，然后执行：

```text
python3 scripts/video_cli_preflight.py --manifest <manifest>
```

严格按返回的`selected`处理：

1. `libtv_cli`：完整读取`libtv-cli` Skill，执行只读账户、项目、模型schema与参考图能力检查。模型名、工作区与画布由脚本自动解析和创建，不要手工试探`--libtv-model`或先取workspaceId。首次生成统一运行`python3 scripts/libtv_batch_generate.py --manifest <manifest> --generation-approved [--vip-download]`；如需指定工作区可传`--workspace-id <id>`，已有画布时可传`--project-uuid <uuid>`复用。该命令一次完成参考图去重上传、nodeKey回填、按顺序连接最终Storyboard/产品图/人物图、实际UUID占位符写入、全Segment并行运行及下载。禁止再手工逐节点编排。`libtv node ... --run`会自行等待终态；脚本不额外轮询，任务不确定时保留状态并禁止自动重提。仅需检查时传`--plan-only`，不创建画布、不上传、不提交付费任务。
2. `xiaoyunque_cli`：运行`python3 scripts/run_generation.py --manifest <manifest> --video-provider xiaoyunque_cli --generation-approved`。
3. `dreamina_cli`：运行`python3 scripts/run_generation.py --manifest <manifest> --video-provider dreamina_cli --generation-approved`。
4. `lingzhi_cli`：运行`python3 scripts/run_generation.py --manifest <manifest> --video-provider lingzhi_cli --generation-approved`。
5. `null`：运行`python3 scripts/prepare_manual_video_handoff.py --manifest <manifest>`，再按 references/generation_rules.md 的“无CLI手动交付”模板回复。必须列出四种可安装CLI，并按Segment明确列出Storyboard、产品图、人物身份图的实际文件；不得只交付一句泛化的“Prompt和参考图已就绪”。

每个Segment只提交一次并立即保存返回的画布、节点或任务标识；之后只恢复同一任务。已开始提交后，失败、未知状态、超时、登录或余额异常都不得自动切换到下一家CLI，避免重复付费提交。首次提交必须处于本轮启动确认的授权内；任何重生成均需用户重新授权。

付费提交前必须检查`referenceAudit.segments[].orderedFiles`：顺序为最终Storyboard、独立产品参考图、该段人物身份图。只要Replacement Map含`replaceProduct=true`，产品图必须同时出现在`registeredProductImages`、`productImages`和`orderedFiles`中；任一处缺失、文件为空，或Manifest仍标记沿用原产品，都必须阻止提交。不得因为Storyboard里已经画出了新产品而省略独立产品图。

## 6. 核验与交付

默认使用**快速技术质检**：只读取原片与成片的媒体信息，检查时长、画面比例、音轨存在性和文件可读取性；不重新做低清视觉扫描，不生成10组原片/成片对齐图。这样不会影响已生成视频，只减少生成后的等待。

只有用户明确要求“深度质检/复刻效果评估/对齐图”时，才运行full质量模式（本地CLI路径可传`--full-quality-review`；`quality_review.py analyze`可传`--profile full`），生成切镜扫描与aligned-comparison。保留原视频路径供此时使用。technicalPassed仅代表技术指标，不能声称语义复刻成功；对白串人、乱码、反应/反转遗漏须如实报告。失败报告不隐藏已生成成片，不自动重生成。

本地修改Skill和工作流JSON不代表线上已发布。交付配套导入JSON并注明管理员导入启用后才适用新服务端契约。
