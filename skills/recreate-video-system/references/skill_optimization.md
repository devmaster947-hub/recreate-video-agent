# 质检失败后的技能自优化

只在语义评分后的质量报告状态为`failed`时执行。本流程自动生成方案，但修改技能必须等待用户看到完整方案后明确回复“确认优化 skill”。该确认不授权生成新候选、上传素材或产生服务费用。

## 形成方案

读取质量报告、评分依据以及与`recommendedRepairLayer`直接相关的技能说明、脚本和测试。把报告中的每条`finding`和每个`hardFailure`映射到至少一个`issue`；一个finding包含多个根因时拆成多个issue。分类只能是：

- `skill_actionable`：现有技能存在可证明的通用缺口，修改后能改善不同任务。
- `task_specific`：只适用于当前素材、Storyboard、Prompt或候选。
- `provider_limited`：生成模型、渠道、水印或随机遵循度限制，技能无法可靠消除。
- `insufficient_evidence`：现有证据不足以支持永久修改。

单次失败可以提出修改，但“本次失败发生过”本身不证明问题通用。若技能已经包含正确约束而候选没有遵循，默认归入`provider_limited`或`insufficient_evidence`，不得简单叠加同义禁令。只有`skill_actionable`进入`changes`；所有其他issue必须出现在`exclusions`并写明原因。没有可执行修改时保存`no_change`方案，不要求用户确认。

方案JSON保存到`<task>/review/skill-optimization/<candidateId>/proposal.json`，字段固定为：

```json
{
  "proposalVersion": "1.0",
  "candidateId": "candidate-01",
  "targetSkill": "recreate-video-system",
  "summary": "",
  "issues": [
    {
      "issueId": "issue-01",
      "sourceFindingIndices": [0],
      "hardFailureCodes": [],
      "classification": "skill_actionable",
      "evidence": "",
      "reason": ""
    }
  ],
  "changes": [
    {
      "changeId": "change-01",
      "path": "SKILL.md",
      "section": "Step 5",
      "change": "",
      "expectedEffect": "",
      "risks": [],
      "tests": [],
      "addresses": ["issue-01"]
    }
  ],
  "exclusions": [
    {"issueId": "issue-02", "reason": ""}
  ],
  "acceptanceTests": [],
  "confirmationPhrase": "确认优化 skill"
}
```

新方案必须写`targetSkill: "recreate-video-system"`。重命名前已经保存的`recreate-product-video`和`recreate-product-video-v4`方案仅作为已有任务兼容值读取，不再用于新方案。

每条finding索引和每个hardFailure代码必须被issue覆盖；issue ID和change ID不得重复。每个`skill_actionable` issue必须被至少一个change引用，change不得引用其他分类。`exclusions`必须恰好覆盖全部非`skill_actionable` issue。

`path`必须是目标技能根目录下的相对路径，只允许`SKILL.md`以及`references/`、`scripts/`、`tests/`、`agents/`内的文本、Python、YAML或JSON文件。禁止`..`、绝对路径、`cli/`、凭据、其他技能和`recreate-video-system-v3.1`。

登记命令：

```text
python3 scripts/generation_manifest.py set-skill-optimization-proposal --manifest <manifest> --candidate-id <candidateId> --file <proposal.json>
```

命令会验证完整覆盖、分类与白名单，保存质量报告摘要、方案摘要、目标文件原始SHA-256和技能版本。随后向用户完整展示summary、issues、changes、exclusions、风险和验收测试，并停止等待。方案、质量报告或任一目标文件发生变化后，旧确认失效；标记`stale`并重新生成方案。

## 用户确认后应用

只有用户在看到当前完整方案后明确回复“确认优化 skill”才可继续。确认前运行事务快照；它会复核方案、质量报告和目标文件SHA-256，并把精确原文件保存到任务目录：

```text
python3 scripts/skill_optimization.py snapshot --manifest <manifest> --candidate-id <candidateId> --proposal <proposal.json>
```

随后只修改方案`changes`列出的文件，并递增`SKILL.md`标题中的小版本。不得扩大修改范围，也不得顺带重新生成视频。修改完成后运行：

```text
<workspace-python> <skill-creator>/scripts/quick_validate.py <skill-root>
<workspace-python> -m unittest discover -s <skill-root>/tests -p 'test_*.py'
```

把两项结果写入`validation.json`，至少包含`passed`、`quickValidate`和`unitTests`。全部通过后登记：

```text
python3 scripts/generation_manifest.py set-skill-optimization-result --manifest <manifest> --candidate-id <candidateId> --proposal <proposal.json> --status applied --validation-file <validation.json>
```

任一校验失败时，立即恢复快照中新建或修改前的全部文件，确认恢复后的SHA-256与登记值一致，再以`rolled_back`登记结果并报告失败。不得保留不可用技能或自动尝试第二轮修改：

```text
python3 scripts/skill_optimization.py rollback --manifest <manifest> --candidate-id <candidateId> --proposal <proposal.json>
python3 scripts/generation_manifest.py set-skill-optimization-result --manifest <manifest> --candidate-id <candidateId> --proposal <proposal.json> --status rolled_back --validation-file <validation.json>
```
