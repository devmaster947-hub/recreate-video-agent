# Step 5：默认快速技术质检

正常生成完成后默认运行Fast质量报告，只用ffprobe/ffmpeg读取媒体信息，不重新逐帧扫描原片和成片，也不生成aligned-comparison。检查项：

- 成片文件可读取
- 时长是否在容差内
- 画面比例是否与原片一致
- 原片有音轨时成片是否缺音轨

`technicalPassed`仅代表这些技术指标，不代表语义复刻成功。Fast报告中`qualityProfile=fast`、`semanticStatus=not_reviewed`、`comparisonSheet=null`。报告不阻断已经成功生成的视频，不自动重生成。

只有用户明确要求深度质检、复刻效果评估、镜头节奏对比或对齐图时，才运行Full模式：

```text
python3 scripts/quality_review.py analyze ... --profile full
# 本地视频生成链路也可：run_generation.py ... --full-quality-review
```

Full模式保留原有低清视觉扫描、候选切镜统计与10组原片/复刻片对齐图。语义评分仍是用户明确要求时才执行的可选分析。所有报告均`deliveryBlocked=false`、`mayAutoRegenerate=false`。
