# 本地口播识别

`scripts/local_transcribe.py`把原视频音轨直接转成`audioReview`，输出分段和词级时间戳。音频只在本机读取；模型权重可以从模型仓库下载，但输入音频不得上传到任何转写服务。

## 后端顺序

`--backend auto`固定选择：

1. Apple Silicon上的`mlx-whisper`；
2. 已安装的`openai-whisper`。

检测：

```text
python3 scripts/local_transcribe.py --detect
```

Apple Silicon推荐运行方式：

```text
uv run --with mlx-whisper python scripts/local_transcribe.py \
  --video <benchmark> \
  --output <task>/analysis/audio-review.json \
  --backend mlx
```

如果目标语言已知，可传`--language en`、`--language zh`等Whisper语言代码。默认自动识别。默认MLX模型为`mlx-community/whisper-turbo`；只有用户对速度、磁盘或精度另有要求时才传`--model`。

首次`uv run`可能下载Python依赖，首次使用模型可能下载权重。发生下载前遵守当前环境的网络与文件权限要求。下载的是执行依赖和模型权重，不是上传用户音频。

若系统已经安装后端，也可直接运行：

```text
python3 scripts/local_transcribe.py --video <benchmark> --output <task>/analysis/audio-review.json
```

## 输出与登记

有口播时输出：

```json
{
  "audioReview": {
    "status": "transcript_ready",
    "hasAudio": true,
    "speechDetected": true,
    "reviewMethod": "local_mlx_whisper",
    "language": "en",
    "text": "...",
    "segments": [
      {
        "id": 1,
        "start": 0.24,
        "end": 2.18,
        "text": "...",
        "words": [{"start": 0.24, "end": 0.62, "text": "..."}]
      }
    ],
    "audioUploaded": false
  }
}
```

音轨存在但Whisper未检测到可用语音时输出`verified_no_speech`；视频无音轨时输出`no_audio`。登记：

```text
python3 scripts/generation_manifest.py set-audio-review --manifest <manifest> --file <task>/analysis/audio-review.json
```

## 质量边界

- 保留Whisper原始语言和时间范围，不自动翻译或润色。
- 默认生成词级时间戳，供Segment边界与视听同步使用。
- 品牌名、型号、强口音或噪声片段需要结合原音频复核；只修正能可靠确认的局部词。
- 明显幻觉、重复或空转写不得登记为可靠口播。改用更合适的本地模型或请用户提供口播稿。
- 后端不可用或模型下载失败时停止，不得回退到外部ASR API，不得把失败解释为“无人声”。
