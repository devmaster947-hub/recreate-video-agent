# V7：先分析原视频，兼容V6旧任务

RecreateVideoPromptV3仅通过LuluLab task submit/fetch调用，现有主模型/兜底保持不变。本文件只规范拆解用途；LuluLab 图片工作流的按需配置及视频生成限制由`SKILL.md`、`storyboard_editing.md`和`generation_rules.md`规范，不会改变RecreateVideoPromptV3的输入与输出契约。V5.1新任务输入携带`plannerVersion=2`；旧任务携带`plannerVersion=1`、benchmarkVideo媒体对象及userConfig：model、newVideoDuration、targetCountry、targetLanguage、otherRequirements、targetDuration、blueprintSchemaVersion=7.0（旧任务6.0）、technicalCutCandidates，以及仅供Code节点使用的sourceDuration、targetDurationSource。不传rawStoryboards或替换参考图；sourceDuration/targetDurationSource不注入Gemini Prompt。

默认由客户端读取真实原始时长，按`durationMode=custom`裁剪原片前N秒，然后按需压缩、校验并上传；`source`保留全片，不受生成时长取整影响。`sourceDuration`保留原始真实时长，`targetDuration`保持现有复刻目标语义，实际分析时长仅记录为本地`analysisDuration`；过滤区间外候选切点。上传限制仍为20,000,000字节。

`--benchmark-url`先安全下载验证真实媒体，需要裁剪或压缩时上传预处理文件；仅无需处理的合规MP4可直接沿用URL。本地原片仍用于技术分析、Storyboard和最终对齐。下载限制：HTTP/HTTPS公共地址、IP固定连接及逐跳校验、最多3次重定向、512,000,000字节、120秒总时限和15秒单次网络/DNS时限。失败禁止submit。直链模式不得改变`mimeType=video/mp4`或单次提交规则。

新任务输出videoBlueprint.schemaVersion=7.0，保留基础信息、爆款逻辑、声音结构、逐镜头拆解、原产品信息、视频元素、爆款资产；增加节奏结构、candidateCutReview、reviewRequired。
真实shots含shotId/start/end/keyframes、表演变化、声音.utterances；节奏结构含cutPlan和dramaticBeats；声音结构含稳定voiceProfiles。时间均为全局数字秒，代表帧处于所属镜头内。跨镜头一句对白由相同lineId关联不重复文字片段；无法确认说话人停止而不猜测。

V2校验版本、时间连续性、镜头/人物/产品/对白引用；不可执行的数据返回明确错误。旁白允许不关联画面人物，沉默人物无声线，普通warning不阻断。reviewRequired只提示具体来源疑问，不一律中止；按entity_bindings.md利用已有原片材料核对，不自动重拆。
提交规则：排他锁→本地预处理与校验→preparing_submission→上传分析区间→submit→taskId原子receipt→manifest→fetch。首次任务明确进入`failed/failure/error/cancelled/timeout`终态时，客户端复用同一请求和已上传媒体自动重提一次；两次taskId按顺序原子追加到receipt和manifest attempts。第二次失败立即停止。鉴权/余额异常、CLI调用异常、本地轮询超时、未知状态、无taskId或submit结果不确定均禁止重提。后台session继续等待；有ID使用--resume-task-id恢复最新任务。旧结果不自动重拆。

工作流由 LuluLab CLI 提交任务后自动触发；客户端不配置、导入或发布工作流。

## 服务端确定性复刻规划

Gemini成功返回videoBlueprint后，同一工作流立即进入“生成复刻规划”Code节点。该节点不调用大模型、不产生额外Token；Planner Engine负责稳定算法，Policy集中保存模型能力、分段权重和anchor策略。客户端只消费结果，不保留上述规划算法。计划schema仍为1.0；新任务使用Planner V2及Blueprint 7.0，旧任务保留V1及6.0。V2新增完整镜头实体引用及真实shotId，仍为9格并沿用原分段引擎。

返回成功结果至少包含：
- `videoBlueprint`
- `replicationPlan.schemaVersion`
- `replicationPlan.plannerVersion`
- `replicationPlan.segments[]`（含globalStart/globalEnd/duration/anchors；layout不是服务端协议字段）
- `replicationPlan.blueprintWarnings`
- `replicationPlan.unresolvedCuts`



默认轮询间隔为5秒；这只减少任务完成后的等待，不改变服务端模型执行速度。

兼容规则：同一plannerVersion内只能做不破坏协议的参数调优；不得删除/改名字段，也不得改变已发布模型的anchor数量或合法Segment时长范围。出现这类变化时新增Planner V2，保留V1供已发出的Skill继续使用。
V2人物使用characterId，声源使用speakerId及可空characterId；每个镜头包含visibleCharacterIds和productIds，人物referenceTime提供源证据定位。详细字段和替换登记见entity_bindings.md。V2分析配置不把目标语言、目标产品或自定义替换要求注入原片事实；最终Prompt阶段再按用户要求适配。

## LuluLab CLI 0.0.2 文档差异

CLI命令已经核对附件文档及二进制help：`upload <file>`、`task submit --workflow-id --input`（或`--input-file`）、`task fetch --id`、`user --credits`。上传响应的 `expiresAt` 归一化为现有工作流媒体对象的 `expiredAt`。

附件文档的拆解样例使用 `RecreateVideoPromptV2`，键名为 `benchmarkVideoUrl`，包含 `productBrief`、`creatorBrief`。本Skill的V3还依赖 `videoBlueprint`、`replicationPlan` 与 Planner 2 输出；不能只改工作流ID就替换为V2。客户端保留 `RecreateVideoPromptV3` 及原输入契约，直接交由 CLI 自动触发工作流，读取返回的拆解和规划结果；不要求用户部署工作流。

Key仅保存在本机私有配置。用户确认LuluLab与原服务契约一致，图片与视频分别沿用原CLI中的ImageGenV2与VideoGenV2，见lululab_cli_contract.md。
