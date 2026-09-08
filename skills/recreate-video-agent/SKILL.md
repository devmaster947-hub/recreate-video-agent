---
name: recreate-video-agent
description: 通过统一《复刻要求确认单》选择启动配置，使用灵智工坊CLI并行生成替换故事板与分段视频，经用户检查故事板和确认视频提示词后，高保真复刻 TikTok、抖音等产品视频；质检失败后生成可确认的通用技能优化方案。不用于从文字重做 Storyboard 或自由创作 Hook。
---

# recreate-video-agent v4.3

## 执行效率与授权交互

- 新任务的本地准备与Storyboard提交以10分钟为交互上限：目标是3分钟内完成分析与原板、5分钟内完成区域预览、最迟在第6分钟并行提交图片任务。外部服务未在剩余时间内完成时，保留原任务ID并返回`processing`，不阻塞超过10分钟、不重复提交。这是交互时间上限，不虚假保证外部生成已完成。
- “只换包装/产品，其他不变”默认进入快速路径：一次查看全时间轴和全部原板，只放大真正含替换目标且边界不清的格；不对无包装的产品本体、购物袋、展示盘或空容器建立替换区域。
- 本轮用户直接要求修改本技能时，该请求即授权所要求的技能修改；不要套用Step 5质检失败后自动提案的额外确认门。技能修改不授权上传素材或生成媒体。
- 已读且未变化的说明、已检查的图片和已完成的分析直接复用。恢复时只读取manifest、当前步骤所需资料与变化字段；不要每轮重新通读整个技能或脚本源码。正常调用先用命令帮助和已有示例，报错时再定位实现。
- 独立的本地读取、技术检查、候选帧提取按批并行；先总览真实时间轴，只放大不清楚的关键格。元数据纠正不改变时间戳或画布时只更新记录，不重新解码视频或重画原板。
- Step 2使用批量准备入口，把紧凑的逐格对象轮廓一次转换为Map、蒙版预览和jobs；不由模型反复展开整份JSON。预览必须查看，但不变的预览可复用；只修正有证据的错误格。仍保留逐格遮挡、区域外像素验证、原任务ID恢复和最终视觉检查。
- 区域图一次写入、一次生成所有蒙版预览，一次视觉检查所有预览；最多允许1轮有证据的本地蒙版修正。修正不改变已选帧时，禁止重抽帧、重登记整份metadata或重画原板。
- 上传/积分授权按[启动与授权交互](references/start_confirmation_format.md)明确服务名称、素材和操作，用交互选项“1. 同意 / 2. 不同意”。同意仅覆盖该问题列出的范围；预选项、等待超时和未回复都不是授权。明确授权后在相同范围内不重复追问；不承诺绕过系统审批。Step 3本地校验完成后最多6路并行上传原视频与两组Storyboard；缓存锁只保护读写，不覆盖网络传输，失败恢复只补缺失上传。

正式流程固定为五步：

1. 按模型时长限制生成每段一张真实帧3×3 Storyboard。
2. 可选地通过灵智工坊CLI并行编辑完整Storyboard，再由用户检查确认。
3. 把对标视频、完整用户配置、产品/达人信息及各自图片、成对的`rawStoryboards`/`targetStoryboards`交给灵智工坊CLI“复刻爆款提示词v2”，由工作流完成Gemini拆解与GPT重构。
4. 当前Codex校验、登记并完整展示工作流返回的Video Prompt。
5. 通过灵智工坊CLI并行生成全部Segment视频、质检与交付。

