---
name: recreate-video-system
description: 使用灵智工坊 LZStudio CLI 拆解并复刻 TikTok、抖音或其他爆款对标视频，并可通过官方 Seedance CLI 或 LZStudio CLI 生成视频。支持查询当前生效的灵智工坊 API Key、复用公网素材 URL、按需上传本地文件、分段视频提示词、达人参考图、自适应布局分镜图及 Seedance 2 / Google Omni / Grok Imagine 1.5 Preview 视频生成。用户需要查询灵智工坊 API Key、复刻爆款或对标视频、替换产品、生成视频提示词、达人图、分镜图或继续生成视频时使用。
---

# 爆款视频复刻系统

统一通过 `scripts/run_cli.py` 调用 CLI。拆解、上传和图片生成使用技能内置的 `lzstudio`；Seedance 2 视频生成阶段在内部选择已安装的官方 `dreamina` 或内置 `lzstudio`。保持既有的输入收集、`product_brief` 构建、原样展示、多 Segment、多达人、一次性后处理选择和最终拼接流程。不得安装 CLI。Prompt 默认锁定并使用服务返回的原文；只有用户明确要求修改 Prompt 时，才按用户指定的目标和内容建立显式覆盖，不改写已保存的服务原始结果。

## 运行边界

- 支持 macOS Apple Silicon（`arm64` / `aarch64`），只使用 `cli/macos-arm64/lzstudio`。
- 支持 Windows x64（`amd64` / `x86_64` / `x64`），只使用 `cli/windows-x64/lzstudio.exe`。
- 不支持 Intel Mac、Windows ARM 和 Linux；其他平台会提示“当前平台不支持，请使用 macOS Apple Silicon 或 Windows x64。”
- 内置 LZStudio CLI 不从 `PATH` 查找替代命令；官方渠道只查找用户已安装的 `dreamina`。不运行 Bash、PowerShell 或任何外部安装脚本。
- 所有调用统一经过 `scripts/run_cli.py`；该封装使用 `subprocess` 且不启动 shell，解析 JSON、保存灵智 API Key 并脱敏错误。
- Skill 启动时立即运行 `python3 scripts/run_cli.py video-provider detect`，只检查可执行文件，不登录、不联网、不消耗积分。
- 对每个素材按“`~/.recreate-video/config.json` 全局素材缓存 → 当前 manifest → 上传”的顺序解析；URL 未过期且剩余有效期至少 10 分钟时直接复用。缓存命中结果仍写入当前 manifest，后续任务只传公网 HTTP(S) URL。
- 对标拆解固定通过 `python3 scripts/run_cli.py recreate-video-prompt run --input-json <复刻输入.json> --output-root <输出目录>` 一次完成产品卡序列化、素材解析与上传、submit、manifest 记录、常驻 poll、结果保存和余额查询。进程中断后只运行 `recreate-video-prompt resume --manifest <manifest.json>`，不得重新上传或 submit。
- 每个任务成功 submit 一次后立即记录 ID，之后只 fetch 同一 ID。等待、`Created`、处理中、网络错误、本地轮询超时或其他 Segment 失败都不得触发重复 submit。唯一例外是跨多个 Segment 的达人图在服务明确返回终态失败后，按后处理规则自动重试一次，并把第二个 ID 作为独立尝试记录。
- 图片和视频生成默认采用批量并发：同一阶段所有依赖已满足的任务必须同时 submit，并同时 fetch/poll；不得逐个完成后再开始下一个，也不得只并发提交却串行轮询。只有任务间存在真实素材依赖时才分批，未就绪任务不得阻塞其他已就绪任务。并发任务的 manifest 写入必须由协调流程串行落盘，避免并发读改写覆盖。

## API Key

API Key 按以下优先级加载：函数参数 `api_key`、运行时环境变量 `RECREATE_VIDEO_API_KEY`、`~/.recreate-video/config.json`、用户输入。只要前三项已有非空 Key，后续任务直接复用，不得再向用户索取。

凡 API Key 缺失、为空或被服务明确判定失效而需要用户提供新 Key 时，必须在聊天中明确提示：“请前往灵智工坊 https://studio.lingzhiai.com.cn/ 获取 API Key，然后发送给我。”首次索取和失效后重新索取均不得省略该提示。

