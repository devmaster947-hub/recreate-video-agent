# LuluLab CLI 与原灵智CLI的共用契约

服务端与工作流契约保持不变；随包客户端现为 Node.js CLI。固定工作流 ID 为 `ImageGenV2`、`VideoGenV2` 和 `RecreateVideoPromptV3`。

所有任务通过 `node scripts/lululab_cli.mjs task submit --workflow-id <ID> --input <JSON>` 提交；取得 ID 后用 `task fetch --id <ID>` 查询。`user --credits`作真实鉴权预检，不在任务日志保存余额。`upload <file>`返回媒体 url 和 mimeType；签名上传 URL 的 expiresAt 不作为媒体的 expiredAt。

图片输入：`model: gpt-image-2-5-sunburst`、`prompt`、`resolution: 1K`、`aspectRatio`、`referenceImages`。视频输入额外包含整数duration，模型默认 `seedance-2-mini`，720p。referenceImages有序保留最终Storyboard、需替换的产品图、相关人物图，不传本地路径。

纯文生图使用空referenceImages。成功图片从 `output.image`、视频从 `output.video` 读取。始终排除input中的参考图。内置ID无需配置，环境变量 `LULULAB_IMAGE_WORKFLOW_ID`、`LULULAB_VIDEO_WORKFLOW_ID`仅供高级覆盖。