启动前必须通过统一《复刻要求确认单》完成一次启动确认。该确认在同一个交互步骤中收集产品、达人、视频模型、复刻时长、目标国家/语言和其他配置，同时授权Step 2所需的灵智图片生成、Step 3所需的原视频上传及流程执行到Step 4。存在替换Storyboard时，必须展示全部最终Storyboard并取得“确认故事板”后继续；Step 4完成后必须展示完整视频提示词并取得“确认生成”，才可进入付费Step 5。除这两个确认门外，不加入其他常规确认。新任务默认上传产品图、沿用对标视频达人、使用`Seedance 2 Fast`、复刻时长与原视频一致、目标国家/语言与原视频一致，其他复刻要求为“无”；图片和视频生成provider默认`lingzhi_cli`，原比例、720p、`strict`质量档和高保真模式作为内部固定默认值执行，不在确认单中展示。事实分工固定为：最终Storyboard和anchors负责静态视觉与实体位置，工作流内Gemini基于原视频和rawStoryboard拆解动态与声音，GPT基于blueprint、targetStoryboard与产品/达人brief重构提示词；产品/达人图只锁定身份。Codex负责本地预检、展示和质量判断。

## 启动前确认门

本节“确认开始”也接受用户在已明确列出配置、上传目的地、素材和费用的启动交互中选择“1. 同意”；只针对同一启动问题，不把其他问题的同意当作启动或视频生成确认。

从对话和附件提取已提供信息后，首次响应必须直接展示统一《复刻要求确认单》，不得先单独询问产品或达人。用户已经在当前请求中明确表达的选择直接预填；未明确的项目直接采用确认单中的默认值，不显示“待选择”。

展示前完整读取 [references/start_confirmation_format.md](references/start_confirmation_format.md)，使用其中的单表卡片和固定操作区。只把示例值替换为当前任务的真实文件名、已选配置和素材状态。确认单不得展示输出规格、复刻模式、必须保留项、禁止项、候选产品图、候选达人图或单独的产品卖点字段。用户提供的产品卖点或希望相对对标视频修改的内容，原样记入“其他复刻要求”；没有时显示“无”。

默认产品选项是“上传产品图”。启动前必须至少收到一张产品参考图；若用户未上传，只要求其补充产品图，或明确改选“使用对标视频中的原产品”。默认达人选项是“沿用对标视频达人”；选择“替换为新达人”时，启动前必须收到达人参考图。达人附件本身不改变默认选择，除非用户明确选择替换。不得自动生成新达人。

自定义时长必须填写正整数秒，含义固定为复刻原视频`0～N秒`，不得压缩整片，也不得超过原片实际时长。原片不足10秒时，“仅开场10秒”等同复刻完整原片。选择“自定义语言”时必须给出具体语言；未给出时只补问语言，不得启动。

确认单末尾使用 [references/start_confirmation_format.md](references/start_confirmation_format.md) 的“下一步”操作区，要求用户用一条回复完成选择和授权。接受全部默认项时，要求用户附上至少一张产品图并回复“确认开始”；改用原产品时，推荐格式为：

> 产品=原产品；确认开始

用户也可以在同一条回复中覆盖任意默认项，例如：

> 产品=上传；达人=替换；模型=Seedance 2；时长=10秒；语言=英语；其他复刻要求=<原文>；确认开始

“产品=上传/原产品”分别对应“上传产品图/使用对标视频中的原产品”；“达人=替换/默认”分别对应“替换为新达人/沿用对标视频达人”；“模型=默认”表示`Seedance 2 Fast`；“时长=默认”表示与原视频一致；“语言=默认”表示与原视频一致。用户此前已经明确表达且在最新版确认单中预填的配置无需重复，只需满足所选素材条件并明确回复“确认开始”。

只有在用户看到最新版统一确认单、所有自定义值完整、所选上传或替换项具备对应参考图，并明确回复“确认开始”后才可执行。若默认上传产品图但图片缺失、替换达人但图片缺失，或自定义语言/时长缺少具体值，保留其他已选配置，只补问缺失内容并要求再次确认，不得初始化任务、分析视频或上传素材。

复刻时长与原视频一致或原片不足10秒时，源时长不是整数秒不得增加二次时长确认；启动后由技术分析自动采用最接近的合法整数秒并继续。分析结果必须记录`durationMode`、`requestedDuration`、原始时长、目标时长、`replicationWindow`和调整量。自定义时长超过原片、或复刻总时长短于所选模型最短时长时必须停止，不得静默延长。任何用户主动配置改变都会使旧确认失效；自动时长归一化不视为配置改变，无需再次确认。

