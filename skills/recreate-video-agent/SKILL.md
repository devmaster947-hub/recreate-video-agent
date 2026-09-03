---
name: recreate-video-agent
description: 通过统一《复刻要求确认单》一次选择产品、达人、模型等启动配置后，按“分段真实帧故事板→可选整板产品/达人替换→服务端拆解与重构提示词→用户确认→视频生成与质检”高保真复刻 TikTok、抖音等产品视频；核心拆解与提示词逻辑由灵智工作流执行。不用于从文字重做 Storyboard 或自由创作 Hook。
---

# recreate-video-agent v5.13-server-core

正式流程固定为五步：

1. 按模型时长限制生成每段一张真实帧4×4 Storyboard。
2. 可选地在完整Storyboard原图上局部替换产品和/或达人。
3. 客户端上传原视频、最终Storyboard、产品图和达人图，并调用服务端核心工作流。
4. 服务端完成Gemini动态拆解与GPT多模态重构，客户端校验并展示结构化Video Prompt。
5. 视频生成、内部质检与交付。

启动前必须通过统一《复刻要求确认单》完成一次启动确认。该确认在同一个交互步骤中收集产品、达人、视频模型、复刻时长、目标国家/语言和其他配置，只授权执行到Step 4；Step 4完成后必须展示完整视频提示词并取得第二次“确认生成”，才可进入付费Step 5。除服务端核心任务和生成确认门外，不加入其他常规确认。新任务默认上传产品图、沿用对标视频达人、使用`Seedance 2 Fast`、复刻时长与原视频一致、目标国家/语言与原视频一致，其他复刻要求为“无”；原比例、720p、`strict`质量档和高保真模式作为内部固定默认值执行，不在确认单中展示。事实分工固定为：最终Storyboard和anchors负责静态视觉与实体位置，服务端Gemini拆解负责其中可用的动作、声音、口播和营销逻辑，产品/达人图只锁定身份，服务端GPT负责等价状态映射与最小适配，客户端负责Segment锁定、程序校验和质量判断。

## 启动前确认门

从对话和附件提取已提供信息后，首次响应必须直接展示统一《复刻要求确认单》，不得先单独询问产品或达人。用户已经在当前请求中明确表达的选择直接预填；未明确的项目直接采用确认单中的默认值，不显示“待选择”。

确认单固定使用以下结构和措辞；只把示例视频名替换为实际文件名，并把用户已明确指定的值预填到对应字段：

## 《复刻要求确认单》

- 对标视频：`对标视频.mp4`
- 产品：上传产品图（默认，请附至少一张）/ 使用对标视频中的原产品
- 达人：替换为新达人 / 沿用对标视频达人（默认）

* 视频模型：Seedance 2 Fast（默认）
  - 其他可选：Seedance 2 Mini、Seedance 2、Seedance 2.5、Minimax H3、Grok Imagine 1.5 Preview
* 复刻时长：与原视频一致（默认）
  - 其他可选：仅开场10秒、自定义时长
* 目标国家/语言：与原视频一致（默认）/ 自定义语言
* 其他复刻要求：无

确认单不得展示输出规格、复刻模式、必须保留项、禁止项、候选产品图、候选达人图或单独的产品卖点字段。用户提供的产品卖点或希望相对对标视频修改的内容，原样记入“其他复刻要求”；没有时显示“无”。

默认产品选项是“上传产品图”。启动前必须至少收到一张产品参考图；若用户未上传，只要求其补充产品图，或明确改选“使用对标视频中的原产品”。默认达人选项是“沿用对标视频达人”；选择“替换为新达人”时，启动前必须收到达人参考图。达人附件本身不改变默认选择，除非用户明确选择替换。不得自动生成新达人。用户回复“确认开始”表示同意把本任务执行到Step 4所必需的原视频、Storyboard、产品图和达人图上传至灵智处理；未确认前不得上传。

自定义时长必须填写正整数秒，含义固定为复刻原视频`0～N秒`，不得压缩整片，也不得超过原片实际时长。原片不足10秒时，“仅开场10秒”等同复刻完整原片。选择“自定义语言”时必须给出具体语言；未给出时只补问语言，不得启动。

