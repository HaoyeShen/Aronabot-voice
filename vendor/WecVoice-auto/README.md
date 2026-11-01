# WecVoice-auto

> 在 Windows 上，用**免费的 Edge TTS** 合成语音，再通过**微信 4.1.9+ 原生“按住说话”**发送真正的微信语音气泡。
> 不是语音文件、不是语音转文字，对方收到的就是一条可以点开播放的语音消息。

当前版本：**0.0.2（支持微信 4.2 右侧 Alt）**

---

## 它解决什么问题

微信电脑版从 **4.1.9** 开始才支持“发送语音消息”（发送按钮旁的语音键，或按住**右侧 `Alt`** 说话），最长 60 秒。
这给机器人留出了一条干净的路线：

1. 用 TTS 把回复合成为音频；
2. 把音频播放到一块**虚拟声卡**；
3. 把虚拟声卡的录音端设为微信的“麦克风”；
4. 程序按住**右侧 `Alt`**（或语音按钮），播放音频，松开；
5. 微信自己完成录音编码并把语音气泡发出去。

整个过程**不注入微信进程、不读写微信数据库、不截图、不劫持协议**，只使用 Windows 的 UI 自动化与音频设备。

```
文本 ──► Edge TTS ──► 48kHz PCM ──► CABLE Input（播放）
                                          │
                                          ▼
                                    CABLE Output（录音）
                                          │
微信窗口 ◄── 按住 Alt / 语音键 ◄──────────┘
   │
   └── 松开后，微信原生发送语音气泡
```

## 功能

- ✅ 真正的微信语音气泡（不是 `.silk` / `.mp3` 文件）
- ✅ 免费 TTS：Microsoft Edge 在线音色，无需 API Key
- ✅ 音色可切换：12 个内置预设 + 任意 Edge 音色 + 语速/音调/音量覆盖
- ✅ 默认音色 `arona`：阿洛娜风格近似音（晓伊 + 音高/语速调整）
- ✅ 自动切换默认录音设备，发送后恢复原麦克风
- ✅ 长回复自动按句切分，避免超过微信 60 秒限制
- ✅ 试听模式：只在本机播放，不碰微信
- ✅ 环境诊断：`wecvoice doctor` 一次检查音频、微信、权限
- ✅ 可注入 TTS / 播放器 / 窗口对象，方便接入现有机器人
- ✅ 纯 UI 自动化，不注入、不读库、不截屏

## 环境要求

| 项目 | 要求 |
|---|---|
| 系统 | Windows 10 / 11 64 位 |
| 微信 | **4.1.9 或更高**（4.1.15 实测可用），已登录并打开主窗口 |
| Python | 3.9 – 3.12 |
| 音频 | **VB-Audio Virtual Cable**（免费） |
| 网络 | 合成语音时需要访问 Edge TTS（`speech.platform.bing.com`） |

### 1. 安装 VB-CABLE

从 <https://vb-audio.com/Cable/> 下载 `VBCABLE_Driver_Pack*.zip`，解压后**右键以管理员身份运行** `VBCABLE_Setup_x64.exe`，安装完成后**重启电脑**。

安装成功后，声音设置里会出现两个设备：

- **CABLE Input (VB-Audio Virtual Cable)** —— 播放设备，TTS 音频送进去；
- **CABLE Output (VB-Audio Virtual Cable)** —— 录音设备，微信从这里“听”。

> 名称看起来是反的，这是 VB-CABLE 的命名习惯：Cable 的“输入”是给它喂声音的播放端。

### 2. 设置默认录音设备

推荐让程序自动切换（默认行为）。如果自动切换不生效，手动设置：

`设置 → 系统 → 声音 → 输入 → 选择 CABLE Output`

然后**重启微信**（微信可能在启动时缓存录音设备）。设置好后可以把 `switch_default_input` 关掉。

### 3. 安装本库

尚未发布到 PyPI，直接从 GitHub 安装：

```powershell
pip install "git+https://github.com/HaoyeShen/WecVoice-auto.git"
```

没有安装 `git` 时，可以直接安装 tag 压缩包：

```powershell
pip install "https://github.com/HaoyeShen/WecVoice-auto/archive/refs/tags/v0.0.0.zip"
```

或者克隆后开发安装：

```powershell
git clone https://github.com/HaoyeShen/WecVoice-auto.git
cd WecVoice-auto
pip install -e ".[dev]"
```

### 4. 自检

```powershell
wecvoice doctor
```

期望看到：

