# LuluLab CLI 视频生成

固定provider为 `lululab_cli`，模型默认 `seedance-2-mini`（Seedance2 Mini），720p、每段4–15秒，不切换其他平台。

```text
python3 scripts/video_cli_preflight.py --manifest <manifest>
python3 scripts/run_generation.py --manifest <manifest> --video-provider lululab_cli --generation-approved
```

CLI通过 `task submit --workflow-id VideoGenV2 --input <JSON>` 自动触发既有视频工作流。沿用原灵智CLI输入：`model`、`prompt`、整数 `duration`、`aspectRatio`、`resolution`、`referenceImages`。图片工作流为 `ImageGenV2`，模型为 `gpt-image-2-5-sunburst`，图片输入不含duration。

参考图片按最终Storyboard、实际替换产品的独立参考图、相关人物图的顺序上传，转换为url/mimeType/expiredAt媒体对象，不把本地路径传给服务端。沿用原产品时不用额外产品图；无用户达人图的单Segment不生成身份图，多Segment跨段人物使用新生成的无产品正面/单侧面/背面三视图，不直接提交原片帧为达人图。

每段只提交一次并立即保存taskId。之后只用 `task fetch --id` 查询原任务。状态不确定、超时、失败或鉴权异常不自动重做，也不自动换平台。成功结果从output中的image/video媒体对象下载，不能误取input的参考图。

固定图片与视频工作流ID来自原附件CLI二进制，用户已确认LuluLab与原服务其他契约一致。无需客户端部署工作流；环境变量覆盖仅为高级可选项。