确认单末尾要求用户用一条回复完成选择和授权。接受全部默认项时，要求用户附上至少一张产品图并回复“确认开始”；改用原产品时，推荐格式为：

> 产品=原产品；确认开始

用户也可以在同一条回复中覆盖任意默认项，例如：

> 产品=上传；达人=替换；模型=Seedance 2；时长=10秒；语言=英语；其他复刻要求=<原文>；确认开始

“产品=上传/原产品”分别对应“上传产品图/使用对标视频中的原产品”；“达人=替换/默认”分别对应“替换为新达人/沿用对标视频达人”；“模型=默认”表示`Seedance 2 Fast`；“时长=默认”表示与原视频一致；“语言=默认”表示与原视频一致。用户此前已经明确表达且在最新版确认单中预填的配置无需重复，只需满足所选素材条件并明确回复“确认开始”。

只有在用户看到最新版统一确认单、所有自定义值完整、所选上传或替换项具备对应参考图，并明确回复“确认开始”后才可执行。若默认上传产品图但图片缺失、替换达人但图片缺失，或自定义语言/时长缺少具体值，保留其他已选配置，只补问缺失内容并要求再次确认，不得初始化任务、分析视频或上传素材。

复刻时长与原视频一致或原片不足10秒时，源时长不是整数秒不得增加二次时长确认；启动后由技术分析自动采用最接近的合法整数秒并继续。分析结果必须记录`durationMode`、`requestedDuration`、原始时长、目标时长、`replicationWindow`和调整量。自定义时长超过原片、或复刻总时长短于所选模型最短时长时必须停止，不得静默延长。任何用户主动配置改变都会使旧确认失效；自动时长归一化不视为配置改变，无需再次确认。

确认前只能整理输入和检查附件路径；不得初始化任务、分析视频、抽帧、编辑图片、上传素材或调用任何生成服务。确认后运行：

```text
python3 scripts/generation_manifest.py init --task-id <taskId> --output-root <output-root> --video-model <videoModel> --duration-mode <source|opening_10|custom> [--target-duration <customSeconds>] [--custom-requirement <text>]
python3 scripts/benchmark_analysis.py --video <benchmark> --model <videoModel> --duration-mode <source|opening_10|custom> [--target-duration <customSeconds>] --output <task>/analysis/benchmark-analysis.json
python3 scripts/generation_manifest.py set-benchmark-analysis --manifest <manifest> --file <task>/analysis/benchmark-analysis.json
```

有产品图时按需读取 [references/product_brief_generation.md](references/product_brief_generation.md)，不得虚构卖点、功能或功效。

## Step 1：分段真实帧 Storyboard

完整读取 [references/storyboard_extraction.md](references/storyboard_extraction.md)。只分析`replicationWindow`覆盖的原视频区间和其中的真实切镜，再按模型能力确定最少Segment；边界优先吸附到合法范围内的真实切镜，确定后不得漂移。

每段必须有连续的`globalStart/globalEnd`和正好16个真实anchors；第一格为段起点且是`hard`，首段事件为`first_frame`，后续段为边界切镜或`continuity_start`。硬锚点保留切镜、Hook、产品首次出现、关键状态、Before/After、Proof和CTA；软锚点覆盖动作开始/过程/结束；`context`只补时间覆盖。多人视频必须为跨镜头可识别人物建立稳定`source-creator-N`，原始anchors只写`sourceCreatorIds`，不得提前把原片人物冒充成用户Creator。

```text
python3 scripts/storyboard.py --video <benchmark> --timestamps-file <segment-anchors.json> --analysis-file <task>/analysis/benchmark-analysis.json --output-dir <task>/storyboards/original
```

输出固定为`segment-01-storyboard-4x4.png`等文件，并把metadata登记到manifest。所有画格必须直接来自原视频，禁止AI生成或重绘。

## Step 2：可选完整故事板局部替换

完整读取 [references/storyboard_editing.md](references/storyboard_editing.md)。

