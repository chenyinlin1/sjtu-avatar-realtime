# 实时数字人音视频同步与打断机制实现指南

本文基于 OpenAvatarChat 当前 RTC + FlashHead 链路整理，目标读者是准备在其他工程中复用类似能力的 LLM 或工程师。文档重点不是逐行复刻代码，而是抽象出可迁移的设计原则、关键状态机和伪代码。

适用范围：

- 前端通过 WebRTC 播放远端数字人音视频流。
- 后端实时生成 avatar video frame，并把 TTS audio 按帧切片后一起送到 RTC。
- 用户可以通过按钮或语音插话打断正在播放的数字人。
- avatar 模型可以替换，但需要提供“按 fps 输出视频帧”和“按播放采样率输出音频片段”的能力。

## 1. 核心结论

音视频同步的核心不是前端手动对齐图片帧和音频，而是后端保证两个条件：

1. **视频帧按稳定 fps 输出**，例如 FlashHead 为 25 fps。
2. **每一帧视频都配套同一时间长度的音频片段**，例如 24 kHz / 25 fps = 960 samples/frame。

前端只把服务端返回的远端 `MediaStream` 绑定到 `<video>`，浏览器和 WebRTC 根据同一条媒体流内的音频、视频时间戳完成播放同步。

打断机制的核心也不是直接“停掉前端 video 标签”，而是从控制面发出 `INTERRUPT` 信号，服务端取消当前 `CLIENT_PLAYBACK` 播放流，再由 avatar、TTS、LLM 等处理器清理各自的队列和状态。

## 2. 本项目代码依据

以下路径是本文对应的主要实现位置：

| 模块 | 代码位置 | 作用 |
| --- | --- | --- |
| 前端 WebRTC 播放 | `/root/autodl-tmp/chatrobot_rebuild_workspace/OpenAvatarChat/src/service/frontend_service/frontend/src/renderer/src/utils/webrtcUtils.ts:214-228` | 将远端 `MediaStream` 绑定到 video 节点 |
| 前端 RTC 配置加载 | `/root/autodl-tmp/chatrobot_rebuild_workspace/OpenAvatarChat/src/service/frontend_service/frontend/src/renderer/src/store/app.ts:46-85` | 从后端 init config 读取 `rtc_configuration` 和 `track_constraints` |
| 前端 PeerConnection | `/root/autodl-tmp/chatrobot_rebuild_workspace/OpenAvatarChat/src/service/frontend_service/frontend/src/renderer/src/store/webrtc.ts:38-65` | 使用 `new RTCPeerConnection(appStore.rtcConfig)` 创建连接 |
| 前端媒体约束 | `/root/autodl-tmp/chatrobot_rebuild_workspace/OpenAvatarChat/src/service/frontend_service/frontend/src/renderer/src/store/media.ts:118-237` | 合并设备选择和服务端下发的 track constraints |
| 前端 getUserMedia | `/root/autodl-tmp/chatrobot_rebuild_workspace/OpenAvatarChat/src/service/frontend_service/frontend/src/renderer/src/utils/streamUtils.ts:18-42` | 调用浏览器 `getUserMedia` 获取本地音视频输入 |
| 前端手动打断 | `/root/autodl-tmp/chatrobot_rebuild_workspace/OpenAvatarChat/src/service/frontend_service/frontend/src/renderer/src/store/webrtc.ts:123-140` | 通过 data channel 发送 `Interrupt` |
| 前端打断按钮 | `/root/autodl-tmp/chatrobot_rebuild_workspace/OpenAvatarChat/src/service/frontend_service/frontend/src/renderer/src/components/ActionGroup.vue:153-173` | 根据 `replying` 和连接状态决定是否可点击 |
| WebRTC offer/ICE | `/root/autodl-tmp/chatrobot_rebuild_workspace/OpenAvatarChat/src/service/frontend_service/frontend/src/renderer/src/utils/webrtcUtils.ts:230-262` | 创建 data channel、offer、发送 ICE candidate、设置 remote answer |
| WebRTC 初始化配置 | `/root/autodl-tmp/chatrobot_rebuild_workspace/OpenAvatarChat/src/handlers/client/rtc_client/client_handler_rtc.py:412-437` | 下发 `rtc_configuration`、`track_constraints`、avatar config |
| WebRTC 服务挂载 | `/root/autodl-tmp/chatrobot_rebuild_workspace/OpenAvatarChat/src/handlers/client/rtc_client/client_handler_rtc.py:492-518` | 创建 fastrtc `Stream(modality="audio-video", mode="send-receive")` |
| TURN/STUN provider | `/root/autodl-tmp/chatrobot_rebuild_workspace/OpenAvatarChat/src/service/rtc_service/rtc_provider.py:22-70` | 解析 `turn_config`，生成浏览器 `rtc_configuration` |
| TURN server 参数 | `/root/autodl-tmp/chatrobot_rebuild_workspace/OpenAvatarChat/src/service/rtc_service/turn_providers/turn_service.py:11-33` | 生成 `iceServers: [{ urls, username, credential }]` |
| Twilio TURN 参数 | `/root/autodl-tmp/chatrobot_rebuild_workspace/OpenAvatarChat/src/service/rtc_service/turn_providers/twilio_service.py:8-38` | 生成 Twilio `iceServers`，并设置 `iceTransportPolicy="relay"` |
| H.264 编码配置 | `/root/autodl-tmp/chatrobot_rebuild_workspace/OpenAvatarChat/src/handlers/client/rtc_client/client_handler_rtc.py:27-216` | 优先 H.264，配置码率、硬件编码和低延迟 encoder options |
| RTC 音视频输出 | `/root/autodl-tmp/chatrobot_rebuild_workspace/OpenAvatarChat/src/service/rtc_service/rtc_stream.py:161-218` | 从 audio/video queue 取数据并交给 RTC |
| RTC data channel | `/root/autodl-tmp/chatrobot_rebuild_workspace/OpenAvatarChat/src/service/rtc_service/rtc_stream.py:258-281` | 接收前端 `Interrupt` 消息并发信号 |
| RTC fps 配置 | `/root/autodl-tmp/chatrobot_rebuild_workspace/OpenAvatarChat/src/handlers/client/rtc_client/client_handler_rtc.py:336-378` | `output_video_fps` 必须和 avatar fps 一致 |
| 当前 6006 配置 | `/root/autodl-tmp/chatrobot_rebuild_workspace/OpenAvatarChat/config/chat_with_openai_compatible_bailian_cosyvoice_flashhead_6006.yaml:20-25,98-104` | RTC 和 FlashHead 都配置为 25 fps |
| FlashHead 音频预处理 | `/root/autodl-tmp/chatrobot_rebuild_workspace/OpenAvatarChat/src/handlers/avatar/flashhead/avatar_handler_flashhead.py:456-490` | 保留原始播放音频，同时重采样给模型 |
| FlashHead 帧音频配对 | `/root/autodl-tmp/chatrobot_rebuild_workspace/OpenAvatarChat/src/handlers/avatar/flashhead/flashhead_processor.py:78-81,291-318` | 计算每帧音频长度并入队 `(video, audio)` 对 |
| FlashHead 节拍输出 | `/root/autodl-tmp/chatrobot_rebuild_workspace/OpenAvatarChat/src/handlers/avatar/flashhead/flashhead_processor.py:420-486` | 按 fps 定时输出，idle 时也输出静音音频 |
| 打断总控 | `/root/autodl-tmp/chatrobot_rebuild_workspace/OpenAvatarChat/src/handlers/logic/interrupt/interrupt_handler.py:63-117` | 监听 `INTERRUPT`，取消播放流 |
| Stream 取消 | `/root/autodl-tmp/chatrobot_rebuild_workspace/OpenAvatarChat/src/chat_engine/core/stream_manager.py:279-333,994-1021` | 取消当前流和可取消祖先流 |
| FlashHead 打断清理 | `/root/autodl-tmp/chatrobot_rebuild_workspace/OpenAvatarChat/src/handlers/avatar/flashhead/avatar_handler_flashhead.py:133-146,492-498` | 收到 `STREAM_CANCEL` 后调用 processor interrupt |
| FlashHead 队列清理 | `/root/autodl-tmp/chatrobot_rebuild_workspace/OpenAvatarChat/src/handlers/avatar/flashhead/flashhead_processor.py:514-543` | 清 pending audio、output queue、speaking 状态 |
| 语音打断 VAD | `/root/autodl-tmp/chatrobot_rebuild_workspace/OpenAvatarChat/src/handlers/vad/silerovad/duplex_vad_handler.py:121-127,220-232,305-313` | 数字人说话时也持续监听麦克风 |
| 语义打断 | `/root/autodl-tmp/chatrobot_rebuild_workspace/OpenAvatarChat/src/handlers/llm/semantic_turn_detector/semantic_turn_detector_handler.py:41-68,520-580,612-710,985-1036` | 判断用户是否在打断，以及是否有新话题 |
| LLM 取消 | `/root/autodl-tmp/chatrobot_rebuild_workspace/OpenAvatarChat/src/handlers/llm/openai_compatible/llm_handler_openai_compatible.py:166-174,209-215` | 流取消后停止继续消费 LLM stream |
| TTS 取消 | `/root/autodl-tmp/chatrobot_rebuild_workspace/OpenAvatarChat/src/handlers/tts/bailian_tts/tts_handler_cosyvoice_bailian.py:46-53,199-218,267-292` | 调用 TTS streaming cancel，并停止提交音频 |

