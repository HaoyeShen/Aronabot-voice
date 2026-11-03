# 本机角色语音

选择「监听与回复 → 语音气泡」中的阿洛娜或普拉娜角色音色后，软件可用中文参考录音合成新的回复。此功能使用社区训练的角色模型，相似程度需通过试听确认，不是游戏官方语音接口。

## 本机文件

推理环境与素材位于 `data/character_voice/`，与机器人 `.venv` 分开：

- `runtime/Scripts/python.exe`：独立 Python 环境，依赖见根目录 `requirements-character-voice.txt`。
- `GPT-SoVITS/`：上游源码，固定提交 `4fd57b0ea776d396f202e13be180b4c02e4dd957`（20250228v3）。
- `models/plana/`：GPT 与 SoVITS 配套角色权重。
- `models/arona/`：阿洛娜 normal 配套权重，下载大小及 SHA-256 记录在目录内的 `manifest.json`。
- `models/base/`：中文 RoBERTa 与 CNHuBERT 基础模型。
- `plana-reference.wav`：提取的单声道参考音频；原始录音不改动。
- `profile.json`：参考台词、音频 SHA-256、模型来源及本机验证状态。
- `arona-reference.wav`、`profiles/arona.json`：用户视频中的国服中文参考录音与独立角色配置，台词为「天空真是广呢，另一边会有什么呢？」；原视频保留。
- `model_manifest.json`：下载文件大小和上游 SHA-256 校验信息。

来源：[GPT-SoVITS](https://github.com/RVC-Boss/GPT-SoVITS)、[阿洛娜社区模型](https://huggingface.co/xingguangcuican6666/arona-sovits)、[普拉娜中文社区模型](https://huggingface.co/xingguangcuican6666/plana-gptsovits-v2)、[基础模型](https://huggingface.co/lj1995/GPT-SoVITS)。模型及参考录音不随源码、安装包或发布上传。没有这些本机文件的机器保留原有 Edge 逻辑。

## 运行与回退

本机验证成功后，在语音预设中为 `arona` 或 `plana` 保存 `engine: gpt_sovits`，即可使用对应角色后端。其他预设默认 `engine: edge`，旧配置兼容。阿洛娜切换前的 Edge 参数另存为备用音色；自定义 Edge 音色保留原参数。角色后端的语速、音量可调，不对角色声线再做 Edge 音高偏移。

模型在隐藏的独立进程中运行，合成不把聊天文字或参考录音上传云端。首次加载耗时较长，同一角色后续请求复用模型；切换角色先释放原进程，再加载对应模型，避免同时占用两套模型内存或串用音色。停止或退出机器人会结束子进程。启动、推理、解码和发送异常沿用原有文字回退。当前配置专用于中文；含英文字母时回退文字，避免漏读。

CPU 推理使用 pypinyin 中文前端和 BERT 动态量化来控制内存；声学模型保持原精度。参考文件发生变化时须重新验证并更新哈希。诊断日志位于 `data/character_voice/worker.log`。

「试听」只播放「你好」，不操作微信。「发送测试语音气泡」使用当前音色发送「你好，语音测试。」；调试发送仍只使用 `test` 群。目标会话身份核对、录音前等待和结尾额外录制 1 秒继续生效。