选择替换达人时同时完整读取 [references/creator_replacement.md](references/creator_replacement.md)。一张用户达人图固定只替换`primary`核心达人，其他原片人物保持不变；禁止把同一目标Creator复制给多个原片人物。无法可靠判断唯一核心达人时只做一次必要映射澄清。

- 产品和达人都沿用时，不调用图片编辑，把原Storyboard直接登记为最终Storyboard。
- 替换任一身份时，先从16个anchors建立正好16格的`segment-XX-replacement-map.json`，分出产品完整/局部替换格、达人完整/局部替换格和整格冻结格。
- 按Segment逐张编辑完整4×4原图，每张默认最多一次生成式Image Edit。默认先调用灵智CLI的`gpt-image-2`；只有CLI明确失败且没有待恢复任务时，才允许使用智能体自带图片编辑能力fallback一次。Image 1唯一决定对象是否存在、数量、位置、`full/partial`可见范围、大小、朝向、遮挡和接触关系；多视图产品和四视图达人只各代表一个身份。
- 整板编辑返回后，确定性执行`restore layout → lock-merge`；editable cells使用edited对应格，frozen cells强制恢复original对应格和原编号/时间标签/网格/分隔线。
- 只对editable cells做一次非生成式视觉检查，生成replacement audit并运行`validate-plan`。“分格”仅用于确定性处理和检查，禁止“逐格生成”。
- `replacementVerified=true`必须同时满足：整板Image Edit返回、Map有效、lock-merge成功、冻结格来自original、editable cells审计完成、`validate-plan.passed=true`。任一失败都停止Step 2，不自动第二次Image Edit，不得继续生成依赖错误Storyboard的Prompt或视频。

正式数据流固定为：`Replacement Map → 完整4×4 Image Edit一次 → restore layout → lock-merge → 冻结格原像素恢复 → 仅检查editable cells → validate-plan → replacementVerified → generation storyboard`。通过后才使用 [references/segment_storyboards.md](references/segment_storyboards.md) 生成段内时间Storyboard并登记到`storyboards.generation`。

跨产品结构不匹配时使用营销功能等价的状态映射，保留奖励次数、Proof密度和CTA位置；删除新产品无法成立的动作，不虚构功能。

## Step 3：上传视觉资料并调用服务端核心

完整读取 [references/server_prompt_generation.md](references/server_prompt_generation.md)。客户端已经在Step 1锁定Segment边界，服务端不得重新计算、增加、减少、合并或重新拆分Segment。

服务端工作流兼容旧客户端：`userConfig.visualContext.segments`存在且非空时走本Skill的新版Storyboard路径，缺失或为空时走旧版路径。本Skill不得利用兼容分支省略Storyboard。

客户端通过内置CLI依次上传原视频、每个Segment的一张`storyboards.generation`最终Storyboard、有效产品参考图和实际使用的Creator图。上传结果只作为本次工作流的多模态输入；不得把原始Storyboard或含旧产品的Storyboard作为最终生成参考。

CLI的`input`二级字段必须继续保持为：

```text
benchmarkVideoUrl
userConfig
productBrief
creatorBrief
```

不得新增`input.segmentPlan`、`input.storyboardImages`、`input.productImages`或`input.creatorImages`。新增的分镜与Segment资料统一写入`userConfig.visualContext.segments`；产品图URL写入`productBrief.productImageUrls`；达人图URL写入`creatorBrief.creators`。

```text
python3 scripts/server_prompt_workflow.py --manifest <manifest> --benchmark <benchmark-video>
```

该脚本必须先完成本地结构校验，再上传素材、调用`recreate-video-prompt`工作流、轮询原任务ID、保存返回的`videoPrompts`并调用`generation_manifest.py set-prompts`做第二次严格校验。取得任务ID后，网络或等待错误只能继续查询该任务ID，不得重复submit。

## Step 4：校验并展示 Video Prompt

服务端必须在同一次输出生成`adaptationPlan`、`qualitySpec`和最终`segments`。客户端不得再调用第二个GPT改写、补写或润色服务端结果。