## 3. WebRTC 相关参数

这一节整理当前代码里真正影响 WebRTC 连接、采集、编码和 RTC 输出节奏的参数。迁移到其他工程时，建议把这些参数分成三类理解：

- **连通性参数**：STUN/TURN、ICE policy、session TTL。
- **本地采集参数**：浏览器 `getUserMedia` 的 audio/video constraints。
- **服务端媒体输出参数**：RTC handler 的输入/输出采样率、音频 frame size、视频 fps、编码器码率。

### 3.1 配置下发路径

服务端在 `ClientHandlerRtc.build_frontend_init_config()` 中下发：

```python
config = {
    "avatar_config": avatar_config,
    "rtc_configuration": rtc_configuration,
    "track_constraints": track_constraints,
}
```

前端在 `appStore.init()` 中读取：

```ts
if (config.rtc_configuration) {
  appStore.rtcConfig = config.rtc_configuration
}

if (config.track_constraints) {
  mediaStore.setTrackConstraints(config.track_constraints)
}
```

然后 WebRTC 启动时使用：

```ts
peerConnection = new RTCPeerConnection(appStore.rtcConfig)
localStream = await getUserMedia(trackConstraints)
setupWebRTC(localStream, peerConnection, remoteVideo)
```

迁移原则：

- 后端负责生成 `rtc_configuration`，前端只透传给 `RTCPeerConnection`。
- 后端负责生成推荐的 `track_constraints`，前端可叠加用户选择的设备 `deviceId`。
- 不要把 TURN 密码硬编码到前端源码；当前工程是通过 init config 动态下发。

### 3.2 RTCConfiguration / ICE 参数

当前 6006 配置使用自建 TURN provider。实际中转服务器参数来自
`/root/autodl-tmp/chatrobot_rebuild_workspace/OpenAvatarChat/config/chat_with_openai_compatible_bailian_cosyvoice_flashhead_6006.yaml:26-33`：

```yaml
RtcClient:
  connection_ttl: 900
  output_video_fps: 25
  input_video_enabled: True
  turn_config:
    turn_provider: turn_server
    urls:
      - "stun:stun.l.google.com:19302"
      - "turn:115.159.81.115:3478?transport=udp"
      - "turn:115.159.81.115:3478?transport=tcp"
    username: "turnuser"
    credential: "Abc_2026_Turn_x9Kp7"
```

当前实际 TURN/STUN 参数表：

| 类型 | Host / IP | 端口 | Transport | URL |
| --- | --- | --- | --- | --- |
| STUN | `stun.l.google.com` | `19302` | UDP/STUN 默认 | `stun:stun.l.google.com:19302` |
| TURN | `115.159.81.115` | `3478` | UDP | `turn:115.159.81.115:3478?transport=udp` |
| TURN | `115.159.81.115` | `3478` | TCP | `turn:115.159.81.115:3478?transport=tcp` |

TURN 鉴权：

```text
username: turnuser
credential: Abc_2026_Turn_x9Kp7
```

`turn_server` provider 会生成：

