# Windows x64 LZStudio CLI

Skill内置LZStudio CLI 0.0.5：`cli/windows-x64/lzstudio.exe`。其SHA-256为`f1c61d3fd5ec0ee5b6a58957494ff21cf220098e4350a4c2f89081f60bf55ab0`。`server_video_analysis.py`会在Windows x64上直接发现该文件；无需为了使用Skill修改PATH。

用户明确要求全局安装时才执行：

```powershell
py scripts\install_lzstudio.py
```

脚本只支持Windows x64和macOS arm64；Windows默认安装到`%LOCALAPPDATA%\Programs\LZStudio\bin\lzstudio.exe`，并幂等加入当前用户PATH。安装前校验内置文件SHA-256，安装后使用精确目标路径验证`--version`及`task submit --help`契约。失败时停止，不联网下载、不回退到其他CLI。