配置缺失且没有函数参数或运行时环境变量时，允许用户在聊天中发送一次 `灵智工坊 API Key`。收到后立即持久化：在 PTY 中运行 `python3 scripts/run_cli.py credential save`，通过该隐藏提示的标准输入传入 Key。API Key 不得写入 Skill 文件、Prompt、manifest、日志或持久化环境变量。运行时环境变量仅用于 Codex Sandbox 等隔离环境临时注入。除下述用户明确发起的查询流程外，不得把 Key 放入命令参数、临时文件或回复，不得回显、复述或确认 Key 的具体内容。保存成功只回复“API Key 已安全保存”，然后自动继续当前任务。

用户未在聊天中提供时，仍可由 `scripts/run_cli.py` 的隐藏提示收集。两种方式都保存到同一配置文件：

```json
{"apiKey":"","mediaCacheVersion":1,"mediaCache":{}}
```

macOS 将配置文件权限设为 `0600`。仅当 Key 缺失、为空或服务明确判定失效时重新索取；失效时覆盖保存新 Key，同样只需用户发送一次。

当用户明确要求“查询 / 查看 / 显示灵智工坊 API Key”时，运行 `python3 scripts/run_cli.py credential show`，并把返回的 `apiKey` 原样展示给用户，同时说明 `source`。该命令按运行时环境变量 `RECREATE_VIDEO_API_KEY`、配置文件的顺序返回当前生效的 Key，不弹出输入提示；未配置时直接告知用户尚未配置，并附上获取地址。API Key 属于敏感凭据：只有用户明确要求查询时才允许执行和展示；不得在普通复刻流程、状态更新、日志、错误信息或任务结果中主动显示，也不得把查询结果写入任何新文件。

## 积分展示

- 每次向用户展示提示词、图片、视频或最终结果的“本次积分消耗”时，必须紧接着展示“剩余积分”。
- 在任务到达成功或失败终态后再查询余额，确保展示的是任务结束后的余额。灵智工坊任务调用 `run_cli.get_remaining_credits("lingzhi_cli")`；官方 Seedance 视频任务调用 `run_cli.get_remaining_credits("official_cli")`。不得跨渠道混用余额。
- 固定使用两行格式：`本次积分消耗：<服务实际返回值>` 和 `剩余积分：<余额查询实际返回值>`。两项都不得估算，也不得用旧余额减去消耗量推算。
- 余额查询失败不得影响已完成任务或触发任务重提；展示 `剩余积分：未能获取（<脱敏错误>）`。任务未返回积分消耗时，不虚构消耗值，但如需展示剩余积分仍可单独查询。

## 复刻配置收集

用户发起复刻任务后，先收集完成任务所必需且尚缺失的配置。全部必需配置齐备后不得再展示创作配置确认摘要，也不得提供“确认并继续 / 修改配置 / 取消”等中间确认选项；如有需要上传的本地文件，直接按“上传本地文件”章节取得聊天中的明确编号同意，再主动发起系统安全授权。不得先尝试普通上传、等安全策略拦截后再申请。配置收集完成前，本地文件只可做存在性和类型检查。

首次收集时，以结构化形式列出以下必需配置项及所有可选值，不要只列出默认值：

1. **对标视频**：获取一个本地视频文件或视频 URL。
2. **基础配置**：
   - 视频模型：Seedance 2 Mini（默认）/ Seedance 2 Fast / Google Omni / Grok Imagine 1.5 Preview。
   - 视频时长：与原视频一致（默认）/ 15秒 / 30秒 / 45秒 / 60秒
   - 目标国家：无默认值，必须由用户明确指定。只有用户主动指定了默认国家时，才可将其作为默认值。
3. **产品**：
   - 上传产品图（默认）：提供至少一张产品图，用用户产品替换对标视频中的产品。
   - 使用对标视频产品：仅当用户明确选择此项时，沿用对标视频中的原产品。
4. **其他复刻要求**：无（默认）/ 用户提供

收集配置时尽量把同一问题的有限选项编号，允许用户只回复对应编号；目标国家、自由文本要求等无法穷举的字段仍接受自然语言。用户已在同一条消息中明确给出的配置不得重复询问。

`otherRequirements` 是完全由用户控制的原文透传字段。只有用户主动、明确提供了其他复刻要求时，才把用户原文逐字写入；用户选择“无”“默认配置”“按默认配置”或未提供时，必须写入空字符串 `""`。代理、脚本和服务不得推断、生成、改写、翻译、概括或追加任何内容。