```json
{
  "iceServers": [
    {
      "urls": [
        "stun:stun.l.google.com:19302",
        "turn:115.159.81.115:3478?transport=udp",
        "turn:115.159.81.115:3478?transport=tcp"
      ],
      "username": "turnuser",
      "credential": "Abc_2026_Turn_x9Kp7"
    }
  ]
}
```

Twilio provider 的行为略有不同：

```json
{
  "iceServers": "<twilio_token.ice_servers>",
  "iceTransportPolicy": "relay"
}
```

参数含义：

| 参数 | 当前来源 | 作用 | 迁移建议 |
| --- | --- | --- | --- |
| `iceServers` | `turn_config` | 浏览器 ICE 候选收集使用的 STUN/TURN 列表 | 公网/跨 NAT 必配，内网测试可为空 |
| `urls` | YAML | STUN/TURN 地址，可同时放 UDP/TCP TURN | 至少提供一个 STUN；生产环境建议 TURN UDP + TCP |
| `username` / `credential` | YAML | TURN 鉴权 | 文档和日志中应脱敏，不要提交真实密钥 |
| `iceTransportPolicy` | Twilio provider | 是否强制 relay | 自建 TURN 当前未设置，浏览器默认通常是 `all`；需要强制走 TURN 时设 `relay` |
| `connection_ttl` | `RtcClient.connection_ttl` | fastrtc session time limit，当前 900 秒 | 防止长连接泄漏；产品可按会话时长调整 |

当前工程没有显式设置这些 `RTCConfiguration` 字段：

```text
bundlePolicy
rtcpMuxPolicy
iceCandidatePoolSize
```

因此它们使用浏览器默认值。

### 3.3 前端媒体采集参数

服务端下发的 `track_constraints` 当前为：

```python
track_constraints = {
    "audio": {
        "sampleRate": 16000,
        "channelCount": 1,
        "autoGainControl": False,
        "noiseSuppression": False,
        "echoCancellation": True,
    },
    "video": {}  # input_video_enabled=True 时
}
```

如果 `input_video_enabled=False`：

```python
track_constraints["video"] = False
```

前端本地默认 fallback 是：

```ts
const defaultTrackConstraints = {
  video: {
    width: 500,
    height: 500,
  },
  audio: {},
}
```

前端最终会把设备选择和 fallback constraints 合并：

```ts
const constraints = {
  video:
    selectedVideoDevice
      ? { deviceId: { exact: selectedVideoDevice }, ...videoConstraints }
      : hasCamera && videoConstraints,
  audio:
    selectedAudioDevice
      ? { deviceId: { exact: selectedAudioDevice }, ...audioConstraints }
      : hasMic && audioConstraints,
}

stream = await navigator.mediaDevices.getUserMedia(constraints)
```

参数含义：

| 参数 | 当前值 | 作用 | 对打断/同步的影响 |
| --- | --- | --- | --- |
| `audio.sampleRate` | `16000` | 浏览器麦克风采样率请求 | 匹配服务端 VAD/ASR 输入，减少重采样和时间戳换算 |
| `audio.channelCount` | `1` | 单声道输入 | 匹配 `expected_layout="mono"` |
| `autoGainControl` | `False` | 关闭浏览器自动增益 | 避免音量被浏览器动态拉扯；但用户声音小时 VAD 可能不够敏感 |
| `noiseSuppression` | `False` | 关闭浏览器降噪 | 保留原始信号；噪声环境下可能降低 VAD 稳定性 |
| `echoCancellation` | `True` | 开启回声消除 | 数字人外放时减少自激打断，是 duplex 场景关键参数 |
| `video` | `{}` 或 `False` | 是否采集用户摄像头 | 用户摄像头不是 avatar 输出同步的核心；可按业务关闭 |

注意：

- 用户摄像头 fps 不等于 avatar 输出 fps。用户摄像头只是输入 track，avatar 输出同步看的是服务端 `output_video_fps`。
- 语音打断不敏感时，可以同时检查浏览器约束和 VAD 阈值。比如 `autoGainControl=False` 时，远距离麦克风可能音量偏低。
- 如果产品优先追求打断敏感度，可以尝试开启 `autoGainControl` 或降低 VAD 阈值，但误触发风险会升高。

### 3.4 前端 PeerConnection / offer / ICE / data channel

当前前端启动流程：

```ts
peerConnection = new RTCPeerConnection(appStore.rtcConfig)

for (const track of localStream.getTracks()) {
  peerConnection.addTrack(track, localStream)
}

peerConnection.ontrack = (event) => {
  remoteVideo.srcObject = event.streams[0]
}

dataChannel = peerConnection.createDataChannel("text")

offer = await peerConnection.createOffer()
await peerConnection.setLocalDescription(offer)

peerConnection.onicecandidate = ({ candidate }) => {
  if (candidate) {
    postToServer({
      type: "ice-candidate",
      webrtc_id,
      candidate: candidate.toJSON(),
    })
  }
}

answer = await postToServer({
  type: offer.type,
  sdp: offer.sdp,
  webrtc_id,
})

await peerConnection.setRemoteDescription(answer)
```

关键参数：

| 参数 | 当前值 | 作用 |
| --- | --- | --- |
| `RTCPeerConnection(config)` | 后端下发的 `rtc_configuration` | 决定 ICE/STUN/TURN 行为 |
| data channel label | `"text"` | 传输 `Interrupt`、文本和控制消息 |
| data channel options | 未显式设置 | 浏览器默认可靠、有序 |
| `webrtc_id` | 前端随机字符串 | 服务端用它找到对应 RTC session |
| ICE candidate 发送 | trickle ICE | candidate 产生后通过 HTTP API 发给服务端 |
| remote media 绑定 | `event.streams[0]` | 音视频在同一个远端 media stream 中播放 |

迁移原则：

- data channel 可以复用同一个 `"text"` 通道传控制消息，但消息必须有 `header.name` 或 `type` 区分。
- 手动打断建议走 data channel，因为它比等待 ASR/语义判断短得多。
- 如果需要更强的“点击即停”体感，可以在发送 data channel interrupt 后立即做前端乐观 UI/mute，但不要把它当成后端真实取消。

### 3.5 服务端 fastrtc Stream 参数

服务端创建 fastrtc `Stream`：

```python
webrtc = Stream(
    modality="audio-video",
    mode="send-receive",
    time_limit=handler_config.connection_ttl,
    rtc_configuration=rtc_configuration,
    handler=rtc_streamer_factory,
    concurrency_limit=handler_config.concurrent_limit,
)
```

