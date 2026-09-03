# 多达人识别与替换

仅在用户明确选择“替换为新达人”时读取。用户只是上传达人图但未选择替换时，继续沿用对标视频人物，不登记Creator，也不调用图片编辑。

## 1. 原片人物编号

查看原视频和所有Segment Storyboard，对跨镜头出现的同一可识别人物使用稳定编号：`source-creator-1`、`source-creator-2`。每个原片人物只能有一个编号，不得因景别、服装状态、表情或Segment变化重新编号。

为每个人物标记一个角色：

- `primary`：承担主要Hook、口播、产品演示、Proof或CTA的核心达人；整条视频必须且只能有一个。
- `supporting`：有明确剧情或口播作用的配角。
- `background`：可识别但不承担主要营销功能的人物。

只有手、背影、严重遮挡、远景或无法跨镜头确认身份的人，不创建`sourceCreatorId`，但仍计入`personCount`并保持原样。

原始anchors使用`sourceCreatorIds`记录本格实际可识别的原片人物。`creatorIds`在原始Storyboard阶段保持为空；它只记录完成替换后需要外部达人参考图的目标Creator。

## 2. 默认映射

一张用户达人图时，固定执行：

```json
{
  "creatorReplacementMap": [
    {
      "sourceCreatorId": "source-creator-1",
      "role": "primary",
      "action": "replace",
      "targetCreatorId": "creator-1"
    },
    {
      "sourceCreatorId": "source-creator-2",
      "role": "supporting",
      "action": "keep"
    }
  ]
}
```

即：只替换核心达人，其他配角、路人和未识别人物保持原样。禁止把`creator-1`同时映射给多个原片人物，禁止生成多个长相相同的人。

有多张达人图时，每张图登记为独立`creator-N`，再按人物一对一映射。目标Creator不得重复使用。用户要求替换所有人物但图片数量不足时停止，请用户补充达人图或明确只替换哪一个人；不得复制一张脸补齐人数。

无法可靠判断唯一核心达人时，只问一次：

> 对标视频中有多个主要人物。请确认这张达人图要替换哪一位：①…；②…。其他人物将保持原样。

这属于必要映射澄清，不是新增确认门。能够按Hook、口播、产品演示、Proof或CTA明确判断核心达人时，不询问。

## 3. 登记与校验

先登记每张用户达人图，再登记映射：

```text
python3 scripts/generation_manifest.py add-creator \
  --manifest <manifest> \
  --creator-id creator-1 \
  --image <creator-image>

python3 scripts/generation_manifest.py set-creator-replacement-map \
  --manifest <manifest> \
  --file <creator-replacement-map.json>
```

映射文件必须覆盖所有已编号的`sourceCreatorId`。每项只能是`replace`或`keep`；`replace`必须引用已登记的`creator-N`，`keep`不得带`targetCreatorId`。

## 4. 逐格替换

每个Segment的Replacement Map顶层复制同一份`creatorReplacementMap`。每格`creator.sourceCreatorIds`只列出本格真实可识别的人物：

```json
{
  "creator": {
    "state": "full",
    "count": 2,
    "replace": true,
    "sourceCreatorIds": ["source-creator-1", "source-creator-2"]
  }
}
```

脚本根据全局映射自动推导：

- `replacedSourceCreatorIds`
- `keptSourceCreatorIds`
- `targetCreatorIds`

`creator.replace=true`只表示该格至少包含一个需要替换的人，不代表格内所有人物都替换。图片编辑Prompt必须逐名写清“谁替换、谁保持”，禁止使用“替换画面中的达人”“把所有人物换成参考人物”等泛化表达。

编辑后的anchors同时保存：

- `sourceCreatorIds`：本格原片人物身份。
- `creatorIds`：本格实际替换后的目标`creator-N`，用于发送参考图。
- `keptSourceCreatorIds`：本格保持原样的原片人物。

## 5. 审计

editable cell审计除人数、可见范围和旧达人残留外，还必须记录：

```json
{
  "expectedReplacedSourceCreatorIds": ["source-creator-1"],
  "replacedSourceCreatorIds": ["source-creator-1"],
  "expectedKeptSourceCreatorIds": ["source-creator-2"],
  "keptSourceCreatorIds": ["source-creator-2"],
  "expectedTargetCreatorIds": ["creator-1"],
  "editedTargetCreatorIds": ["creator-1"]
}
```

任一目标人物错换、保留人物被改动、目标Creator复用、人物数量变化或出现旧达人残留，`validate-plan`必须失败，停止后续Prompt和视频生成。