用户未明确指定目标国家，且未主动指定可用的默认国家时，必须询问目标国家；不得根据视频内容、产品、语言、位置、历史任务或其他上下文推断，也不得把“按默认配置”解释为任何国家。产品配置默认选择“上传产品图”：用户未提供产品图且未明确选择“使用对标视频产品”时，必须请求用户上传至少一张产品图，不得自动沿用对标视频原产品。只有用户明确选择“使用对标视频产品”时，才进入沿用对标视频原产品的无图模式；不得根据文件名、上下文或代理推断新产品信息，也不得把用户文字描述转换成无图替换产品卡。不得把“是否授权上传”“是否提交任务”“是否消耗积分”等授权选项混入创作配置收集；全部必需配置齐备后，再单独请求本地素材上传同意和系统安全授权。

以下内部配置不得展示或询问：

- **产品卖点来源**：仅在用户提供产品图时适用；用户未主动提供卖点时按产品图保守生成，用户主动提供时按原文锁定。无图模式不收集或构建替换产品卖点。
- **达人配置**：默认使用 `creatorMode=auto`，由对标视频自动规划达人；仅当用户主动提供达人参考图时记录并使用。


维护以下输入对象；字段名不得改动：

```json
{
  "benchmarkVideo": {"filePath": "", "url": ""},
  "productBrief": {
    "productName": "",
    "sellingPoints": "",
    "targetCountry": "",
    "targetAudience": "",
    "productImages": []
  },
  "productAnalysis": {
    "productName": "",
    "appearance": "",
    "productColor": "",
    "material": "",
    "logo": "",
    "structure": "",
    "usage": "",
    "userSellingPoints": [],
    "productMaterialFacts": [],
    "aiSupplements": [],
    "forbiddenChanges": []
  },
  "userConfig": {
    "videoModel": "seedance-2-mini",
    "duration": 0,
    "targetCountry": "",
    "otherRequirements": ""
  },
  "creatorBrief": {
    "creatorMode": "auto",
    "creatorImages": [{"fileName": "", "filePath": "", "url": ""}]
  }
}
```

视频生成阶段只在内部解析一次生成渠道：Seedance 检测到官方 CLI 时使用官方渠道，否则使用灵智工坊；Google Omni 和 Grok Imagine 1.5 Preview 使用灵智工坊。该内部状态不得写入 `userConfig` 或提交给拆解服务。没有可用渠道时说明原因并停止生成。取得生成任务 ID 后不得切换渠道或重复提交。

收集期间保留上述字段名，但不将示例值当作已收集值。用户选择“与原视频一致”时固定提交 `duration: 0`，由服务端工作流还原真实时长；配置阶段不得调用 ffprobe、读取 MP4 元数据或用其他方式探测原片时长。只有对标视频超过 `20_000_000` 字节且必须压缩时，压缩器才可在内部读取时长计算码率，该值不得写入 `userConfig.duration`。接受原有自然名称并在提交时映射为 CLI 模型 ID：Seedance 2 Fast → `seedance-2-fast`，Seedance 2 Mini → `seedance-2-mini`，Google Omni → `google-omni`，Grok Imagine 1.5 Preview → `grok-imagine-1-5-preview`。自动设置 `creatorMode=auto`。目标国家同时写入 `productBrief.targetCountry` 和 `userConfig.targetCountry`；未取得明确国家前不得上传或提交。用户选择“上传产品图”时，必须根据至少一张产品图填充并提交完整 `productBrief`；用户主动提供的卖点优先，未提供时按产品图生成。只有用户明确选择“使用对标视频产品”时，才保持 `productBrief.productImages: []` 和空 `productAnalysis`，跳过产品卡构建，并固定提交非空 `productBrief`：`{"productName":"跟原视频产品一致"}`。冲突时只确认冲突本身，不重新询问上述自动配置。

统一入口读取旧版输入时，必须在构建请求前删除 `videoStyle`、`videoProvider`、`targetLanguage`、`resolution` 和 `aspectRatio`；这些字段不得出现在提交给灵智工坊的 `userConfig` 中。

## 构建 product_brief

每次复刻都读取 [references/product_brief_generation.md](references/product_brief_generation.md)：