参数含义：

| 参数 | 当前值 | 作用 | 迁移建议 |
| --- | --- | --- | --- |
| `modality` | `"audio-video"` | 建立音视频双媒体能力 | 数字人场景通常需要 audio + video |
| `mode` | `"send-receive"` | 前端既发送麦克风/摄像头，也接收数字人音视频 | 如果只播放不收麦克风，可改为接收模式，但语音打断会失效 |
| `time_limit` | `900` | 单次 RTC 会话最长时间 | 与产品会话生命周期一致 |
| `rtc_configuration` | TURN/STUN 配置 | 传给前端和 RTC 层 | 跨网访问时必须验证 TURN 可用 |
| `handler` | `RtcStream` | 实际处理音视频输入输出 | handler 的 fps/sample rate 是同步核心 |
| `concurrency_limit` | 默认 `1` | 并发会话限制 | GPU avatar 通常建议单路或小并发 |

### 3.6 RtcStream 媒体参数

`RtcStream` 继承 `fastrtc.AsyncAudioVideoStreamHandler`，当前构造参数是：

```python
RtcStream(
    expected_layout="mono",
    input_sample_rate=16000,
    output_sample_rate=24000,
    output_frame_size=480,
    fps=output_video_fps,
    stream_start_delay=0.5,
)
```

参数含义：

| 参数 | 当前值 | 作用 | 与同步/打断的关系 |
| --- | --- | --- | --- |
| `expected_layout` | `"mono"` | 服务端期望麦克风输入为单声道 | 匹配前端 `channelCount=1` |
| `input_sample_rate` | `16000` | 输入音频采样率，传给 fastrtc，也作为 session `timestamp_base` | 影响 VAD/ASR 输入时间轴 |
| `output_sample_rate` | `24000` | 服务端发给前端的 avatar/TTS 音频采样率 | 必须用于计算每帧音频 samples |
| `output_frame_size` | `480` | fastrtc 音频输出帧大小，24 kHz 下约 20 ms | 影响 RTC audio emit 粒度；不等同于 avatar 一帧音频 960 samples |
| `fps` | `output_video_fps`，当前 25 | RTC 视频输出 fps | 必须等于 FlashHead `fps` |
| `stream_start_delay` | `0.5` 秒 | data channel 过早消息会被忽略 | 避免刚建连时控制消息打到未准备好的 session |

这里容易混淆的是：

```text
output_frame_size = 480 samples = 20 ms at 24 kHz
avatar audio per video frame = 24000 / 25 = 960 samples = 40 ms
```

也就是说，RTC audio 层的底层输出粒度可以是 20 ms，而 avatar 同步层仍按 40 ms 为一帧视频配一段音频。迁移时不要把这两个概念混在一起。

### 3.7 H.264 编码和码率参数

当前服务端在导入 fastrtc 前修改 aiortc 的 H.264 编码配置：

```python
h264.DEFAULT_BITRATE = 1500000  # 1.5 Mbps
h264.MIN_BITRATE = 500000       # 0.5 Mbps
h264.MAX_BITRATE = 2500000      # 2.5 Mbps
```

编码器优先级：

```python
hardware_encoders = [
    "h264_nvenc",
    "h264_qsv",
    "h264_videotoolbox",
]
fallback = "libx264"
```

低延迟参数：

```python
encoder_options = {
    "h264_nvenc": {
        "preset": "p4",
        "tune": "ll",
        "profile": "main",
        "rc": "cbr",
        "zerolatency": "1",
    },
    "h264_qsv": {
        "preset": "veryfast",
        "profile": "main",
    },
    "h264_videotoolbox": {
        "realtime": "1",
        "profile": "baseline",
    },
    "libx264": {
        "level": "31",
        "tune": "zerolatency",
        "profile": "baseline",
        "preset": "ultrafast",
    },
}
```

同时代码会把 H.264 codec 排到 VP8 前面，并在 SDP negotiation 后重新整理 video transceiver codecs，让 H.264 优先。

迁移原则：

- 编码器参数不决定“音画是否配对”，但会显著影响端到端延迟和卡顿。
- 低延迟数字人优先选择 H.264 硬件编码；没有硬件编码时使用 `libx264` 的 `zerolatency/ultrafast`。
- 码率过低会糊和掉帧，过高会增加弱网延迟。当前 0.5-2.5 Mbps 是比较保守的实时头像范围。
- 如果 avatar 分辨率提高，需要同步评估 `MAX_BITRATE`、GPU encoder 支持和网络带宽。

### 3.8 WebRTC 参数迁移清单

最小可迁移配置可以抽象成：

```python
webrtc_config = {
    "rtc_configuration": {
        "iceServers": [
            {
                "urls": [
                    "stun:stun.l.google.com:19302",
                    "turn:YOUR_TURN_HOST:3478?transport=udp",
                    "turn:YOUR_TURN_HOST:3478?transport=tcp",
                ],
                "username": "YOUR_TURN_USER",
                "credential": "YOUR_TURN_PASSWORD",
            }
        ]
    },
    "track_constraints": {
        "audio": {
            "sampleRate": 16000,
            "channelCount": 1,
            "autoGainControl": False,
            "noiseSuppression": False,
            "echoCancellation": True,
        },
        "video": {},
    },
    "rtc_stream": {
        "expected_layout": "mono",
        "input_sample_rate": 16000,
        "output_sample_rate": 24000,
        "output_frame_size": 480,
        "fps": 25,
        "stream_start_delay": 0.5,
    },
    "codec": {
        "preferred": "H264",
        "default_bitrate": 1500000,
        "min_bitrate": 500000,
        "max_bitrate": 2500000,
        "low_latency": True,
    },
}
```

同步相关的硬约束：

```python
assert webrtc_config["rtc_stream"]["fps"] == avatar_config["fps"]
assert webrtc_config["rtc_stream"]["output_sample_rate"] == tts_playback_sample_rate
```

连通性相关的硬约束：

```python
if client_and_server_not_same_lan:
    assert rtc_configuration["iceServers"] contains usable STUN_or_TURN
```

打断相关的硬约束：

```python
assert data_channel_is_open
assert manual_interrupt_uses_data_channel_or_websocket
assert mic_track_enabled_if_voice_barge_in_required
```

## 4. 音视频同步设计

### 4.1 前端只负责播放远端 MediaStream

