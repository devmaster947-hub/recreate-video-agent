# Step 5：生成、候选和恢复规则

## Provider与模型

运行`run_cli.py cli ensure-installed`和`run_cli.py video-provider detect`。模型能力以 [model_capabilities.md](model_capabilities.md) 及`scripts/model_capabilities.py`为准。新任务默认`seedance-2-fast`、720p、原片比例。

Seedance系列自动路由顺序为官方Dreamina CLI → 小云雀CLI → 灵智工坊。官方Dreamina和小云雀必须使用逻辑模型对应的精确VIP provider ID；CLI未声明该ID时该provider不可用，禁止静默改用同系列非VIP。小云雀当前只有provider骨架，自动路由必须跳过；显式选择时在任何上传或submit前报告“适配器尚未配置”。任务取得ID后provider锁定，网络错误和超时只能继续轮询该ID。

## 参考素材

每段顺序固定为：一张`storyboards.generation`最终Storyboard → 本段需要时的产品身份板/产品图 → 本段`creatorIds`显式指定的达人图。Prompt负责运动、声音和连续时间轴。禁止把原视频或含旧产品的原始Storyboard作为生成参考。

每个Segment的`storyboardIds`必须且只能含一个ID；该Storyboard的`segmentId/globalStart/globalEnd/localStart/localEnd`必须与Prompt完全一致。`creatorIds: []`严格表示不传达人图，缺失或空列表不得回退为全部达人。

`creatorIds`只包含该Segment中完成替换的目标`creator-N`。保留原样的`source-creator-N`记录在Storyboard metadata的`keptSourceCreatorIds`中，不上传外部达人图。单张达人图不得因为一个Segment内有多人而重复传递或用于多个身份。

新产品模式至少一张`product.productImages`。只有该Segment的Storyboard metadata显示存在目标产品时才附加产品身份图；沿用对标产品或无产品Segment均不传外部产品图。多张产品图在图片上限不足时确定性合成一张身份板，不得静默丢图。

官方Seedance最多9张图片。仍超限时明确失败，不得随意拆分已经锁定的Segment。灵智工坊首次上传本地素材前列出文件名和目的地，并取得聊天同意及系统授权；官方CLI可直接读取本地文件。小云雀适配器启用前不得上传任何素材。

提交前运行`scripts/reference_audit.py`，确认Storyboard存在、属于本段且已完成Step 2复核；新产品模式不得引用`storyboards.original`；产品与达人引用必须与每段metadata一致。

## 候选与费用边界

首次付费提交前，用户必须已经看到当前最新版所有Segment的完整视频Prompt，并明确回复“确认生成”。启动确认“确认开始”不能替代本确认。未确认时必须停止在Step 4；不得上传灵智素材或提交任何视频任务。

首次提交命令必须显式传入`--generation-approved`。脚本仅在需要创建新任务ID时检查该标志；已有任务ID的查询、下载和恢复不得要求重复确认，也不得借机重新submit。任何Prompt、Storyboard、产品/达人素材、模型、时长、语言或输出规格变更后，旧确认失效，必须重新展示Prompt并重新取得确认。

用户明确要求只生成部分Segment时，使用一个或多个`--segment-id <id>`选择提交范围，并同时传入`--skip-concat`。脚本仍先校验完整Segment计划，只对指定段执行引用审计、付费提交和下载；未选择的Segment不得产生任务ID或费用。后续补齐全部Segment后再拼接与整片质检。

第一次生成使用`candidate-01`，每个Segment在manifest保存attempts。任务ID、模型、分辨率、Prompt、输出、积分和错误全部保留；新候选不覆盖旧结果。

成功但质量未通过时不得自动重提。只有用户明确同意后才能传入`--new-candidate --quality-retry-approved`。服务终态失败也必须报告原任务和实际积分，不得把重复submit伪装成恢复。

## 生成后质检

完成拼接后自动运行本地技术质检并创建对齐图与draft质量报告。Codex按 [quality_gates.md](quality_gates.md) 完成语义评分；最终报告写入`finalVideo.qualityReport`。未评分或未通过时保持`step5_review_pending`，不会自动生成下一候选。

## Manifest

保持`version=4`，新任务使用`schemaRevision=5.4`。读取旧schema 5.0～5.3时，将非空`shotContracts`和`renderUnits`移入`migrationArchive`后从活动接口移除，并补充`creatorReplacementMap`；根据现有Storyboard恢复到最近可继续步骤。新任务和新写入不得出现`shotContracts`、`renderUnits`或任何`renderUnitId(s)`字段。
