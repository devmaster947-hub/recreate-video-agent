# Step 2：完整3×3 Storyboard替换与对象区域保护

沿用启动配置。产品和达人都沿用时，直接使用原Storyboard。替换任一身份时，每个Segment最多一次整板Image Edit，使用灵智工坊CLI并行提交和轮询。局部分区只用于本地像素合成，禁止逐格调用生成模型。重复生成需用户另行授权。

## 1. 从原图建立替换范围

### 批量准备（默认）

先看整张原板，只有轮廓或遮挡不清楚的格才额外放大。一次写入紧凑的`annotations.json`，使用每格视觉区的原始像素坐标，省去模型手算归一化和重复复制9格metadata。脚本直接继承已登记原板的存在、数量、可见范围及接触关系，不负责自动猜轮廓。

```json
{"referenceFiles":["<产品图绝对路径>","<达人图绝对路径>"],"segments":[{"segmentId":1,"replaceProduct":true,"replaceCreator":false,"coordinateSpace":"pixels","prompt":"<完整整板编辑提示词>","cells":[{"index":1,"regions":{"product":[[[72,149],[144,149],[144,320],[72,320]]],"protect":[]}}]}]}
```

示例坐标仅说明格式，不可直接复用为实际轮廓。坐标基准为原板每格视觉区，不含时间标签；默认标签38px，特殊板显式传`labelHeight`。`coordinateSpace`也可为`normalized`。每格必须为实际待替换对象提供对应轮廓；确无目标格可省略。局部保留某角色时以`creatorReplace:false`关闭该格人物替换，不能改变原始人数。`productReplace`同理。

对“只换包装，其他不变”使用快速标注：先区分包装、裸露产品、购物袋、展示容器和空容器，只给真实包装建区域。同格多个小包装用一组多边形一次标注，不为每个物体单独生成任务。字幕、顶部标题、前景手指和遮挡道具直接写入`protect`，不再创建第二份审计文件。

```text
python3 scripts/prepare_storyboard_edits.py --manifest <manifest> --annotations <annotations.json> --output-dir <task>/storyboards/edited
```

一次本地执行生成所有段的Map、蒙版预览和jobs；独立预览并行生成，内容哈希未变且预览未被修改时直接复用。查看返回的全部预览后使用下文runner提交。只有元数据文字纠正且选帧未变时，同步原始metadata及manifest，不重抽帧。已存在图片生成任务记录时本工具拒绝重建输入，使用原jobs恢复，避免影响任务指纹或重复扣费。

以下单段模板入口仍可用于局部调试；常规流程不逐段手动重复执行。

先查看每张原始Storyboard，再逐格读取原始anchors；原图决定构图，anchors记录时间和实体状态。两者有冲突时先回查真实视频帧，纠正原始记录，不得沿用生成图的错误反改原始事实。

用原始metadata生成模板：

```text
python3 scripts/storyboard_regions.py map-template \
  --metadata <original-storyboard-metadata.json> --segment-id 1 \
  --replace-product --output <segment-01-replacement-map.json>
```

只替换达人时用`--replace-creator`；同时替换时两个开关都传。模板忠实复制存在、数量、full/partial和interactionState，区域默认为空，尚不能提交。新任务必须使用`object-regions-v1`，不能只标记editable整格。

Map必须包含01～9格。以下展示一个格子的结构，其余格子按各自原图填写：

```json
{
  "segmentId": 1,
  "mergeMode": "object-regions-v1",
  "coordinateSpace": "original-cell-visual-normalized",
  "labelHeight": 38,
  "replaceProduct": true,
  "replaceCreator": false,
  "cells": [
    {
      "index": 1,
      "product": {"state": "partial", "count": 1, "replace": true},
      "creator": {"state": "partial", "count": 1, "replace": false},
      "interactionState": "held",
      "regions": {
        "product": [[[0.30,0.35],[0.60,0.35],[0.60,0.75],[0.30,0.75]]],
        "creator": [],
        "protect": [[[0.30,0.62],[0.40,0.62],[0.40,0.75],[0.30,0.75]]]
      }
    }
  ]
}
```