前端没有做“第 N 张图对应第 N 段音频”的业务同步。RTC 模式下，前端拿到远端 track 后，把远端流绑定给 video 元素：

```ts
function setupWebRTC(localStream, peerConnection, remoteVideo) {
  for (const track of localStream.getTracks()) {
    peerConnection.addTrack(track, localStream)
  }

  peerConnection.ontrack = (event) => {
    const remoteStream = event.streams[0]
    if (remoteVideo.srcObject !== remoteStream) {
      remoteVideo.srcObject = remoteStream
    }
  }

  const dataChannel = peerConnection.createDataChannel("text")
  return dataChannel
}
```

迁移原则：

- 前端不要用 `setTimeout` 自己排图片帧和音频片段。
- 音频 track 和视频 track 应尽量放在同一个 WebRTC peer connection 中。
- 播放同步交给浏览器媒体栈，后端负责提供连续、稳定、同一时间基准的数据。

### 4.2 RTC 输出 fps 必须和 avatar fps 一致

本项目 `ClientRtcConfigModel.output_video_fps` 默认是 30，但注释明确要求必须匹配 avatar handler 的 fps，否则 lip-sync PTS 会错。

当前 6006 配置中：

```yaml
RtcClient:
  output_video_fps: 25

FlashHead:
  fps: 25
```

迁移原则：

```python
assert rtc.output_video_fps == avatar.output_fps
```

如果 avatar 是 25 fps，但 RTC 按 30 fps 发视频，浏览器会以错误的视频节奏消费帧。即使每帧嘴形是对的，整体时间轴也会逐渐偏。

### 4.3 模型音频和播放音频分开处理

FlashHead 的算法输入采样率和播放输出采样率不同：

- TTS 输出给用户播放的原始音频通常是 24 kHz。
- FlashHead 模型推理使用 16 kHz 音频特征。
- 因此同一段 TTS audio 需要分成两份：
  - `audio_data_16k`：重采样后喂给 avatar 模型。
  - `original_audio`：保持原采样率，用来切成每帧对应的播放音频。

伪代码：

```python
def on_tts_audio(audio, input_sr, algo_sr, output_sr, speech_id, end_of_speech):
    # 给模型用
    if input_sr != algo_sr:
        model_audio = resample(audio, input_sr, algo_sr)
    else:
        model_audio = audio

    # 给前端播放用，保持原始播放采样率
    playback_audio = audio.astype(float32)

    avatar_processor.add_audio(
        audio_data_for_model=model_audio,
        audio_data_for_playback=playback_audio,
        speech_id=speech_id,
        end_of_speech=end_of_speech,
    )
```

迁移原则：

- 不要把重采样给模型的音频直接拿去播放，除非播放链路也使用同一采样率。
- avatar 推理可以用 16 kHz，WebRTC 输出可以用 24 kHz，两者通过“同一语音片段的时间长度”对齐。

### 4.4 每帧视频配一段固定长度音频

在 FlashHead 中：

```python
audio_samples_per_frame = output_sample_rate // fps
```

当 `output_sample_rate = 24000` 且 `fps = 25` 时：

```text
24000 / 25 = 960 samples/frame
960 samples = 40 ms
```

模型一次可能生成多帧视频，比如 24 帧。处理器会把原始播放音频切成同样数量的片段，入队为 `(video_frame, audio_segment)`。

伪代码：

```python
def enqueue_generated_frames(video_frames, playback_audio, speech_id, end_of_speech):
    samples_per_frame = output_sample_rate // fps

    for i, video_frame in enumerate(video_frames):
        if interrupted:
            return

        audio_start = i * samples_per_frame
        audio_end = min((i + 1) * samples_per_frame, len(playback_audio))
        audio_segment = playback_audio[audio_start:audio_end]

        output_queue.put(FrameAudioItem(
            video_frame=convert_to_transport_format(video_frame),
            audio_segment=audio_segment,
            speech_id=speech_id,
            end_of_speech=(i == len(video_frames) - 1 and end_of_speech),
        ))
```

迁移原则：

- 最好把“视频帧”和“对应音频片段”作为一个原子 item 入队。
- 后续发送线程只消费这个 item，而不是分别从两个互不相关的队列里猜测对应关系。
- 如果音频不足一帧，可以补零或允许最后一帧较短，但要保证后续时间轴连续。

### 4.5 用 frame collector 做统一节拍器

FlashHead 的 `_frame_collector_worker` 是同步链路里最关键的设计。它不是“生成好就立刻发”，而是按固定 fps 输出：

```python
def frame_collector_loop():
    frame_interval = 1.0 / fps
    start_time = monotonic()
    frame_id = 0

    while not stopped:
        target_time = start_time + frame_id * frame_interval
        sleep_until(target_time)

        item = output_queue.get_nowait_or_none()

        if interrupted and item is not None and item.speech_id is not None:
            item = None

        if item is not None:
            emit_video(item.video_frame)
            emit_audio(item.audio_segment)

            if item.end_of_speech:
                emit_speech_end(item.speech_id)
        else:
            emit_video(idle_frame)
            emit_audio(zeros(samples_per_frame))

        frame_id += 1
```

这里有三个关键点：

1. **用绝对时间计算 target_time**，避免每轮 sleep 的误差累积。
2. **每个 tick 最多发一帧视频和一段音频**，让时间节奏由 fps 控制。
3. **没有讲话帧时，也发 idle video 和 silent audio**。

第三点非常重要。只发 idle video、不发静音音频，会导致音频 track 没有持续推进，视频 track 却一直推进，WebRTC 播放侧可能出现视频跑在音频前面的错觉或 PTS 断续。

迁移原则：

```python
if no_speech_frame:
    emit_video(idle_frame)
    emit_audio(silence_for_one_video_frame)
```

### 4.6 RTC 层只负责取队列数据并输出

RTC 层有两个输出函数：

- `emit()`：从 audio queue 取音频。
- `video_emit()`：从 video queue 取图像帧。

伪代码：

```python
async def emit_audio():
    if first_audio:
        clear_stale_output_data()
        first_audio = False

    while not quit:
        audio = await output_queues[AUDIO].get()
        if audio is valid:
            return output_sample_rate, audio

async def emit_video():
    if first_audio_not_yet_emitted:
        await sleep(0.1)

    while not quit:
        frame = await output_queues[VIDEO].get()
        if frame is valid:
            return frame
```

