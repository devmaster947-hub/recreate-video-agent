# Step 2：生成专用段内 Storyboard

段内Storyboard是最终完整Storyboard的确定性重组，不是重新生成、重新选剧情或新增流程步骤。Step 2的AI编辑始终针对完整4×4原图；本文件中的分格只用于把已通过复核的最终画面转换成Segment局部时间参考。

## 不变量

- 每个Segment只使用一张4×4、16格生成Storyboard。
- 16格全部来自该Segment的全局时间窗，禁止混入未来或过去Segment画面。
- 第一格全局时间必须等于Segment `globalStart`，允许误差≤0.05秒。
- 确定性裁掉最终画格底部的原全局时间标签区，再写入局部时间：`localTimestamp = globalTimestamp - globalStart`，第一格必须显示`01 · 0.00s`；生成参考图不得同时保留全局和局部标签。
- 全局时间、局部时间、锚点角色和事件类型同时保存在metadata中；全局时间不烙入生成参考图。
- Segment之间通过相邻边界帧保持连续，不在同一张图中塞入段外“过渡帧”。上一段末帧和下一段首帧应尽量接近同一真实切点。
- 生成Storyboard只能使用已完成Step 2视觉复核的最终画格；没有替换素材时最终画格就是原始真实帧。

## 计划格式

```json
{
  "segments": [
    {
      "segmentId": 1,
      "globalStart": 0,
      "globalEnd": 12,
      "cells": [
        {
          "file": "<final-cell.png>",
          "globalTimestamp": 0,
          "anchorRole": "hard",
          "eventType": "first_frame",
          "productPresent": true,
          "productVisibility": "partial",
          "productCount": 1,
          "personPresent": true,
          "personExtent": "partial",
          "personCount": 1,
          "creatorIds": ["creator-1"],
          "interactionState": "单手遮挡瓶身下半部"
        }
      ]
    }
  ]
}
```

每个`cells`必须正好16项并按全局时间升序。运行：

`productPresent`、`productVisibility`、`productCount`、`personPresent`、`personExtent`、`personCount`、`creatorIds`和`interactionState`必须从最终Storyboard anchors原样传入段内Storyboard metadata。有替换时还必须传递完整`replacement`对象和由其验证结果得到的`replacementVerified`；确定性重组不得改写这些字段。

```text
python3 scripts/storyboard_cells.py split --board <final-segment-storyboard.png> --output-dir <task>/storyboards/final-cells/segment-01
python3 scripts/segment_storyboards.py --plan <segment-storyboard-plan.json> --output-dir <task>/storyboards/generation
python3 scripts/generation_manifest.py set-generation-storyboards --manifest <manifest> --metadata-file <task>/storyboards/generation/segment-storyboard-metadata.json
```

## Segment边界

使用模型允许的最少Segment数。总时长和单段时长为整数秒时，优先把边界吸附到附近真实硬切；必须同时满足单段时长限制、全局时间连续和完整覆盖。不得为了凑16格改变Segment数量。若某段重要状态不足16格，只在该段时间窗内补`context`锚点。

## 提交前检查

- `storyboards.generation[].segmentId`与`videoPrompts.segments[].segmentId`一一对应。
- `globalStart/globalEnd`完全一致。
- `localStart=0`，`localEnd=duration`。
- Prompt时间轴从0开始并以duration结束。
- 每段引用清单中只包含本段Storyboard、产品身份图和显式列出的Creator图。