上面的矩形仅解释格式，不能复用为所有格子的真实区域。坐标以每格原始视觉区为基准：左上角(0,0)、右下角(1,1)，不含底部时间标签；labelHeight必须匹配原板实际值。每个区域是由至少三个点组成的简单多边形，一个对象类型允许多个不相连区域。轮廓应沿真实边缘取点，不画自交多边形。脚本验证字段、有限坐标、面积和目标类型；轮廓是否贴合对象仍需看图检查。

- `product`和`creator`只填写已授权替换类型的区域；该格对应replace=false时必须为空。
- 有效编辑区为product与creator区域的并集，再减去protect。protect优先，包括需要保留的前景手指、头发、道具、未替换的产品等。
- 区域覆盖旧对象轮廓与合理的新对象轮廓所需的最小范围。形状改变时可包含紧邻的必要背景修复区，不能任意扩张到整格；整格蒙版会被拒绝。不要用统一百分比外扩所有对象。
- 瓶体和瓶盖、包装与配件、分离的肢体等使用多个独立区域。保留原图对象数量，多个部件不是多个完整产品；parts状态与整体数量分别描述。
- 原图只露出局部时，保留原遮挡和出画边界。禁止为了让参考图完整出现而补出完整产品、面部或身体。
- 替换产品时保持人物；替换人物时保持产品和非目标道具。两者同时替换时按原遮挡前后关系分别定义，不能覆盖整个人物外接矩形中的背景。
- 所有无目标格完全冻结。有目标格内部的背景、姿势和非目标实体也必须受保护，不能因“每格都有产品”而让整板全部重绘。

记录原始anchors的interactionState原文。不得把“握持、旋转、打开、涂抹”等逐格信息统一覆盖为通用描述。runner会在上传前逐格核验存在、数量、可见范围和interactionState。

本地输出蒙版预览并查看每格边缘与遮挡：

```text
python3 scripts/storyboard_regions.py preview-mask \
  --original <original.png> --plan <replacement-map.json> --output <mask-preview.png>
```

红色区域才允许变化。预览属于执行检查，无需增设用户确认门。没有可靠轮廓时先修正区域；不要直接使用大矩形兜底。

## 2. 输入画布与编辑提示词

Image 1为原始3×3板加外边后的完整画布。它唯一决定格序、时间、视角、构图、姿势、接触关系、数量和可见范围。身份图只决定外观，不决定场景与动作。多视图均表示同一个身份。

图片比例依据**故事板自身宽高（包括标签）**计算，不能套用视频9:16。runner在CLI支持的1:1、3:4、4:3、9:16、16:9中选择最近比例，仅在外侧补灰边；原板内部不拉伸、不裁切、不改变网格。默认2K，保留完整画布；不在jobs中用视频比例覆盖。返回图仅按整个加边画布恢复尺寸，再去掉已知外边，不按生成内容自动裁切单格。

提示词写清：
1. Image 1为唯一构图依据，保留全画布与外边、3×3格序、所有时间条。
2. 按Map列出产品/达人完整替换格、局部替换格、冻结格；说明每格需保留的动作阶段和前景遮挡。
3. 注入已验证的身份、结构、颜色及Logo；替换身份后保持原位置、相对尺度、朝向和可见比例。
4. 参考图不允许增加对象、恢复不可见身体/部件、改变背景或重新设计广告分镜。

runner还会注入固定画布约束，不能用“重新创作、更美观、更高级、参考原风格”等开放描述代替局部编辑要求。

先检查身份参考对关键状态的覆盖：闭合、打开、拆分、佩戴、操作等不能相互冒充。若关键结构只能猜测，先利用现有资料确认；仍无法确定且影响复刻时只补充询问必要视图或状态资料。不得凭单张正面图虚构内部结构，也不能把不确定状态改成另一套镜头。跨品类确需功能等价映射时，在对应格明确最小适配及依据，保持时间、Proof和CTA位置；涉及用户未授权的动作/场景变化时说明冲突。