```
[OK] Virtual output: [x] CABLE Input (VB-Audio Virtual Cable)
[OK] Virtual input: [y] CABLE Output (VB-Audio Virtual Cable)
[OK] WeChat window: '微信', hwnd ...
[OK] Trigger: Hold RIGHT Alt (AltGr) to talk (recommended)
```

## 快速开始

### 命令行

先在微信里**打开目标会话**（本库只负责发送，不负责搜索会话），然后：

```powershell
wecvoice send "老师早上好！今天也要加油哦。" --chat 文件传输助手
```

试听音色（不发送）：

```powershell
wecvoice say "老师，阿洛娜在这里。" --voice arona
```

保存成 WAV：

```powershell
wecvoice say "晚安，老师。" --voice arona-soft --save night.wav
```

列出音色：

```powershell
wecvoice voices
wecvoice voices --online --locale zh
```

### Python API

```python
from wecvoice_auto import VoiceSender

sender = VoiceSender()
result = sender.send(
    "老师早上好！今天也要加油哦。",
    chat="文件传输助手",   # 只做校验；请先在微信里打开它
    voice="arona",
)
print(result.to_dict())
```

接入现有机器人（例如 ARONA）：

```python
from wecvoice_auto import VoiceSender

voice = VoiceSender()

# 在你的回复流程里，文本发送成功之后：
voice.send(reply_text, chat=incoming.chat)
```

`send()` 的常用参数：

| 参数 | 说明 |
|---|---|
| `chat` | 目标会话名；用于校验当前打开的会话，不会自动切换 |
| `voice` | 预设 id 或 Edge 音色名；默认 `arona` |
| `rate` / `pitch` / `volume` | 覆盖预设，例如 `rate="+10%"`、`pitch="+5Hz"` |
| `preview` | `True` 时只在本机播放，不操作微信 |
| `trigger` | `"alt"`（默认）或 `"button"` |

## 音色

### 内置预设

| ID | 说明 | Edge 音色 |
|---|---|---|
| `arona` | **默认**，阿洛娜风格近似 | `zh-CN-XiaoyiNeural`，`+5%` / `+12Hz` |
| `arona-happy` | 元气版 | 晓伊，`+12%` / `+20Hz` |
| `arona-soft` | 轻声版 | 晓伊，`-4%` / `+8Hz` |
| `plana` | 普拉娜风格近似 | 晓晓，`-5%` |
| `xiaoyi` | 晓伊·少女 | `zh-CN-XiaoyiNeural` |
| `xiaoxiao` | 晓晓·温柔 | `zh-CN-XiaoxiaoNeural` |
| `yunxi` | 云希·少年 | `zh-CN-YunxiNeural` |
| `yunjian` | 云健·旁白 | `zh-CN-YunjianNeural` |
| `xiaobei` | 晓北·东北 | `zh-CN-liaoning-XiaobeiNeural` |
| `xiaoni` | 晓妮·陕西 | `zh-CN-shaanxi-XiaoniNeural` |
| `nanami` | 七海·日语 | `ja-JP-NanamiNeural` |
| `arona-jp` | 阿洛娜日语近似 | `ja-JP-NanamiNeural`，`+3%` / `+10Hz` |

> **关于“阿洛娜”音色**：`arona` 是使用免费 Edge 音色做的**风格近似**，不包含《蔚蓝档案》官方音源，也不是声优的声音克隆。若需要更接近原声，可自行接入有授权的 GPT-SoVITS / RVC 模型（`VoiceSender` 支持注入自定义 TTS 对象）。

### 自定义音色

直接传 Edge 音色短名：

```python
sender.send("测试一下", voice="zh-CN-XiaoxiaoNeural", rate="+0%", pitch="-5Hz")
```

或修改配置：

```powershell
wecvoice config --set voice=plana
wecvoice config --set rate=+0%
```

## 配置

配置文件默认在 `%APPDATA%\WecVoice-auto\config.json`，也可以 `--config` 指定。

| 键 | 默认值 | 说明 |
|---|---|---|
| `voice` | `arona` | 默认音色 |
| `max_chars` | `120` | 单段最大字数，超出自动切分 |
| `max_seconds` | `55` | 单段最长秒数，超过直接报错 |
| `output_device` | `CABLE Input` | TTS 播放端（子串匹配） |
| `input_device` | `CABLE Output` | 微信录音端（子串匹配） |
| `switch_default_input` | `true` | 发送时自动切换默认录音设备 |
| `restore_default_input` | `true` | 发送后恢复原设备 |
| `trigger` | `alt` | `alt` 或 `button` |
| `lead_seconds` | `0.35` | 按下后等待多久再播放（避免截头） |
| `tail_seconds` | `0.30` | 播完后等待多久再松开（避免截尾） |
| `settle_seconds` | `1.1` | 松开后等待微信完成发送 |
| `save_audio` | `false` | 保留 WAV 便于排查 |
| `gain` | `1.0` | 音量倍数 |