迁移原则：

- RTC 层不应该重新决定音画同步关系，它只消费 avatar 层已经配好的音频和视频数据。
- 首帧阶段可以让视频稍微等待音频，避免一开始视频先跑。
- 如果有历史残留数据，开始新播放前应清空旧队列。

## 5. 音视频同步实现清单

在其他工程中复用时，至少满足这些不变量：

1. **统一 fps**

```python
rtc_fps == avatar_fps
```

2. **统一输出音频采样率**

```python
samples_per_frame = output_audio_sample_rate // avatar_fps
```

如果不能整除，建议用累计采样误差法：

```python
def audio_range_for_frame(frame_index):
    start = round(frame_index * output_sr / fps)
    end = round((frame_index + 1) * output_sr / fps)
    return start, end
```

3. **视频帧和音频片段同 item 入队**

```python
queue.put({
    "video": frame,
    "audio": audio_segment,
    "speech_id": speech_id,
    "end": is_last_frame,
})
```

4. **输出线程按 fps 节拍发**

```python
every 1 / fps seconds:
    emit one video frame
    emit matching audio duration
```

5. **idle 时也发静音音频**

```python
emit_video(idle_frame)
emit_audio(silence(samples_per_frame))
```

6. **中断时清理至少三层队列**

```text
avatar pending audio
avatar generated frame/audio queue
RTC output audio/video queue
```

本项目当前 FlashHead 已清理前两类；`RtcClientSessionDelegate.clear_data()` 已存在，但主要在首个音频输出时调用。若要进一步减少“打断后还有残留声音”，建议在 `CLIENT_PLAYBACK` cancel 时也清理 RTC output queues。

## 6. 打断机制设计

### 6.1 两类打断入口

本项目有两种打断来源：

1. **手动打断**：用户点击前端按钮。
2. **语音打断**：用户在数字人说话时插话，经过 VAD、ASR 和语义判断触发。

它们最终都汇聚成同一种控制信号：

```python
ChatSignal(type=INTERRUPT)
```

后续统一交给 `InterruptHandler` 取消播放流。

### 6.2 手动打断链路

前端按钮逻辑：

```ts
const canInterrupt = replying && streamState === "open"

function handleInterrupt() {
  if (!canInterrupt) return

  if (chatMode === "ws") {
    wsChatStore.interrupt()
  } else {
    videoChatStore.interrupt()
  }
}
```

RTC 模式下发送 data channel 消息：

```ts
function interrupt() {
  dataChannel.send(JSON.stringify({
    header: {
      name: "Interrupt",
      request_id: random_id(),
    },
    payload: {},
  }))
}
```

服务端收到后转成信号：

```python
def on_data_channel_message(message):
    data = json.loads(message)

    if data["header"]["name"] == "Interrupt":
        emit_signal(ChatSignal(
            type=INTERRUPT,
            source_type=CLIENT,
            source_name="rtc",
        ))
```

迁移原则：

- 手动打断应该走低延迟控制通道，例如 WebRTC data channel 或 websocket。
- 控制消息只表达“要打断”，不要在前端猜测后端有哪些流需要停。
- 前端可以做乐观 UI 更新，比如按钮点击后立即把 replying 置 false 或临时 mute remote audio，但真正的资源清理应由服务端完成。

### 6.3 语音打断链路

语音打断链路比手动打断慢，因为它需要经历：

```text
mic audio
  -> Duplex VAD
  -> ASR text
  -> SemanticTurnDetector
  -> INTERRUPT signal
  -> InterruptHandler
  -> stream cancellation
```

Duplex VAD 的关键点是：即使 avatar 正在播放，也持续处理麦克风音频。

伪代码：

```python
def on_mic_audio(audio_chunk):
    # duplex mode: do not ignore mic while avatar is speaking
    vad_state = vad.process(audio_chunk)

    if vad_state.enter_start:
        stream.avatar_was_speaking_at_start = session_history.avatar_is_speaking_now()

    if vad_state.in_speech:
        submit_human_duplex_audio(
            audio_chunk,
            metadata={
                "avatar_was_speaking_at_stream_start": stream.avatar_was_speaking_at_start,
            },
        )
```

SemanticTurnDetector 只在“用户开始说话时 avatar 正在说话”的情况下进入打断判断：

```python
def on_asr_text(text, metadata):
    if metadata.avatar_was_speaking_at_stream_start:
        check_interrupt(text)
    else:
        handle_normal_user_input(text)
```

判断逻辑：

```python
def check_interrupt(text):
    if len(text) < min_text_length:
        return

    if is_pure_stop_command(text):
        emit_interrupt(should_send_text=False)
        return

    avatar_text = get_current_avatar_text()

    if interrupt_on_any_speech:
        intent = judge_intent(text, avatar_text)
        emit_interrupt(should_send_text=(intent == "has_new_topic"))
        return

    result = llm_detect_interrupt(text, avatar_text)
    if result == "interrupt":
        emit_interrupt(should_send_text=False)

        intent = judge_intent(text, avatar_text)
        if intent == "has_new_topic":
            submit_human_text(text)
```

这里的 `should_send_text` 决定用户插话是否要成为新一轮问题：

- `pure_interrupt`：只停止数字人，不把这句话发给 LLM。
- `has_new_topic`：先打断当前回复，再把这句话作为新输入，让数字人立刻回答新问题。

### 6.4 打断总控只负责取消流

`InterruptHandler` 的职责是“怎么取消”，不是“什么时候取消”。什么时候取消由前端按钮或语义检测决定。

伪代码：

```python
def on_signal(signal):
    if signal.type != INTERRUPT:
        return

    target = signal.related_stream

    if target is None:
        active_playback_streams = stream_manager.find_active_streams(type=CLIENT_PLAYBACK)

        if len(active_playback_streams) == 0:
            return
        elif len(active_playback_streams) == 1:
            target = active_playback_streams[0]
        else:
            stream_manager.cancel_streams_by_type(CLIENT_PLAYBACK)
            return

    stream_manager.cancel_stream_chain(target)
    session_history.record_interrupt(signal)
```

迁移原则：

- interrupt handler 应该只依赖抽象的 stream graph，不耦合具体 avatar/TTS/LLM。
- 取消目标优先选择当前 `CLIENT_PLAYBACK`，因为用户真正感知到的是“正在播放的这条回复”。
- 取消播放流后，通过 `STREAM_CANCEL` 广播让各模块自己清理资源。