确认前只能整理输入和检查附件路径；不得初始化任务、分析视频、抽帧、编辑图片、上传素材或调用任何生成服务。确认后运行：

```text
python3 scripts/generation_manifest.py init --task-id <taskId> --output-root <output-root> --video-model <videoModel> --duration-mode <source|opening_10|custom> [--target-duration <customSeconds>] [--target-country <country>] [--target-language <language>] [--custom-requirement <text>]
python3 scripts/benchmark_analysis.py --video <benchmark> --model <videoModel> --duration-mode <source|opening_10|custom> [--target-duration <customSeconds>] --output <task>/analysis/benchmark-analysis.json
python3 scripts/generation_manifest.py set-benchmark-analysis --manifest <manifest> --file <task>/analysis/benchmark-analysis.json
```

有产品图时按需读取 [references/product_brief_generation.md](references/product_brief_generation.md)，不得虚构卖点、功能或功效。

## Step 1：分段真实帧 Storyboard

完整读取 [references/storyboard_extraction.md](references/storyboard_extraction.md)。只分析`replicationWindow`覆盖的原视频区间和其中的真实切镜，再按模型能力确定最少Segment；边界优先吸附到合法范围内的真实切镜，确定后不得漂移。

每段必须有连续的`globalStart/globalEnd`和正好9个真实anchors；第一格为段起点且是`hard`，首段事件为`first_frame`，后续段为边界切镜或`continuity_start`。硬锚点保留切镜、Hook、产品首次出现、关键状态、Before/After、Proof和CTA；软锚点覆盖动作开始/过程/结束；`context`只补时间覆盖。

```text
python3 scripts/storyboard.py --video <benchmark> --timestamps-file <segment-anchors.json> --analysis-file <task>/analysis/benchmark-analysis.json --output-dir <task>/storyboards/original
```

输出固定为`segment-01-storyboard-3x3.png`等文件，并把metadata登记到manifest。所有画格必须直接来自原视频，禁止AI生成或重绘。

## Step 2：可选完整故事板局部替换

完整读取 [references/storyboard_editing.md](references/storyboard_editing.md)。

- 产品和达人都沿用时，不调用图片编辑，把原Storyboard直接登记为最终Storyboard。
- 替换任一身份时，从9个原始anchors建立完整Map，原样保留存在、数量、可见范围及interactionState。新任务使用`object-regions-v1`：逐格标注产品/达人允许变化的多边形，以及前景手指、道具等protect区域；先查看本地蒙版预览。
- 用`scripts/run_storyboard_edits.py`并行提交全部Segment的完整3×3 Image Edit，每张默认最多一次生成。参考图只锁定身份；原始板唯一决定构图、数量、状态和接触关系。画布比例按含标签的原板尺寸计算，只补外边，默认2K，不能套用视频比例。
- `run_storyboard_edits.py`默认8秒轮询、最多360秒并行等待；超时返回`processing`并保留任务ID，不视为生成失败。使用`--submit-only`可在全部新任务ID落盘后立即返回；恢复时重运同一命令但不需再传授权标志。
- 返回后检查画布比例并恢复为原Storyboard尺寸；逐格去除生成图自带的时间条，只从original恢复时间标签和格线；完整Image Edit结果作为新的最终Storyboard，不再按对象区域蒙版进行像素拼接，也不因非目标区域漂移指标中止。
- Codex查看原板/生成板/最终板对比，核对身份、数量、可见范围、状态、动作阶段、遮挡和人物一致性。明显错误先指出并处理，不自动追加生成。不使用旧replacement audit或validate-plan作为通过依据。
- 展示全部原板与新的最终板供用户检查，用户明确确认后才登记。新模式`replacementVerified=true`必须同时满足Image Edit完成、Map有效、画布和标签恢复成功、证据哈希一致、用户确认的成品哈希有效。
- 已有ID仅恢复查询；已完成且未改变的产物保留确认。旧Map可读和恢复旧任务，不允许新提交；升级不能重置已有任务ID或触发重复扣费。

