# Step 2：完整4×4 Storyboard单次替换与锁定合并

本步骤以启动确认单为准。产品和达人都沿用时，直接把原Storyboard登记为最终Storyboard，不调用图片编辑。用户确认替换任一身份时，对每个Segment的完整4×4原图最多执行一次生成式Image Edit。多张Storyboard必须逐张处理，禁止合并多个Segment。

用户选择替换达人时，先完整读取[creator_replacement.md](creator_replacement.md)，建立人物级映射后才能生成Replacement Map。单张达人图只替换`primary`核心达人，其他人物必须保持原样。

“分格”只用于Image Edit后的本地确定性crop、split、compose、lock-merge和视觉检查；严禁逐格调用任何生成式图片模型，严禁16次Image Edit。

## Replacement Map

Image Edit前，从本Segment的16个anchors建立`segment-XX-replacement-map.json`，必须正好包含格号01-16：

```json
{
  "segmentId": 1,
  "replaceProduct": true,
  "replaceCreator": true,
  "creatorReplacementMap": [],
  "cells": [
    {
      "index": 1,
      "product": {"state": "none", "count": 0, "replace": false},
      "creator": {"state": "full", "count": 1, "replace": true, "sourceCreatorIds": ["source-creator-1"]},
      "interactionState": ""
    }
  ]
}
```

`state`只能是`none | partial | full`，必须忠实复制anchor的存在、数量和可见范围。只替换用户已确认的对象类型。某格的`product.replace`或`creator.replace`为true时才是editable cell；两者都是false时是整格冻结格。

从Map明确导出并在提示词中分别列出：

```text
产品完整替换格：
产品局部替换格：

达人完整替换格：
达人局部替换格：

整格冻结格：
```

## 完整故事板Image Edit

Image 1始终是完整原始4×4 Storyboard，也是画布、对象是否存在、数量、位置、可见范围、大小、朝向、透视、遮挡和接触关系的唯一依据。产品参考和达人参考只负责身份，不得决定构图、动作、数量或新增内容。

- 多视图产品参考中所有视角都代表同一个产品，不代表Storyboard需要多个产品。
- 达人四视图都代表同一个人，不代表Storyboard需要多人。
- 产品`partial`格只替换原产品当前真实可见区域，禁止利用完整参考图补全原图不可见部分。
- 达人`partial`格只替换原图已存在的身体区域。只有手、手臂、半张脸、肩部等时保持相同可见范围，禁止补出脸、头部、躯干、完整人物或第二个人。
- 同一格原来有几个产品/人，替换后仍是几个。原图不存在的对象不得凭空新增。

提示词必须注入产品卡中已验证的名称、外观、颜色、Logo、结构和禁止变化项，或对应的达人身份特征。明确选择沿用的产品/人物身份保持原图不变。所有背景、镜头、动作阶段、光线、道具、网格、编号和时间标签都以Image 1为准。

## 图片编辑渠道

完整Storyboard Image Edit默认调用灵智CLI固定模型`gpt-image-2`。先把编辑Prompt保存为文件，再运行：

```text
python3 scripts/image_edit_workflow.py \
  --prompt-file <prompt.txt> \
  --reference-image <original-storyboard.png> \
  --reference-image <product-or-creator.png> \
  --aspect-ratio <与原Storyboard一致> \
  --output <edited.png> \
  --report <image-edit-report.json>
```

第一张参考图必须是完整原Storyboard，后续才是产品图和按映射实际需要的达人图。CLI成功时报告必须为`provider=lingzhi_cli`、`model=gpt-image-2`。

只有报告明确给出`fallbackAllowed=true`时，才允许使用智能体当前可用的图片编辑能力，并使用完全相同的Prompt与参考图。以下情况禁止fallback：

- 已取得灵智任务ID但轮询超时、网络中断或状态未知；使用`--resume-task-id`继续原任务。
- submit结果不明确，无法排除任务已经创建。
- CLI已经返回可用图片但视觉审计失败；这属于生成结果问题，不得自动换渠道重生成。

CLI未安装、API Key不可用、素材上传在创建任务前明确失败，或图片任务进入明确终止失败且无输出时，允许fallback一次。fallback只替代本次完整Storyboard Image Edit，不得增加逐格生成或自动生成缺失的产品图、达人图。

## 确定性 lock-merge

Image Edit返回完整图后，禁止追加逐格生成；允许且必须进行确定性分格处理：

```text
python3 scripts/storyboard_cells.py lock-merge \
  --original <original.png> \
  --edited <edited.png> \
  --plan <segment-XX-replacement-map.json> \
  --output <final.png>
```

`lock-merge`会把edited归一化到original尺寸/比例，分解两张整板，editable cells选用edited对应格，frozen cells强制选用original对应格，恢复原编号、全局时间标签、网格、分隔线和布局，输出完整4×4图及`.lock-merge.json` metadata。该命令不调用任何图片生成接口。

## 仅审计editable cells

冻结格已由程序恢复原像素，不做视觉审计。Codex直接查看原Storyboard、最终lock-merge Storyboard和Replacement Map，只对editable cells生成`segment-XX-replacement-audit.json`。

每个editable cell至少检查：

- 产品：存在、数量、`full/partial`、目标身份、旧产品残留、位置、大小和接触关系。
- 达人：人物存在、数量、`full/partial`、目标身份、旧达人残留、是否凭空补出身体、姿势和接触关系。
- 共同：`layoutChanged`、`backgroundChanged`、`contactChanged`。

审计是非生成式视觉检查，不得调用图片生成服务。然后必须执行：

```text
python3 scripts/storyboard_cells.py validate-plan \
  --plan <segment-XX-replacement-audit.json> \
  --output <segment-XX-replacement-validation.json>
```

`validate-plan`必须拦截对象存在/数量/局部程度、布局、背景、接触、旧产品/达人残留和目标身份错误。旧`identityMatch`仍兼容；新审计优先分别写`productIdentityMatch`、`creatorIdentityMatch`和`oldCreatorResidual`。

## replacementVerified和失败处理

`replacementVerified=true`只能在以下全部成立时写入：整板Image Edit成功返回；Replacement Map有效；lock-merge成功；冻结格已强制来自original；editable cells视觉审计完成；`validate-plan.passed=true`。

登记edited Storyboard时，通过`--replacement-file`传入replacement metadata。metadata至少记录：

```json
{
  "applied": true,
  "method": "whole-board-lock-merge",
  "generationAttempts": 1,
  "maxImageEditAttempts": 1,
  "imageEditSucceeded": true,
  "mapValid": true,
  "lockMergeSucceeded": true,
  "frozenCellsRestored": true,
  "editableCellsAudited": true,
  "validationPassed": true,
  "editableCells": [],
  "frozenCells": [],
  "editedImageFile": "",
  "mapFile": "",
  "lockMergeFile": "",
  "auditFile": "",
  "validationFile": "",
  "replacementVerified": true
}
```

默认`maxImageEditAttempts=1`。灵智CLI明确失败后使用智能体图片编辑能力属于同一次逻辑Image Edit的渠道fallback，必须在replacement metadata记录`primaryProvider`、`fallbackProvider`和CLI失败报告；不得再追加第三次生成。视觉审计失败时不自动调用第二次Image Edit；标记Step 2失败和`replacementVerified=false`，列出失败Segment、Cell及原因，停止生成依赖错误Storyboard的Video Prompt或视频。

通过后才登记为最终Storyboard，并按[segment_storyboards.md](segment_storyboards.md)生成仅用于视频模型的段内时间Storyboard。
