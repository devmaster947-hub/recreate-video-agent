# Step 3-4：服务端拆解与提示词重构

本版本只把核心推理迁到服务端，不改变前后端职责：客户端锁定Segment与视觉事实，服务端按已锁定的资料生成Prompt，客户端继续做严格预检、展示和生成确认。

## 客户端必须准备的资料

- 原对标视频本地文件。
- `storyboards.generation`：每个Segment恰好一张最终4×4 Storyboard。
- 每张Storyboard的`storyboardId`、`segmentId`、`globalStart`、`globalEnd`和16个anchors。
- 产品卡与有效产品参考图；沿用原产品时明确`useBenchmarkProduct=true`。
- Creator ID与参考图；只上传实际登记的Creator。
- `userConfig`中的模型、时长、国家、语言和`customRequirement`。

不得上传`storyboards.original`代替最终Storyboard。新产品模式下，Storyboard替换未通过`replacementVerified`时必须停止，不得调用服务端。

## 固定CLI协议

CLI只允许以下四个`input`二级字段：

```json
{
  "input": {
    "benchmarkVideoUrl": "https://example.com/benchmark.mp4",
    "userConfig": {},
    "productBrief": {},
    "creatorBrief": {}
  }
}
```

新版资料放入原字段内部，不扩展二级字段：

```json
{
  "input": {
    "benchmarkVideoUrl": "https://example.com/benchmark.mp4",
    "userConfig": {
      "videoModel": "seedance-2-fast",
      "duration": 24,
      "targetCountry": "美国",
      "targetLanguage": "英语",
      "customRequirement": "",
      "modelCapabilities": {},
      "visualContext": {
        "segments": [
          {
            "segmentId": 1,
            "storyboardId": 1,
            "globalStart": 0,
            "globalEnd": 12,
            "duration": 12,
            "storyboardUrl": "https://example.com/segment-01.png",
            "storyboardMimeType": "image/png",
            "anchors": []
          }
        ]
      }
    },
    "productBrief": {
      "useBenchmarkProduct": false,
      "productAnalysis": {},
      "productImageUrls": [
        {"url": "https://example.com/product.png", "mimeType": "image/png"}
      ]
    },
    "creatorBrief": {
      "creators": [
        {"creatorId": "creator-1", "url": "https://example.com/creator.png", "mimeType": "image/png"}
      ],
      "replacementMap": [
        {"sourceCreatorId": "source-creator-1", "role": "primary", "action": "replace", "targetCreatorId": "creator-1"},
        {"sourceCreatorId": "source-creator-2", "role": "supporting", "action": "keep"}
      ]
    }
  }
}
```

`visualContext.segments`是唯一Segment计划。服务端不得根据Gemini拆解重新切段。Storyboard、产品图与Creator图必须作为GPT多模态`files`传入，不能只把URL写进文本。

`creatorBrief.replacementMap`是人物替换的唯一映射。GPT只能为`action=replace`的人引用对应`targetCreatorId`；`action=keep`的人必须沿用最终Storyboard中的原身份。不得把一张达人图扩展成多个目标人物，也不得为未映射人物自动添加Creator引用。

## 与旧版工作流兼容

同一服务端工作流保留两条独立路径：

- `userConfig.visualContext.segments`存在且非空：进入新版Storyboard多模态路径。
- 该字段缺失或数组为空：进入旧版拆解与重构路径。

本Skill必须准备最终Storyboard，因此正常调用固定进入新版路径。兼容分支仅用于不上传Storyboard的旧Skill和旧客户端；不得为了跳过新版校验而删除`visualContext`。服务端不得根据产品图或Creator图是否存在判断模式。

## 正式调用

```text
python3 scripts/server_prompt_workflow.py \
  --manifest <task>/manifest.json \
  --benchmark <benchmark-video>
```

如已有服务端任务ID且之前只是网络或等待失败，使用：

```text
python3 scripts/server_prompt_workflow.py \
  --manifest <task>/manifest.json \
  --benchmark <benchmark-video> \
  --resume-task-id <task-id>
```

`--resume-task-id`只查询旧任务，不重新上传或submit。不得在无法确认旧任务是否创建成功时直接再提交一次。

需要检查将发送的结构而不调用任何服务时使用：

```text
python3 scripts/server_prompt_workflow.py \
  --manifest <task>/manifest.json \
  --benchmark <benchmark-video> \
  --dry-run
```

## 返回与本地校验

服务端成功结果必须至少包含：

```json
{
  "success": true,
  "output": {
    "videoAnalysisSummary": {},
    "videoPrompts": {
      "summary": "",
      "adaptationPlan": {},
      "qualitySpec": {},
      "segments": []
    },
    "creatorPrompts": {}
  },
  "credits": 5,
  "errorMessage": ""
}
```

脚本保存`prompts/server-video-prompts.json`后调用`generation_manifest.set_prompts`。以下任一情况必须失败，不得进入视频生成：

- Segment数量或ID与`visualContext`不一致。
- 任一Segment窗口、时长或Storyboard ID发生变化。
- `adaptationPlan`或`qualitySpec`缺失。
- `qualitySpec.hardAnchors/requiredCuts/proof/cta`不是数组。
- Creator ID不存在，或一个Segment引用多张Storyboard。
- Prompt时间轴、固定开头或最小生成约束未通过原有预检。

预检通过后按SKILL.md展示每段完整Prompt并等待“确认生成”。