数据流固定为：`原始anchors → 对象区域Map与预览 → 加外边原板 → 并行整板Image Edit → 画布与标签恢复 → 完整前后对比检查 → 用户确认 → generation storyboard`。对象区域Map用于约束生成提示词与检查重点，不作为返回图的裁切蒙版。用户确认新的完整Storyboard后运行`generation_manifest.py confirm-storyboards --user-confirmed`并直接继续；通过后按 [references/segment_storyboards.md](references/segment_storyboards.md) 生成段内时间Storyboard。只改时间标签，保留逐格原始时间、实体状态和接触信息。
跨产品结构不匹配时使用营销功能等价的状态映射，保留奖励次数、Proof密度和CTA位置；删除新产品无法成立的动作，不虚构功能。

## Step 3：灵智CLI“复刻爆款提示词v2”

完整读取 [references/gemini_video_analysis.md](references/gemini_video_analysis.md)。使用`scripts/gemini_cli_analysis.py`（保留历史文件名）调用`recreate-video-prompt submit/fetch`。Gemini拆解与GPT重构都在工作流内完成，不再调用“任意任务”，不传`--workflow-id`或`--input`。

提交内容为：对标视频、`productBrief`（含`productImages`）、`creatorBrief`（含`creatorImages`）和完整`userConfig`。`userConfig`必须包含`model`、`newVideoDuration`、`targetCountry`、`targetLanguage`、`otherRequirements`、`rawStoryboards`和`targetStoryboards`，全部从已确认的manifest真实值确定，不得漏传或另行改写。两组Storyboard始终为数组，即使只有一张也不改类型；每个数组元素用`file`包装HTTP媒体对象，并在同一元素保留Segment ID、Storyboard ID和全局时间窗。两组按Segment顺序一一配对。raw来自原始真实帧板；target来自已经确认的最终板的段内时间版本，与Step 5使用的板一致。未替换身份时target复用原画面，仅允许时间标签变化。

所有媒体经CLI上传后使用HTTP(S)地址。任一原板、目标板或已选择的身份图缺失/上传失败，提交前停止，不得漏图继续。模型、时长、国家、语言和其他要求必须按确认单与manifest写进`userConfig`；产品与达人约束分别保留在对应brief。anchors、Map、蒙版、分析提示词和已有blueprint留在本地，不另行加入此次CLI请求；Storyboard条目的ID和时间窗是配对元数据。服务输出若与本地确认配置冲突，在Step 4报告，不能静默改变用户配置。

```text
python3 scripts/gemini_cli_analysis.py --manifest <manifest> --benchmark <benchmark-video> [--product-brief <product-brief.json>] [--creator-brief <creator-brief.json>]
python3 scripts/gemini_cli_analysis.py --manifest <manifest> --resume-task-id <task-id>
```

取得ID后先落盘，等待中断只用同一ID恢复fetch，不重复提交。工作流必须返回非空`videoPrompts.segments`，同时保留原始响应和可用的blueprint；服务没有返回提示词时报告错误，不能改为本地GPT重构。终态失败保留原始响应、任务ID和错误；不得把“缺少必要参考职责或生成约束”解释为自动重提许可。

## Step 4：校验并展示工作流 Video Prompt

完整读取 [references/video_prompt_generation.md](references/video_prompt_generation.md)。以工作流返回的`videoPrompts`为提示词来源；本地只做格式适配、引用和时间校验、必要的质量登记，不重新执行Gemini→GPT。每个Segment严格沿用Step 1窗口和一张`storyboards.generation`。无对应产品/达人画面时不附加该身份图。

先保留原始返回，再验证`segments`，保存前通过`scripts/prompt_preflight.py`。`adaptationPlan`和`qualitySpec`不是视频生成输入，仅在服务实际返回时作为可选元数据保存，缺失不阻塞。服务响应的字段命名可做确定性适配。只有缺少固定参考职责或最小生成约束时，`set-prompts`按[固定约束补齐与失败恢复](references/prompt_constraint_repair.md)执行一次确定性补齐：保留原文、另存修复版和逐段变更记录，再跑完整预检；不调用模型、不重新submit。缺少实质内容、时间不匹配、遗漏用户要求或与目标Storyboard冲突时报告具体差异，不能通过本地重写来掩盖失败。`customRequirement`非空时仍须在返回提示词正文中落实，否则暂停并报告本地要求与工作流输出不一致。

