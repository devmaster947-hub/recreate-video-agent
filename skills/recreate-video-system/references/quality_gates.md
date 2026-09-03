# Step 5：严格复刻质量门槛

`run_generation.py` 成功下载并拼接后调用 `quality_review.py analyze`，生成技术指标、候选切镜和原片/复刻片对齐图。技术脚本不冒充语义判断；Codex必须查看对齐图并提供评分JSON，再调用 `quality_review.py score`。

评分上限：首帧与Hook 15、镜头顺序及切镜20、产品身份/尺寸/位置/交互25、关键状态及奖励密度20、动作连续性10、声音/口播/节奏10。总分≥85且无硬失败才通过。

硬失败：产品错误或缺失、无产品镜头新增产品、首镜头结构错误、关键场景/硬切缺失、Proof或CTA缺失、核心人物严重漂移、错误文字、非预期水印、虚构功能，以及技术报告中的时长/比例/音轨硬错误。

归因：

- 尺寸、构图、产品有无错误 → 返回Step 2。
- 时间、切镜、声音或运动错误 → 返回Step 4。
- Storyboard正确但身份仍漂移 → 调整参考身份板或建议更高质量模型。

报告未通过时 `mayAutoRegenerate=false`。向用户展示分数、失败时间、归因、修复建议和服务实际返回的积分后，完整读取 [skill_optimization.md](skill_optimization.md)，为最终状态为`failed`的报告自动生成技能优化方案。`needs_visual_review`不得提前触发方案。用户确认技能优化只授权修改并验证技能；只有另行明确同意后，才允许带 `--new-candidate --quality-retry-approved` 创建下一候选。