1. 用户明确选择“使用对标视频产品”且 `productBrief.productImages` 为空时进入无图模式：跳过图片校验、视觉分析和产品卡构建，统一入口固定提交 `{"productName":"跟原视频产品一致"}`。不得提交空对象或 `null`，也不得仅因产品图尚未提供而进入无图模式。
2. 有产品图时运行 `scripts/validate_product_images.py` 校验全部产品图，再逐张完成本地视觉分析。
3. 把逐图视觉分析写入统一输入的 `productAnalysis`。统一 `run` 入口使用 `scripts/build_product_brief.py` 对应的 `core/product_builder.py` 逻辑构建完整产品卡，并在同一步完成校验和序列化；该工作与素材解析和上传并发进行。
4. 用户提供的卖点原文锁定，其他字段以图片可验证事实为主；不得虚构功效、参数、认证或承诺。构建成功后直接继续，不单独展示或确认中间产品识别结果。

## 上传本地文件

创作配置收集完成后，直接汇总本次确实需要上传的本地对标视频、产品图和达人参考图，并在聊天中逐项列出文件名和目标第三方“灵智工坊”。在首次调用统一 `run` 入口、`run_cli.upload_file()`、任何底层上传命令或系统安全授权之前，必须先主动取得用户的明确聊天同意；必须在首次上传前单独、主动发起一次系统安全授权，不得先尝试普通上传、等安全策略拦截后再申请。

聊天确认固定提供编号选项：

1. 同意将以上素材上传到灵智工坊并继续
2. 暂不上传

明确提示用户只需回复 `1` 或 `2`，不得要求用户输入“同意上传到灵智工坊”等固定长句。只有用户回复 `1` 或以其他自然语言明确同意上传这些已列出的文件后，才以 `sandbox_permissions=require_escalated` 主动发起系统安全授权；系统授权必须紧接聊天同意，不得先运行普通权限上传。用户可见的 `justification` 使用友好提示，并明确本次文件名：

> 为了开始复刻，需要将你确认的素材安全上传到灵智工坊，用于拆解和生成。是否允许我继续？

一次授权覆盖本次已汇总的全部本地素材；授权通过后只调用一次统一 `run` 入口，同批素材不得重复询问。若用户拒绝授权，立即停止上传和后续远程任务，保留已收集的创作配置并说明可随时继续。若用户之后新增本地素材，只对新增素材在首次上传前再次主动发起系统安全授权。已有公网 HTTP(S) URL 不属于本地文件上传，无需请求该授权。

统一 `run` 入口内部按以下顺序解析缓存并上传：

1. 本地对标视频必须调用 `run_cli.upload_file(path, benchmark=True)` 并回填 `benchmarkVideo.url`。
2. 如有产品图，逐张调用 `run_cli.upload_file(path)`，上传全部产品图并回填 `productBrief.productImages[].url`；无图模式跳过本步骤。
3. 对用户提供的达人参考图逐张调用 `run_cli.upload_file(path)`，并回填 `creatorBrief.creatorImages[].url`。

任何用户提供的本地原视频、产品图或达人参考图都必须在上传前检查实际文件字节数。文件大于 `20_000_000` 字节时，`upload_file()` 必须调用 `scripts/compress_benchmark_video.py` 生成不覆盖用户原文件的压缩副本：视频输出 MP4，图片输出 JPEG；只有压缩副本实际大小不超过 `20_000_000` 字节才允许上传。文件恰好等于限制时可直接上传。压缩失败、格式不受支持或压缩后仍超限时必须停止，不得回退上传原文件，也不得绕过统一上传入口直接调用底层 CLI。

保留 `fileName` 供确认；已有公网 HTTP(S) URL 不重复上传。全局缓存保存在既有 `~/.recreate-video/config.json` 的 `mediaCache` 中，按用户原文件内容 SHA-256 复用，自动清除过期项并最多保留 256 条；保存 API Key 时必须合并写入，不得清空缓存。素材不超限时直接上传原文件；超限时仅上传压缩副本，不改写输入对象中的用户原文件路径。每个缓存命中或上传成功结果都立即写入当前 manifest，把本地原素材与返回的 `url`、`mimeType`、`expiredAt` 关联。上传结果保持：

```json
{"url":"","mimeType":"","expiredAt":""}
```

进入图片或视频生成阶段前，对每个 Segment 调用 `generation_manifest.py references`。只上传 `upload_required` 中的文件，上传后刷新其公网媒体记录并重新运行 `references`；最终只使用重新解析得到的完整有序 `reference_urls`。下载生成结果不会使其原始公网 URL 失效，不得仅因已经保存为本地文件而重新上传。

