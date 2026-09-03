# Step 1：分段真实帧4×4 Storyboard

先运行`scripts/benchmark_analysis.py`读取媒体参数、候选切镜、黑场和音量信息。候选检测只辅助定位；Codex必须查看完整时间轴并决定真实切镜与锚点。

执行时遵循以下提示：

```text
请分析原视频，按生成模型的单段时长限制进行分段，再为每个Segment提取16个真实关键帧，分别拼成4×4十六宫格故事板。

【分段】

1. 读取`benchmark-analysis.json`中的`replicationWindow`，只在该区间内识别真实切镜；不得把区间后的画面纳入Storyboard。
2. 根据模型单段时长上下限，使用最少的Segment完整覆盖复刻区间：Seedance 2 Fast/Mini/2每段4～15秒；Seedance 2.5每段4～30秒；Minimax H3每段4～15秒；Grok Imagine 1.5 Preview每段1～15秒。
3. 分段边界优先吸附到附近的真实切镜，同时保证每段满足模型最短和最长时长。
4. 所有Segment必须时间连续、无重叠、无空档。
5. 确定分段时间窗后不得随意改变。

例如：30秒复刻区间使用Seedance 2 Fast时，应分成最少2段，每段各生成一张十六宫格，而不是整条视频只抽16帧。

【每段抽帧】

每个Segment必须先检查至少16个真实候选画面，再从自己的时间范围内选择正好16帧：

- 第一段第1格为原视频首帧。
- 后续段第1格为分段边界的真实切镜或连续性起点。
- 优先保留真实切镜、Hook、产品首次出现、关键产品状态、动作开始/过程/结束、Before、After、Proof和CTA。
- 所有画格必须来自原视频，禁止AI生成或重绘。
- 避免重复帧、黑场、转场重影和严重模糊。

【拼接】

每个Segment的16帧按照时间顺序，从左到右、从上到下拼成一张4×4图片。

- 每格尺寸一致并保持原视频比例，禁止拉伸。
- 使用细分隔线。
- 标注序号和全局时间，例如“01 · 15.00s”。
- 标签不得遮挡人物、产品或重要内容。
- 输出PNG并命名为`segment-01-storyboard-4x4.png`、`segment-02-storyboard-4x4.png`，按实际Segment数继续编号。
```

## Segment计划

模型能力以 [model_capabilities.md](model_capabilities.md) 和`scripts/model_capabilities.py`为准。使用最少Segment数；在合法时长区间内选择最接近真实切镜的整数秒边界。`source`模式和不足10秒的`opening_10`模式对非整数秒原片自动采用最接近的合法整数总时长，并记录调整量；不得为此增加确认门。复刻总时长小于所选模型最短时长时停止。

输入计划格式：

```json
{
  "segments": [
    {
      "segmentId": 1,
      "globalStart": 0,
      "globalEnd": 15,
      "anchors": [
        {
          "timestamp": 0,
          "shotId": "shot-1",
          "anchorRole": "hard",
          "eventType": "first_frame",
          "importance": 5,
          "productPresent": false,
          "productVisibility": "none",
          "productCount": 0,
          "personPresent": true,
          "personExtent": "full",
          "personCount": 1,
          "sourceCreatorIds": ["source-creator-1"],
          "creatorIds": [],
          "interactionState": ""
        }
      ]
    }
  ]
}
```

每个`anchors`必须正好16项并按全局时间升序。允许的`anchorRole`为`hard`、`soft`、`context`。允许的`eventType`为`first_frame`、`continuity_start`、`cut`、`hook`、`product_first`、`action_start`、`action_process`、`action_end`、`product_state`、`before`、`after`、`proof`、`cta`、`context`。

各Segment窗口必须从0开始连续覆盖目标时长。锚点必须落在本段窗口内；每段第一格必须等于`globalStart`，误差≤0.05秒。首段第一格固定`hard/first_frame`，后续段第一格固定`hard/cut`或`hard/continuity_start`。切镜锚点原则上距候选切镜≤0.25秒。

每个anchor必须同时记录下列Step 2判定metadata，不得在规范化时丢弃：

- `productPresent=false` 时必须是 `productVisibility=none`、`productCount=0`；存在时可见状态只能是 `partial`或`full`。
- 产品只露一部分、被手遮挡或只露边缘时必须记为`productVisibility=partial`，不得当成`full`。
- `personPresent=false` 时必须是 `personExtent=none`、`personCount=0`；只有手、手臂、局部脸、肩、腿或其他局部身体时必须记为`personExtent=partial`。
- `productCount`和`personCount`记录该格实际可确认的对象数。多视图身份参考不参与该计数。
- `sourceCreatorIds`仅列出本格实际存在且可识别的原片人物，跨镜头和Segment保持稳定编号；原始Storyboard的`creatorIds`保持为空。替换后`creatorIds`只列出本格实际使用的目标达人参考图。多人编号与映射规则见[creator_replacement.md](creator_replacement.md)。
- `interactionState`简要记录当前人、手、产品与场景的接触/遮挡关系。
- 模糊画格必须结合上一格、下一格和原视频连续性判断；仍无法确认时视为不存在，不得猜测。

脚本输出`storyboard-metadata.json`，其中保留Segment窗口、16个全局anchors和对应PNG路径。将每张原始Storyboard及其anchors登记到manifest。