确认单中的“其他复刻要求”写入`userConfig.customRequirement`。该字段非空时，必须原样纳入`adaptationPlan`并落实到相关Segment Prompt，作为相对对标视频的明确修改项；不得借此改变未获授权的产品或达人身份，也不得虚构产品功能。要求与最终Storyboard或已确认配置冲突时，在Step 4展示Prompt前明确报告冲突，不得自行取舍。

- 每个Segment严格引用一张`storyboards.generation`，使用`storyboardIds`，不得出现`renderUnitId`或`renderUnitIds`。
- Segment窗口沿用Step 1，Prompt时间从0重新开始且连续覆盖到`duration`。
- 每段按“参考素材职责→视觉和实体不变量→动作阶段→切镜→声音→最小生成约束”组织，使用3～5个宏观阶段。
- 每个时间阶段用正向陈述明确当前可见人物、产品与道具，特别写清产品首次出现前的无产品窗口、人物与产品接触关系及阶段结束状态；不在末尾重复成通用禁止项。
- Prompt末尾只保留字幕/模型生成水印与单画面连续视频约束；产品外观、旧产品和未确认功能约束按本段真实产品参考与替换状态条件注入。
- 前0.2秒硬锁第一格；真实硬切、Proof、CTA和关键产品状态使用准确时间。
- 只有本段实际包含目标产品或核心达人时才传对应身份图；`creatorIds: []`表示不传达人图。
- 保存前必须通过`scripts/prompt_preflight.py`的模型、最少Segment、全局/局部时间、Storyboard/Creator引用和Prompt内容校验。

`server_prompt_workflow.py`返回后必须已经完成保存与预检；不得绕过脚本直接信任工作流输出。通过后，把每个Segment的标题、时长和完整`prompt`正文原样展示给用户，不得只给摘要、文件链接或截断内容。随后明确询问：

> 以上是本次将提交的视频提示词。是否确认生成视频？请回复“确认生成”。

展示后停止等待。启动阶段的“确认开始”不能替代此处确认。只有用户在看到当前最新版全部Prompt后明确回复“确认生成”，才可进入Step 5；用户修改任何Prompt、Storyboard、产品/达人素材、模型、时长、语言或输出规格后，旧的生成确认立即失效，必须重新展示完整Prompt并再次确认。

## Step 5：生成、质检与交付

只有Step 4生成确认门已通过，才可完整读取 [references/generation_rules.md](references/generation_rules.md) 和 [references/quality_gates.md](references/quality_gates.md) 并提交。参考顺序固定为：本段最终Storyboard → 本段产品身份板/产品图 → 本段显式Creator图。禁止把原视频作为生成参考。

新产品模式必须至少有一张`product.productImages`；兼容读取旧字段`product.images`，新任务只写规范字段。引用审计必须在付费提交前阻止不存在的Storyboard、未完成替换的旧产品Storyboard、错误Creator引用、超出图片上限以及无产品Segment附带产品图。

```text
python3 scripts/run_generation.py --manifest <manifest> --video-provider auto --generation-approved [--segment-id <id> --skip-concat]
```

用户明确要求先生成部分Segment时，可重复传入`--segment-id`只提交指定段；完整Prompt计划仍须先通过预检。部分生成必须使用`--skip-concat`，不得把缺段候选拼接成完整成片。

首次成功生成`candidate-01`。技术质检后由Codex查看对齐图并评分；总分≥85且无硬失败才通过。失败时报告差异、归因、修复建议和服务实际费用，并完整读取 [references/skill_optimization.md](references/skill_optimization.md) 自动形成结构化技能优化方案。只有可跨任务复用且可归因到本技能的问题才能进入修改项；模型、渠道和当前任务特有问题必须列入排除项。用户看到完整方案并明确回复“确认优化 skill”后才可修改本技能，且该确认不授权创建新候选或重新提交。网络或等待错误只能轮询原任务ID，取得ID后不得切换渠道或重复submit。

## 永久禁用项

不得使用`shotContracts`、`renderUnits`、`renderUnitId(s)`、客户端核心母提示词、客户端二次GPT改写、自动Creator生成、根据Video Prompt派生Storyboard、自适应网格、自动重做Hook、独立营销分析步骤或自动质量重生成。不得修改、覆盖或删除`recreate-video-system-v3.1`。