## 故障排查

| 现象 | 原因 / 解决 |
|---|---|
| 对方收到语音但没有声音 | 默认录音设备不是 CABLE Output。运行 `wecvoice doctor`；手动切换后**重启微信**。 |
| 语音开头/结尾被截断 | 增大 `lead_seconds` / `tail_seconds`，例如 `0.6` / `0.5`。 |
| 按 Alt 没反应 | 微信 4.2 只认**右侧 Alt**；确认按的是右 Alt，微信窗口有焦点；仍不行再试 `--trigger button`。 |
| 找不到 CABLE 设备 | VB-CABLE 未安装或未重启；确认名字里包含 `CABLE`。 |
| 切换默认麦克风失败 | 系统策略/驱动覆盖。手动设置后加 `--no-switch`。 |
| 发送后麦克风没恢复 | 检查 `restore_default_input`；异常退出时运行 `wecvoice config --set restore_default_input=true`。 |
| Edge TTS 超时 | 需要联网；检查代理/防火墙是否能访问 `speech.platform.bing.com`。 |
| 提示超过 60 秒 | 调小 `max_chars`，或缩短文本。 |
| 微信录到的是环境音 | 默认设备被切回真实麦克风；关闭其他会议/录音软件，重新运行。 |
| 在受限沙箱/低完整性进程里运行，微信窗口能看到但没反应 | 微信以 Medium 完整性运行，Low 完整性进程会被 UIPI 拦截。请从桌面正常启动终端，不要从沙箱或受限文件面板启动。 |

## 隐私与边界

- 不注入微信进程，不修改微信内存，不读写聊天数据库。
- 不截图、不录屏。
- 语音内容只在本机合成，经虚拟声卡进入微信；Edge TTS 会把文本发送到 Microsoft 的语音服务。
- 这是个人自动化工具，不是腾讯官方能力。使用微信自动化存在账号风险，请只在你本人有权操作的账号上使用，并控制发送频率。
- 角色音色、声音克隆涉及著作权与人格权；请使用你有权使用的声音，不要用于冒充或欺诈。

## 已知限制

- 只支持 Windows；依赖 VB-CABLE 驱动。
- 发送期间微信窗口需要在前台，且目标会话已经打开。
- 同一时刻只支持一个发送流程（内部有 UI 锁的需求，v0.0.0 未实现多线程排队）。
- 微信客户端改版可能导致 `trigger="button"` 失效；`alt` 路线更稳。
- Edge TTS 是微软 Edge 的在线朗读接口，不是正式商用 API，可能限流或变更。
- 60 秒是微信上限，本库默认按 55 秒保护。

## 开发

```powershell
pip install -e ".[dev]"
pytest -q
```

项目结构：

```
src/wecvoice_auto/
  audio.py      # 设备枚举、重采样、播放
  cli.py        # wecvoice 命令行
  config.py     # JSON 配置
  devices.py    # IPolicyConfig 切换默认麦克风
  doctor.py     # 环境诊断
  input.py      # Alt / 鼠标按住
  sender.py     # 发送编排
  text.py       # 清理、切分
  tts.py        # Edge TTS + MP3 解码
  voices.py     # 音色预设
  wechat.py     # 微信窗口/UIA
```

## Roadmap

- [ ] 多线程发送队列与取消
- [ ] 自动搜索并打开会话（可选）
- [ ] GPT-SoVITS / RVC 本地音色适配器
- [ ] 发送结果校验（基于 UIA 消息列表）
- [ ] 英文文档与更多音色预设

## License

MIT。详见 [LICENSE](LICENSE)。

## 致谢

- [Microsoft Edge TTS](https://github.com/rany2/edge-tts) —— 免费在线音色
- [VB-Audio Virtual Cable](https://vb-audio.com/Cable/) —— 虚拟声卡
- [wechatauto-replica](https://github.com/fanyuantaier/wechatauto-replica) —— 微信 4.x UIA 控件锚点的参考实现（本库未复制其代码）
- Windows `IPolicyConfig` 的公开逆向资料 —— 默认音频设备切换
