# 单一Storyboard的清理与替换

优先用当前智能体的原生生图/图片编辑能力；只有当前会话没有该能力时，才用`scripts/lingzhi_image_generate.py`的`gpt-image-2`、`1K`兜底。原生能力存在但单次失败时不自动切换。Planner V1/V2每段为3×3、9个anchors；抽帧图只是暂存，最终只保留一张rawStoryboard/edited Storyboard。

## 默认快速路径

1. 依据蓝图visibleText决定是否去字，并将去字和产品/达人替换合并到一次原生图片编辑。
2. 只生成一次整板候选图；不做视觉质检，不因质量问题自动重做。
3. 不建Replacement Map，不运行`validate-map`、`preview-mask`或`lock-merge`。
4. 运行：
```text
python3 scripts/storyboard_cells.py fast-prepare --original <rawStoryboard> --edited <candidate> --output <finalStoryboard>
```
5. `fast-prepare`只执行机械门禁：文件可读、比例偏移不超过15%、3×3网格可识别、恢复原时间标签与原输出尺寸。
6. 用`method=whole-board-fast-v1`、`validationLevel=mechanical`登记edited。`replacementVerified`只是文件、SHA、布局和引用完整性，不是视觉质量声明。

## 显式严格路径

只有用户明确说“严格复刻”或要求逐像素保护时使用：建立`object-regions-v1` Replacement Map，运行`validate-map`和`lock-merge`，要求`protectedPixelsRestored=true`且`outsideMaskChangedPixels=0`，最后执行一次视觉质检。区域冲突时停止，不扩大mask绕过protect。

最终板复用原9个anchors。每个personPresent=true的anchor必须写完creatorIds。原片帧不得直接登记为Creator图。用户提供达人图则直接登记；无用户图的多Segment任务才生成无产品三视图人物板（正面+单侧面+背面），单Segment不额外生成人物参考图。
V7在编辑前按entity_bindings.md登记轻量replacementBindings，并将每格的来源人物/产品与目标映射传入图片编辑指令。它不是像素Replacement Map；fast仍不做编辑后视觉质检。原片核对在首次编辑前的分镜理解步骤完成，有疑问才补看相关原片帧。需要多视图时先锁定目标身份再编辑各板，不新增人物图策略或生成后质检。