TikTok/TK 或抖音页面链接、短链可先运行 `scripts/resolve_benchmark_video.py "<URL>" --json` 轻量解析媒体 URL；解析失败时请用户提供本地视频或公网媒体 URL，不下载页面视频。

## 生成复刻提示词

读取 [references/video_analysis.md](references/video_analysis.md)。上传授权通过后调用一次统一入口：

```text
python3 scripts/run_cli.py recreate-video-prompt run --input-json <复刻输入.json> --output-root output/recreate-video
```

统一入口内部只调用一次 `submit_recreate_video_prompt()`，底层固定使用 `lzstudio recreate-video-prompt submit`。产品卡序列化与素材解析/上传并发完成，任一失败都不得 submit。

提交结构固定为：

```json
{
  "benchmarkVideoUrl": "",
  "userConfig": {},
  "productBrief": {},
  "creatorBrief": {}
}
```

有产品图时提交完整 `productBrief` 对象；无图模式固定提交 `{"productName":"跟原视频产品一致"}`。所有模式都必须传入非空 `--product-brief`，不得提交空对象、`null` 或省略该参数。提交前必须核对 `userConfig.otherRequirements`：其值只能是用户本次主动提供的原文，或空字符串。若无法在用户消息中找到完全对应的原文，必须重置为空字符串后再提交；不得以任何理由自动补充。

保存 submit 返回的 `id` 到 manifest 后才允许第一次 fetch。统一入口作为常驻进程每隔 20 秒只查询该 ID；`Pending`、`Processing`、`Running`、`Queued` 和临时网络错误继续，`Succeeded` 成功，`Failed` 停止并展示脱敏错误。成功后立即原样保存 `prompts/recreate-prompt-result.json`，再查询灵智工坊余额并更新 manifest；余额失败不改变任务成功状态。进程中断或本地超时后只运行 `python3 scripts/run_cli.py recreate-video-prompt resume --manifest <manifest.json>`，不得重新上传或 submit。

成功后只接受并原样保留：

```json
{
  "videoPrompts": {
    "summary": "",
    "segments": [{"segmentId":1,"title":"","duration":"","prompt":""}]
  },
  "creatorPrompts": {
    "summary": "",
    "creators": [{"creatorId":"","role":"","appearsInSegments":[],"consistencyReason":"","prompt":""}]
  }
}
```

按返回顺序展示摘要、每个 Segment 和非空达人方案。Segment 标题使用 `Segment <segmentId>｜<title>`，完整 Prompt 放入独立 `text` 代码块。服务返回的 Prompt 原始字符串必须原样保存。后续生成默认锁定并使用该原文，不得由代理主动改写、概括、翻译、省略或补充。只有用户明确要求修改时，才按下方“用户明确的 Prompt 修改”规则建立独立覆盖。

用户可见的 Prompt 只生成一个排版副本，并按以下规则换行：

- 只允许插入换行符，不得增加、删除、替换或重排任何其他字符；标点、引号、数字、大小写和字段顺序全部保持原样。
- Segment Prompt 在画面规格总述以及 `人物：`、`产品：`、`初始状态：`、`时间轴：`、`声音：`、`口播`、`达人声音：`、`禁止` 等现有字段边界前换行。
- `时间轴：` 内每个已有时间段说明单独成行；只在原文已有的分号、句号等边界后插入换行，并保留原标点。
- Creator Prompt 按人物外观、服装、固定配饰或特征、姿态、背景和禁止项等现有句子边界换行。
- 无法可靠判断边界时保留该段原样，不为追求换行而改动文字。排版副本只用于聊天展示，严禁传给任何生成任务。

达人数组为空时完全省略达人方案和达人图选项。

## 后处理与生成

读取 [references/generation_rules.md](references/generation_rules.md)，复用统一入口已经初始化的 `output/recreate-video/<taskId>/manifest.json`。状态顺序固定为 `creator_images_pending`、`storyboards_pending`、`videos_pending`、`complete`。

用户给出图片计划和视频计划后，禁止临时编写后处理脚本、手工拆读结果 JSON 或逐项调用图片/视频函数。立即把选择映射为下列固定参数，并只启动一次技能内置的一键后处理入口：

```text
python3 scripts/run_generation.py --manifest <manifest.json> --image-plan <all|creator-only|storyboard-only|skip> --generate-video <yes|no> [--video-provider <auto|official_cli|lingzhi_cli>] [--segment-id <ID> ...]
```

