# 视频模型能力表

| 逻辑模型 | 官方即梦/小云雀模型ID | 单段时长 | 分辨率 | 图片上限 |
|---|---|---:|---|---:|
| Seedance 2 Mini | `seedance2.0mini_vip` | 4～15秒 | 720p | 9 |
| Seedance 2 Fast | `seedance2.0fast_vip` | 4～15秒 | 720p | 9 |
| Seedance 2 | `seedance2.0_vip` | 4～15秒 | 720p | 9 |
| Seedance 2.5 | `seedance2.5` | 4～30秒 | 720p | 9 |
| Minimax H3 | 不适用 | 4～15秒 | 720p | 9 |
| Grok Imagine 1.5 Preview | 不适用 | 1～15秒 | 720p | 9 |

以 `scripts/model_capabilities.py` 为运行时唯一事实来源。新任务保存逻辑模型ID；旧Manifest中的`seedance-2-fast-vip`和`seedance-2-vip`继续兼容读取。官方即梦和小云雀CLI必须使用表中的精确provider模型ID；CLI未声明该ID时必须在submit前报错，不得静默降级。