```text
python3 scripts/generation_manifest.py set-prompts --manifest <manifest> --file <task>/analysis/workflow-video-prompts.json
```

登记若生成修复版，后续展示和提交以manifest中`videoPrompts.file`为准，并说明补齐项；原始工作流文件仍保留。补齐不表示已完成人工语义/视觉质检，也不授予视频生成确认。

保存并通过预检后，完整读取 [references/prompt_display_format.md](references/prompt_display_format.md)，按其中的“生成概览 → Segment卡片 → 提交确认”顺序展示。必须把每个Segment的标题、时长和完整`prompt`正文逐字展示给用户，不得只给摘要、文件链接或截断内容。聊天展示可增加标题、表格、字段标签和分隔线，但这些导航元素不属于Prompt，不得写回`video-prompts.json`、manifest或提交给视频模型。末尾必须使用该规范的“提交确认”固定操作区，明确要求用户回复“确认生成”。

展示后停止等待。启动阶段的“确认开始”不能替代此处确认。只有用户在看到当前最新版全部Prompt后明确回复“确认生成”，才可进入Step 5；用户修改任何Prompt、Storyboard、产品/达人素材、模型、时长、语言或输出规格后，旧的生成确认立即失效，必须重新展示完整Prompt并再次确认。

## Step 5：生成、质检与交付

只有Step 4生成确认门已通过，才可完整读取 [references/generation_rules.md](references/generation_rules.md) 和 [references/quality_gates.md](references/quality_gates.md) 并提交。参考顺序固定为：本段最终Storyboard → 本段产品身份板/产品图 → 本段显式Creator图。禁止把原视频作为生成参考。

新产品模式必须至少有一张`product.productImages`；兼容读取旧字段`product.images`，新任务只写规范字段。引用审计必须在付费提交前阻止不存在的Storyboard、未完成替换的旧产品Storyboard、错误Creator引用、超出图片上限以及无产品Segment附带产品图。

```text
python3 scripts/run_generation.py --manifest <manifest> --generation-approved [--segment-id <id> --skip-concat]
```

用户明确要求先生成部分Segment时，可重复传入`--segment-id`只提交指定段；完整Prompt计划仍须先通过预检。部分生成必须使用`--skip-concat`，不得把缺段候选拼接成完整成片。

默认provider固定为灵智工坊CLI。完整生成时先并行提交所有尚无任务ID的Segment并逐个立即登记任务ID，全部提交完成后再并行轮询和下载；恢复任务只轮询原ID。首次成功生成`candidate-01`。技术质检后由Codex查看对齐图并评分；总分≥85且无硬失败才通过。失败时报告差异、归因、修复建议和服务实际费用，并完整读取 [references/skill_optimization.md](references/skill_optimization.md) 自动形成结构化技能优化方案。只有可跨任务复用且可归因到本技能的问题才能进入修改项；模型、渠道和当前任务特有问题必须列入排除项。用户看到完整方案并明确回复“确认优化 skill”后才可修改本技能，且该确认不授权创建新候选或重新提交。网络或等待错误只能轮询原任务ID，取得ID后不得切换渠道或重复submit。

## 永久禁用项

不得使用`shotContracts`、`renderUnits`、`renderUnitId(s)`、“任意任务”提交复刻提示词、绕过指定CLI直接调用Gemini或GPT、在工作流外重新执行提示词重构、自动Creator生成、根据Video Prompt派生Storyboard、改变3×3固定网格的数量或布局、自动重做Hook、独立营销分析步骤或自动质量重生成。旧4×4任务保留产物和任务ID，需用升级前备份版本恢复，不得强行按九宫格读取或自动重新付费提交。不得修改、覆盖或删除`recreate-video-system-v3.1`。