### 6.5 StreamManager 取消链路

播放流通常是下游结果，祖先可能包括：

```text
HUMAN_TEXT
  -> LLM AVATAR_TEXT
  -> TTS AVATAR_AUDIO
  -> Avatar AVATAR_VIDEO / AVATAR_AUDIO
  -> CLIENT_PLAYBACK
```

取消时需要沿着流依赖图处理：

```python
def cancel_stream_chain(target_stream):
    cancelled = []

    for ancestor in reversed(target_stream.cancelable_ancestors):
        if ancestor.status in [NOT_STARTED, STARTED]:
            if ancestor.cancel():
                cancelled.append(ancestor)

    if target_stream.cancelable:
        if target_stream.cancel():
            cancelled.append(target_stream)

    return cancelled
```

每个 stream 被取消时发出：

```python
ChatSignal(
    type=STREAM_CANCEL,
    related_stream=stream.identity,
)
```

迁移原则：

- 取消动作必须幂等，重复 cancel 不应该报错。
- 被取消的 stream 即使已经没有下游，也最好发 `STREAM_CANCEL`，因为某些 handler 可能还在处理它。
- 下游模块不要轮询全局状态，而是监听自己关心的 `STREAM_CANCEL`。

### 6.6 Avatar 侧打断清理

FlashHead 监听 `CLIENT_PLAYBACK` 的 `STREAM_CANCEL`：

```python
def on_signal(signal):
    if signal.type == STREAM_CANCEL and signal.related_stream.type == CLIENT_PLAYBACK:
        context.interrupt()
```

`context.interrupt()` 会清当前 TTS stream key，并通知 processor：

```python
def interrupt():
    current_tts_stream_key = None
    processor.interrupt()
```

processor 清理：

```python
def interrupt_processor():
    interrupted = True
    speaking = False
    current_speech_id = None
    speech_start_pending = False

    pending_model_audio = empty_array()
    pending_playback_audio = empty_array()

    audio_deque = silence(cached_audio_duration)

    while output_queue is not empty:
        output_queue.get_nowait()
```

迁移原则：

- 打断不只是停止输出，还要清掉 avatar 的历史音频状态，否则后续 idle 或新回复可能带着残留嘴形。
- 已生成但未发送的 `(video, audio)` 队列必须清空。
- frame collector 在看到 `interrupted` 后，应丢弃旧 speech_id 的帧，只允许 idle 帧继续输出。

### 6.7 TTS 和 LLM 侧取消

TTS 需要停止云端或本地 streaming synthesis，并确保 callback 不再提交音频：

```python
def on_stream_cancel(stream_id):
    session = sessions.pop(stream_id, None)
    if session:
        session.cancelled = True
        session.synthesizer.streaming_cancel()

def on_tts_data(bytes):
    if session.cancelled:
        return
    submit_audio(bytes)

def on_tts_complete():
    if session.cancelled:
        clear_temp_bytes()
        return
    submit_remaining_audio()
    finish_stream()
```

LLM streaming 需要在取消后停止消费 token：

```python
def stream_llm(stream_key):
    active_streams.add(stream_key)

    for chunk in completion:
        if stream_key not in active_streams:
            completion.close()
            break
        submit_avatar_text(chunk.text)

def on_stream_cancel(stream_key):
    active_streams.discard(stream_key)
```

迁移原则：

- 所有长耗时 streaming 模块都要有 cancel token 或 active set。
- 回调函数必须检查 `cancelled`，避免取消后又提交旧数据。
- 取消后不要发送旧 stream 的 finish frame，否则可能把新 stream 错误 finish。

## 7. 为什么“打断后还在说”不一定是网速问题

在本项目中，手动打断的控制链路通常很短：

```text
frontend data channel
  -> rtc_stream receives Interrupt
  -> InterruptHandler receives INTERRUPT
  -> CLIENT_PLAYBACK STREAM_CANCEL
  -> FlashHeadProcessor interrupt
```

如果日志中这些时间戳只差几十毫秒，问题就不是网络慢，而更可能是下面三类原因。

### 7.1 语音打断检测慢或没触发

语音打断要先过 VAD 和 ASR。如果用户声音被回声消除、降噪、播放器声音或麦克风距离影响，VAD 可能没有进入 START 状态。

当前配置示例：

```yaml
SileroVad:
  speaking_threshold: 0.25
  volume_threshold: -50

SemanticTurnDetector:
  interrupt_on_any_speech: false
  min_text_length_for_interrupt: 2
  request_timeout: 3.0
```

当 `interrupt_on_any_speech=false` 时，系统不是一听到人声就打断，而是等待语义模型判断“这是不是打断”。这会更稳，但体感更慢。

排查日志关键字：

```text
Duplex VAD: Entering START state
SemanticTurnDetector: Avatar was speaking at stream start
SemanticTurnDetector: Triggering interrupt
InterruptHandler: Received INTERRUPT
FlashHeadProcessor: interrupt requested
```

如果没有第一行，问题在 VAD 或麦克风输入。

如果有 VAD START，但很晚才出现 Triggering interrupt，问题在 ASR 或语义判断。

### 7.2 已经送到 RTC 或浏览器的缓冲还会播放一小段

FlashHead 已清空 processor output queue，但已经交给 RTC 或浏览器媒体栈的数据，可能仍会被播放几十到几百毫秒。WebRTC 本身为了平滑播放会有 jitter buffer。

优化建议：

```python
def on_client_playback_cancel():
    avatar_processor.interrupt()
    rtc_session_delegate.clear_output_queues()
    send_interrupt_notification_to_frontend()
```

前端也可以做乐观处理：

```ts
function interrupt() {
  dataChannel.send(interruptMessage)

  // UI feedback, not authoritative backend cleanup
  chatStore.replying = false
  temporarilyMuteRemoteAudio(200)
}
```

### 7.3 插话被当成新问题，数字人马上开始新回复

当 SemanticTurnDetector 判断用户插话是 `has_new_topic`，它会先打断旧播放，再把这句话作为 `HUMAN_TEXT` 送给下游 LLM。用户体感上会像“数字人没有停”，但其实可能是旧回复停了，新回复立刻开始。

如果产品希望“所有插话先只停住，不立刻回答”，可以把策略改成：

