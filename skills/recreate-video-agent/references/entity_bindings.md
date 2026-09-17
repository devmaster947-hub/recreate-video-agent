# Blueprint 7.0 与轻量替换绑定

新任务使用Planner V2；V1/6.0旧任务按原流程恢复，不从旧speakerId猜测迁移人物。

## 来源字段

- 视频元素.人物[]：characterId、原有外观描述、referenceTime（出镜镜头内的原片全局秒）。沉默和短暂人物也记录。
- 视频元素.产品[]：productId、产品识别名称、外观与状态。无产品为[]。
- 声音结构.voiceProfiles[]：speakerId、原有声音描述、characterId（旁白或无法关联时null）。人物与声源不要求一一对应。
- 逐镜头拆解[]：visibleCharacterIds、productIds，保留原有动作/产品状态链；对白speakerId指向声源。镜头集合不代表每帧可见集合。
- reviewIssues[]：{shotId, reason}，仅记录具体疑问。程序检查数据自洽，不能证明观察正确。

抽取原始九格后，在原有分镜理解中核对关键人物、产品、动作分工。实际画面发现遗漏时，在副本修正蓝图并用set-video-blueprint登记；不篡改原始服务端结果。保持镜头时间不变，修正引用需同步人物表和镜头表。只在有具体疑问时补看相关原片帧；不调用额外拆解模型。未解决的关键替换对象歧义再问用户。

## 登记方式

保存bindings.json，例如：

```json
{"replacementBindings":[
  {"entityType":"character","sourceId":"c1","mode":"preserve","targetId":"c1"},
  {"entityType":"character","sourceId":"c2","mode":"replace","targetId":"creator2"},
  {"entityType":"product","sourceId":"product1","mode":"replace","targetId":"targetProduct1","referenceImages":["/absolute/product.png"]}
]}
```

```text
python3 scripts/generation_manifest.py set-replacement-bindings --manifest <manifest> --file <bindings.json>
```

全部来源人物/产品各有一条映射，纯产品/无人声合法，完全无实体用空数组。preserve的targetId等于sourceId。不同人物不得合并；仅用户明确要求统一时，相关条目均记录mergeAuthorized=true。不能自行设置此授权。产品替换先按原命令登记产品参考图，referenceImages只选该产品实际需要的图。未替换产品不用referenceImages。

该清单同时用于分镜编辑指令、Prompt和参考图；它不是strict像素区域Replacement Map。目标creatorId也是各anchor的creatorIds含义，source characterId与speakerId分别保留。只露手时使用局部身份描述，不猜脸；镜面或分屏可同一身份多次出现，不机械按人数拆ID。

当前人物图策略不变。若策略要求人物图，在映射确定后先生成/登记，再编辑相应分镜。用户提供图必须登记到对应targetId；单段无用户图不新增人物图。

prepare_prompt_handoff.py在replication-package.json写入entityContexts：包含全镜头、来源IDs、目标creatorIds、替换映射及产品图清单。使用它编写完整Prompt，九格实际可见人物只是完整集合的子集。对白原文保留，目标语言转换在最终Prompt进行；说明中文，不做语言校验。

分镜编辑后的fast/strict检查和视频生成后检查完全沿用原规则。