## 3. 提交与恢复

```json
{
  "jobs": [
    {
      "segmentId": 1,
      "original": "storyboards/original/segment-01-storyboard-3x3.png",
      "replacementMap": "storyboards/edited/segment-01-replacement-map.json",
      "prompt": "<完整整板编辑Prompt>",
      "referenceFiles": ["<产品图或达人图>"]
    }
  ]
}
```

jobs中的相对路径以jobs文件目录为基准。original必须是manifest登记的原板。

```text
python3 scripts/run_storyboard_edits.py \
  --manifest <manifest> --jobs <jobs.json> --image-generation-approved
```

快速交互模式在全部新任务ID落盘后立即返回：

```text
python3 scripts/run_storyboard_edits.py \
  --manifest <manifest> --jobs <jobs.json> --image-generation-approved --submit-only
```

普通模式默认每8秒轮询，所有Segment并行等待最多360秒。超时任务保持`querying`，输出`processing`和原任务ID；后续重运同一jobs命令仅fetch原ID，不重传、不重提交、不重复扣费。

先提交所有新Segment，再轮询。每个任务ID立即保存，每张完成图立即保存状态，不因其他段失败而丢失。已有任务只恢复原ID；输入改变、成品缺失、失败状态或文件被改动时停止，不自动再次扣费。已确认成品恢复时保留确认，不反复要求确认同一张图。

旧版Map和整格lock-merge只用于读取旧产物和恢复已有ID，不能新提交。升级不修改旧任务文件，也不把旧版证据冒充区域保护证据。迁移旧任务应保留原版，重新构建区域Map；不能修改已有提交的输入指纹来强行恢复。

## 4. 完整新故事板恢复和用户检查

runner自动调用。对象区域Map继续约束生成提示词、身份范围和检查重点，但不再把返回图按蒙版裁切后拼回原板。

新模式按以下顺序处理：
1. 检查返回画布比例，偏差超过1.5%时停止，因为无法可靠恢复九宫格几何。
2. 按原始加边方式恢复完整画布到原Storyboard尺寸，不自动识别单格边界，不裁切或拼接局部生成内容。
3. 只从original恢复九格时间标签和格线，完整保留生成图的视觉内容作为待确认新Storyboard。
4. 生成原板/对齐生成板/最终板三联`.comparison.png`和`.review.json`；记录原图、返回图、Map和成品SHA-256、画布恢复状态与漂移指标。

Codex必须查看完整三联图，逐格核对目标身份、数量、full/partial、动作阶段、前后遮挡、人物身份和背景连续性。漂移指标只提示重点检查格，不设置额外技术确认门。发现明显错误应在展示时记录Segment/格号和差异；需要重新生成时取得额外授权。用户确认当前完整最终Storyboard后直接继续后续复刻。

## 5. 用户检查与后续数据

展示每个Segment的原板和最终板，或清晰可读的前后对比；最终板必须以内联图片展示。说明待检查的目标区域，必要时放大关键格。全部通过执行检查后再让用户确认，不能只展示孤立成品让用户自行回忆原图。

> 请检查以上全部最终Storyboard。若可继续，请回复“确认故事板”；如需调整，请指出Segment和格号。

用户明确认可后运行：

```text
python3 scripts/generation_manifest.py confirm-storyboards --manifest <manifest> --user-confirmed
```

支持`--storyboard-id <id>`仅确认已认可的段。新模式的replacementVerified需要：Image Edit完成、Map有效、画布尺寸和原时间标签恢复成功、证据哈希一致、用户确认当前完整成品。缺少任何条件都不进入依赖它的Prompt和视频生成。

用户确认后按[segment_storyboards.md](segment_storyboards.md)生成段内时间板。后续只改变时间标签，逐格继承原始anchors的身份存在、数量、可见范围、接触动作及时间；身份替换相关信息单独记录，不得把整段同一份通用anchor复制9次。静态身份以已确认成品为准，不能再从文本重新生成一套Storyboard。