- “达人图和分镜图” → `--image-plan all`
- “仅达人图” → `--image-plan creator-only`
- “仅分镜图” → `--image-plan storyboard-only`
- “全部跳过” → `--image-plan skip`
- 继续生成视频 → `--generate-video yes`
- 暂不生成视频 → `--generate-video no`
- 视频渠道默认 `--video-provider auto`；只有用户明确指定官方 Seedance CLI 或灵智工坊 CLI 时，才分别使用 `official_cli` 或 `lingzhi_cli`。
- 默认生成全部 Segment；只有用户明确指定部分 Segment 时才重复 `--segment-id`，并且只提交选中的 Segment。

该入口直接从 manifest 的 `prompt_task.result_file` 读取拆解结果，自动兼容顶层结果和 `output` 包装，不得在运行前另做结果结构探测。入口负责并发提交、串行记录任务 ID、下载、manifest 更新、余额查询和最终拼接；启动后只轮询同一进程并向用户展示阶段进度。若进程中断，使用同一条命令和同一 manifest 恢复，入口必须复用已记录的成功产物和任务 ID，禁止重复提交。

### 用户明确的 Prompt 修改

Prompt 修改默认关闭。只有用户明确要求修改 Prompt 时才启用，并且只作用于用户明确指定的目标：

- `creator-images`：达人图 Prompt。
- `storyboards`：分镜图的派生 Prompt；不改写原 Segment Prompt 和时间轴。
- `videos`：视频 Prompt。

用户提供了确切文本时，将原文保存为 UTF-8 文件，不得改写。根据用户明确说明选择 `append`、`prepend` 或 `replace`；未指定时，对“增加 / 加上”使用 `append`。然后在任何目标任务 submit 之前运行：

```text
python3 scripts/generation_manifest.py set-prompt-override --manifest <manifest.json> --target <creator-images|storyboards|videos> --id <Creator ID 或 Segment ID> --mode <append|prepend|replace> --text-file <用户原文.txt>
```

多个 ID 重复 `--id`。不得直接修改 `recreate-prompt-result.json`；原始服务 Prompt 始终保留，覆盖记录写入 manifest。不得将某一目标的修改自动扩散到其他目标。目标任务一旦取得 submit ID，禁止中途修改该目标 Prompt。

若已有视频任务全部终态失败，且用户随后明确要求改用另一视频模型或渠道，不得覆盖原 Segment 的任务 ID。先创建独立的视频尝试 manifest，复用原 Prompt、产品图、达人图、分镜图及其有效公网 URL，再用一键入口只生成视频：

```text
python3 scripts/generation_manifest.py clone-video-attempt --source-manifest <原 manifest.json> --task-id <新本地任务 ID> --model <新模型 ID> --output-root <输出目录>
python3 scripts/run_generation.py --manifest <新 manifest.json> --image-plan skip --generate-video yes [--video-provider <auto|official_cli|lingzhi_cli>] [--segment-id <ID> ...]
```

新尝试必须保留 `source_task_id` 和 `source_manifest`，Google Omni 与 Grok Imagine 固定解析为灵智工坊渠道；Seedance 仍按官方 CLI 优先规则解析。不得重新生成或重新上传已有且公网 URL 仍有效的图片素材。

结果展示后只询问一次图片计划：有 Creator 时提供“达人图和分镜图 / 仅达人图 / 仅分镜图 / 全部跳过”，无 Creator 时提供“分镜图 / 全部跳过”。同时记录是否计划继续生成视频，后续不重复询问图片计划。