```python
def on_interrupt_intent(intent, text):
    emit_interrupt()

    if product_policy == "always_stop_only":
        return

    if intent == "has_new_topic":
        submit_human_text(text)
```

## 8. 迁移到其他工程的推荐架构

可以把系统拆成四个接口。

### 8.1 AvatarProcessor

```python
class AvatarProcessor:
    fps: int
    output_sample_rate: int

    def add_audio(self, model_audio, playback_audio, speech_id, end_of_speech):
        ...

    def on_video_audio_pair(self, callback):
        ...

    def interrupt(self):
        ...
```

必须保证：

- 输入 TTS audio 后生成 video frames。
- 每个 video frame 都有对应 audio segment。
- idle 时也输出 silent audio。
- interrupt 时清理内部所有旧 speech 状态。

### 8.2 RtcOutputBridge

```python
class RtcOutputBridge:
    def push_video(self, frame):
        video_queue.put(frame)

    def push_audio(self, audio):
        audio_queue.put(audio)

    async def emit_audio(self):
        return await audio_queue.get()

    async def emit_video(self):
        return await video_queue.get()

    def clear(self):
        clear(audio_queue)
        clear(video_queue)
```

必须保证：

- 不改变 avatar 层给出的音视频节奏。
- 新 speech 开始前可以清旧数据。
- interrupt 时可以主动清空 output queues。

### 8.3 StreamManager

```python
class StreamManager:
    def create_stream(self, type, parents, cancelable=True):
        ...

    def cancel_stream_chain(self, target_stream):
        ...

    def find_active_playback(self):
        ...
```

必须保证：

- 能根据当前播放流找到上游 LLM/TTS/avatar 流。
- cancel 后广播 `STREAM_CANCEL`。
- cancel 幂等。

### 8.4 InterruptController

```python
class InterruptController:
    def manual_interrupt(self):
        emit(INTERRUPT(source="client"))

    def voice_interrupt(self, text, intent):
        emit(INTERRUPT(source="semantic", trigger_text=text))
        if intent == "has_new_topic":
            submit_user_text(text)

    def on_interrupt(self, signal):
        playback = stream_manager.find_active_playback()
        if playback:
            stream_manager.cancel_stream_chain(playback)
            rtc_bridge.clear()
            frontend.notify("InterruptNotification")
```

必须保证：

- 手动打断低延迟。
- 语音打断可以配置“更稳”或“更敏感”。
- 打断后清理 avatar、RTC、TTS、LLM 四层状态。

## 9. 推荐调参策略

如果优先追求“稳，不乱打断”：

```yaml
SileroVad:
  speaking_threshold: 0.25

SemanticTurnDetector:
  interrupt_on_any_speech: false
```

如果优先追求“敏感，用户一说话就停”：

```yaml
SileroVad:
  speaking_threshold: 0.12-0.18

SemanticTurnDetector:
  interrupt_on_any_speech: true
```

更激进的方案是在 VAD 层增加 fast path：

```python
def on_vad_start_while_avatar_speaking():
    if speech_energy_is_high_enough and not looks_like_echo:
        emit_interrupt_immediately()
```

风险是误打断会上升，尤其是扬声器声音串进麦克风时。

## 10. 给其他 LLM 的实现提示

如果把本文交给另一个 LLM，让它在其他工程实现类似能力，可以要求它遵循这些硬约束：

1. 不要在前端用 JS 手动同步音频和图片帧。
2. 后端 avatar 层必须生成 `(video_frame, audio_segment)` 对。
3. `audio_segment` 的时长必须等于一帧视频时长。
4. idle 状态也要输出静音音频。
5. RTC fps 必须等于 avatar fps。
6. 打断统一抽象为 `INTERRUPT` 控制信号。
7. `INTERRUPT` 只取消播放流，具体资源清理由各 handler 响应 `STREAM_CANCEL`。
8. 打断时至少清理 avatar pending audio、avatar output queue、RTC output queue。
9. TTS/LLM streaming callback 必须检查 cancel 状态。
10. 语音打断要把“检测到用户在说话”和“是否把这句话作为新问题”分成两个决策。

最小可行伪代码：

```python
class RealtimeAvatarSystem:
    def __init__(self, fps, output_sr):
        self.fps = fps
        self.output_sr = output_sr
        self.samples_per_frame = round(output_sr / fps)
        self.avatar = AvatarProcessor(fps, output_sr)
        self.rtc = RtcOutputBridge()
        self.streams = StreamManager()

        self.avatar.on_frame_audio_pair(self.on_avatar_output)

    def on_avatar_output(self, frame, audio):
        self.rtc.push_video(frame)
        self.rtc.push_audio(audio)

    def on_tts_audio(self, audio, speech_id, end):
        model_audio = resample(audio, tts_sr, avatar_model_sr)
        self.avatar.add_audio(model_audio, audio, speech_id, end)

    def interrupt(self):
        playback = self.streams.find_active_playback()
        if playback:
            self.streams.cancel_stream_chain(playback)

        self.avatar.interrupt()
        self.rtc.clear()
        self.notify_frontend_interrupted()
```

## 11. 验证方法

实现后建议用日志验证三条时间线。

### 11.1 音视频同步验证

记录：

```text
avatar fps
rtc fps
output audio sample rate
samples per frame
video frame emitted timestamp
audio segment emitted timestamp and length
```

检查：

```python
assert avatar_fps == rtc_fps
assert abs(len(audio_segment) / output_sr - 1 / fps) < tolerance
```

### 11.2 手动打断验证

记录：

```text
frontend click interrupt
server data channel received Interrupt
InterruptHandler received INTERRUPT
CLIENT_PLAYBACK STREAM_CANCEL
AvatarProcessor interrupt requested
RTC output queues cleared
frontend received InterruptNotification
```

如果这些日志之间只差几十毫秒，控制链路就是正常的。残留声音通常来自 RTC/browser buffer 或新回复立刻开始。

### 11.3 语音打断验证

记录：

```text
VAD entered START
avatar_was_speaking_at_stream_start=true
ASR partial/final text
SemanticTurnDetector decision
Interrupt emitted
```

如果用户说话了但没有 `VAD entered START`，优先调 VAD、麦克风和回声消除。

如果 VAD 很快触发但语义判断慢，优先考虑 `interrupt_on_any_speech=true` 或增加 VAD fast path。