- 达人图：把所有 Creator 的 `generate_creator_image()` 作为同一并发批次同时启动；该函数按 `appearsInSegments` 判断是否允许一次终态失败重试，并通过 `on_submit` 在每次提交成功后立即分别记录 ID。下载成功后把 poll 返回的公网媒体元数据关联到本地达人图，并立即用绝对本地路径 Markdown `![Creator <creatorId> 达人图](<绝对本地路径>)` 向用户展示。
- 跨多个 Segment 的达人图两次均终态失败时，显示 Creator ID、角色、脱敏错误和全部受影响 Segment，暂停这些 Segment 的分镜与视频。允许其他不依赖该 Creator 的任务继续；等待用户上传达人参考图后注册并恢复，或由用户明确选择缺少该达人图时继续。
- 分镜图采用完整填充的方阵布局：1 镜使用 1×1；2～4 镜使用 2×2 并生成 4 个完整关键帧；5 镜及以上使用 3×3 并生成 9 个完整关键帧。
- 明确镜头少于画格数量时，从现有镜头的开始、过程、后期、操作特写、结束或结果状态中提取额外关键帧，不得留空，也不得新增剧情、人物、产品、功能、场景或结果。
- 超过 9 镜时不报错、不分张，仍使用单张 3×3，并从完整时间轴中确定性选择 9 个代表镜头。
- 禁止空白画格、黑白占位画格、1×3 或 3×1 狭长布局、分屏、画中画，以及把多个镜头放入同一画格。不得随机选择、重复或重排镜头。原 Segment 视频提示词和时间轴默认锁定；用户明确要求修改分镜图 Prompt 时，仅修改派生的分镜 Prompt，不改写原 Segment 原文。所有达人依赖已满足的 Segment 必须同时提交并同时查询，只延后尚缺依赖的 Segment；下载成功后把 poll 返回的公网媒体元数据关联到本地分镜图。
- 达人图和分镜图固定使用 GPT Image 2（`gpt-image-2`）。该模型是不可配置的内部常量：不得向用户展示图片模型选项，不得接受用户、代理、输入文件、旧 manifest 或调用参数修改，也不得降级或切换到其他图片模型。
- 图片底层只使用 `lzstudio image submit --model gpt-image-2` 和 `lzstudio image fetch`；引用素材只传 upload 返回的 URL。
- 每个 Segment 只调用统一的 `generate_video()`；无论 `seedance-2-fast`、`seedance-2-mini`、`google-omni` 还是 `grok-imagine-1-5-preview`，所有引用已准备好的 Segment 必须作为同一批次并发生成。若 provider 返回明确的并发/限流错误，只对未取得任务 ID 的提交按服务要求退避后继续，不得把整批永久降级为逐个串行。
- `generate_video()` 根据内部选定的生成渠道调用官方 `dreamina text2video/multimodal2video + query_result` 或 `lzstudio video submit/fetch`。官方 CLI 传入已有绝对本地参考图路径，LZStudio 传入 manifest 解析的公网 URL。
- 两种 provider 都只向后续流程返回 `{"video": {...}或null, "credits": 0, "errorMessage": ""}`。`credits` 只使用 provider 实际返回值，缺失时为 `0`；后续下载、拼接、展示、manifest 和回调不得再分支判断 provider。
- 图片或视频 fetch 成功后使用 `download_media()` 保存到对应本地目录；该函数自动使用 `-v2`、`-v3` 后缀，禁止覆盖旧产物。
- 达人图或分镜图下载为非空文件后，必须向用户展示图片：达人图按 Creator 返回顺序显示，分镜图按 `segmentId` 顺序显示，并使用 `![Segment <segmentId> 分镜图](<绝对本地路径>)`。展示只供用户查看；不得调用 `view_image` 或其他图片分析能力，不得由代理检查人物、产品、文字、水印或分镜布局，也不得因视觉效果重新提交、排除或替换已成功产物。
- 视频任务返回成功且已下载为非空文件后，直接记录成功并继续，不抽帧、播放、解码或检查画面连续性、音频或成片质量。
- 所有 Segment 成功后按时间线运行 `scripts/concat_videos.py` 生成 `videos/final.mp4`；任一段失败不得生成伪完整视频。

图片与视频媒体结果保持：

```json
{"url":"","mimeType":"image/png 或 video/mp4","expiredAt":""}
```

## 最终输出

```json
{
  "success": true,
  "result": {
    "videos": [{"segmentId":1,"url":"","mimeType":"video/mp4","expiredAt":""}],
    "creatorImages": [{"creatorId":"","url":"","mimeType":"image/png","expiredAt":""}],
    "videoPrompts": {},
    "creatorReferencePlan": {}
  },
  "creditsConsumed": 0
}
```

`creditsConsumed` 只使用服务返回值，缺失时为 `0`，不得估算。每次展示该值时，按“积分展示”规则查询并紧接着展示任务结束后的剩余积分。保留全部 Segment 和 Creator，不按完成顺序重排。

## 输出目录

```text
output/recreate-video/<taskId>/
├── creators/
├── storyboards/
├── videos/
│   └── final.mp4
└── manifest.json
```

不得覆盖同名旧产物；使用 `-v2`、`-v3` 版本后缀。最终显示存在的本地 `final.mp4` 可点击路径。
