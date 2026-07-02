# VAD / ASR / LLM / TTS / FlashHead 完整逻辑

这一节从当前 6006 配置出发，描述麦克风输入如何经过 VAD、ASR、语义转向、主 LLM、TTS 和 FlashHead，最终变成前端 WebRTC 播放的数字人音视频。

核心链路：

```text
Browser mic
  -> RtcClient
  -> MIC_AUDIO
  -> Duplex Silero VAD
  -> HUMAN_DUPLEX_AUDIO
  -> SenseVoice ASR
  -> HUMAN_DUPLEX_TEXT
  -> SemanticTurnDetector
  -> HUMAN_TEXT
  -> LLMOpenAICompatible
  -> AVATAR_TEXT
  -> CosyVoice TTS
  -> AVATAR_AUDIO
  -> FlashHead
  -> AVATAR_VIDEO + AVATAR_AUDIO
  -> RtcClient output queues
  -> WebRTC remote MediaStream
  -> browser <video>
```

涉及的主要代码：

| 阶段 | 代码位置 | 当前输入 | 当前输出 |
| --- | --- | --- | --- |
| 配置入口 | `/root/autodl-tmp/chatrobot_rebuild_workspace/OpenAvatarChat/config/chat_with_openai_compatible_bailian_cosyvoice_flashhead_6006.yaml:37-104` | YAML | 启用 VAD、ASR、SemanticTurnDetector、LLM、TTS、FlashHead |
| Type override | `/root/autodl-tmp/chatrobot_rebuild_workspace/OpenAvatarChat/src/chat_engine/core/chat_session.py:229-303` | handler declared type | actual runtime type |
| RtcClient 输入 | `/root/autodl-tmp/chatrobot_rebuild_workspace/OpenAvatarChat/src/handlers/client/rtc_client/client_handler_rtc.py:300-317` | WebRTC audio/video/text | `MIC_AUDIO` / `CAMERA_VIDEO` / text |
| Duplex VAD | `/root/autodl-tmp/chatrobot_rebuild_workspace/OpenAvatarChat/src/handlers/vad/silerovad/duplex_vad_handler.py:93-127,129-314` | `MIC_AUDIO` | `HUMAN_DUPLEX_AUDIO` |
| SenseVoice ASR | `/root/autodl-tmp/chatrobot_rebuild_workspace/OpenAvatarChat/src/handlers/asr/sensevoice/asr_handler_sensevoice.py:67-148` | declared `HUMAN_AUDIO`, actual `HUMAN_DUPLEX_AUDIO` | declared `HUMAN_TEXT`, actual `HUMAN_DUPLEX_TEXT` |
| SemanticTurnDetector | `/root/autodl-tmp/chatrobot_rebuild_workspace/OpenAvatarChat/src/handlers/llm/semantic_turn_detector/semantic_turn_detector_handler.py:267-318,490-710,985-1099` | `HUMAN_DUPLEX_TEXT` | `HUMAN_TEXT` 或 `INTERRUPT` |
| 主 LLM | `/root/autodl-tmp/chatrobot_rebuild_workspace/OpenAvatarChat/src/handlers/llm/openai_compatible/llm_handler_openai_compatible.py:60-84,114-207` | `HUMAN_TEXT` | streaming `AVATAR_TEXT` |
| CosyVoice TTS | `/root/autodl-tmp/chatrobot_rebuild_workspace/OpenAvatarChat/src/handlers/tts/bailian_tts/tts_handler_cosyvoice_bailian.py:68-121,141-160,193-307` | streaming `AVATAR_TEXT` | streaming `AVATAR_AUDIO` |
| FlashHead | `/root/autodl-tmp/chatrobot_rebuild_workspace/OpenAvatarChat/src/handlers/avatar/flashhead/avatar_handler_flashhead.py:350-408,410-490` | `AVATAR_AUDIO` | `AVATAR_VIDEO` + synchronized `AVATAR_AUDIO` |
| FlashHead processor | `/root/autodl-tmp/chatrobot_rebuild_workspace/OpenAvatarChat/src/handlers/avatar/flashhead/flashhead_processor.py:70-81,291-318,420-486` | model audio + playback audio | frame/audio pairs at fixed fps |

## 1. 流式推理框架的核心要点

这套工程的“流式推理”不是一个大函数从头调用到尾，而是一个 **typed stream graph**：

```text
Handler declares input/output ChatDataType
  -> ChatSession creates DataSink and ChatStreamer
  -> upstream handler streams ChatData chunks
  -> StreamManager attaches stream lifecycle and stream_id
  -> downstream handler pumper consumes chunks from its input queue
  -> next handler keeps streaming
```

关键框架代码：

| 框架点 | 代码位置 | 作用 |
| --- | --- | --- |
| handler pumper | `/root/autodl-tmp/chatrobot_rebuild_workspace/OpenAvatarChat/src/chat_engine/core/chat_session.py:51-155` | 每个 handler 独立循环消费 input queue |
| handler IO 注册 | `/root/autodl-tmp/chatrobot_rebuild_workspace/OpenAvatarChat/src/chat_engine/core/chat_session.py:216-318` | 根据 `get_handler_detail()` 注册输入 sink 和输出 streamer |
| stream_data 分发 | `/root/autodl-tmp/chatrobot_rebuild_workspace/OpenAvatarChat/src/chat_engine/core/stream_manager.py:784-839` | 给 chunk 绑定 stream、metadata、首尾标记，并投递给下游 |
| ChatDataSubmitter | `/root/autodl-tmp/chatrobot_rebuild_workspace/OpenAvatarChat/src/chat_engine/core/stream_manager.py:1040-1128` | handler 调用 `submit_data()` 后找到对应 streamer 发流 |
| stream 生命周期 | `/root/autodl-tmp/chatrobot_rebuild_workspace/OpenAvatarChat/src/chat_engine/core/stream_manager.py:810-839` | 自动发 `STREAM_BEGIN` / `STREAM_END`，打断时发 `STREAM_CANCEL` |

### 1.1 每个 handler 是独立流式算子

每个 handler 都有自己的 input queue 和 pumper 线程。pumper 的行为可以抽象为：

```python
def handler_pumper(handler, context, input_queue):
    handler.warmup_context(context)

    while session_active:
        input_data = input_queue.get_nowait_or_sleep()

        # 把当前输入 stream 更新到本 handler 的输出 streamer，
        # 这样下游 stream 能追踪到自己的上游来源。
        context.data_submitter.update_input_stream(input_data)

        # 如果输入 stream 已被 cancel，即使队列里还有残留 chunk，也丢弃。
        if input_data.stream_id and stream_manager.is_cancelled(input_data.stream_id):
            continue

        # 如果配置了 type override，handler 内部仍看到自己声明的原始类型。
        input_data.type = map_actual_type_back_to_declared_type(input_data.type)

        handler.handle(context, input_data, handler_visible_output_info)

        input_data.type = restore_actual_type(input_data.type)
```

迁移要点：

- 不要把 VAD、ASR、LLM、TTS、Avatar 写在一个同步调用栈里。
- 每个模块只处理自己 input queue 中的 chunk。
- 模块之间只通过 typed `ChatData` 通信。
- 这样 LLM/TTS/Avatar 可以边产出边被下游消费，不需要等整轮对话完成。

### 1.2 Stream 是 chunk 的生命周期容器

一个 stream 不是一个 chunk，而是一组连续 chunk 的生命周期。比如：

```text
AVATAR_TEXT stream
  chunk 1: "你好"
  chunk 2: "，我"
  chunk 3: "可以"
  ...
  last chunk: ""
```

框架在 `ChatStreamer.stream_data()` 里处理 stream 生命周期：

```python
def stream_data(data, finish_stream=False):
    if current_stream is None:
        current_stream = new_stream(source_streams=current_inputs)

    chat_data = packet_to_chat_data(data)

    if current_stream.status == NOT_STARTED:
        current_stream.status = STARTED
        chat_data.is_first_data = True
        emit_signal(STREAM_BEGIN, related_stream=current_stream.identity)

    if current_stream.status == CANCELLED:
        return

    chat_data.stream_id = current_stream.identity
    chat_data.is_last_data = finish_stream
    chat_data.metadata.update(current_stream.inheritable_metadata)

    distribute_to_downstream_sinks(chat_data)

    if chat_data.is_last_data:
        finish_current()
        emit_signal(STREAM_END, related_stream=current_stream.identity)
```

迁移要点：

- 每个流式模块都要明确“什么时候开 stream、什么时候 finish stream”。
- 每个 chunk 都必须带 `stream_id`，否则下游无法知道它属于哪一句话、哪一次 TTS 或哪一次播放。
- `is_first_data` / `is_last_data` 是流式推理的边界信号，不只是普通字段。
- metadata 必须能沿 stream 继承，比如 `avatar_was_speaking_at_stream_start`。

### 1.3 DataSink / ChatStreamer 负责解耦上下游

handler 注册时，框架根据输入输出类型创建路由：

```python
def prepare_handler(handler):
    io_detail = handler.get_handler_detail()

    for input_type in io_detail.inputs:
        data_sinks[input_type].append(DataSink(
            owner=handler.name,
            sink_queue=handler.input_queue,
        ))

    for output_type in io_detail.outputs:
        streamer = stream_manager.create_streamer(
            data_info=output_type,
            data_sinks=data_sinks,
            producer_name=handler.name,
        )
        handler.context.data_submitter.register_streamer(streamer)
```

上游 handler 不知道具体下游是谁：

```python
context.submit_data(ChatData(type=AVATAR_TEXT, data=text_delta))
```

框架会根据 `AVATAR_TEXT` 找到所有监听这个类型的 sink，例如 TTS handler 的 input queue。

迁移要点：

- 上游只依赖数据类型，不依赖下游类名。
- 增加或替换 ASR/TTS/Avatar 时，只要输入输出类型一致，就能接入流图。
- 如果一个输出只应被一个下游消费，可以使用 `ChatDataConsumeMode.ONCE`。

### 1.4 流式推理的关键不是“所有模块都真正 token 级流式”

当前链路里，不同阶段的流式粒度不同：

| 阶段 | 当前流式粒度 | 是否等待完整输入 |
| --- | --- | --- |
| RtcClient | WebRTC audio/video frame | 不等待 |
| VAD | 512-sample clip 累积成人声段 | 边检测边输出人声音频 chunk |
| SenseVoice ASR | VAD 完整人声段 | 等 `is_last_data=True` 后转写 |
| SemanticTurnDetector | ASR 文本 stream | 通常等文本结果后判断 |
| 主 LLM | token / delta text | 真正 streaming 输出 `AVATAR_TEXT` |
| CosyVoice TTS | text delta -> PCM chunk | streaming 合成和回调输出 |
| FlashHead | audio chunk -> video/audio frame pair | 内部按 chunk 推理，collector 按 fps 输出 |
| RTC output | audio/video queue | 持续拉取并发送 |

所以“流式推理”在这个工程里的含义是：

```text
模块之间用 stream/chunk 传递，能流的模块就边产边传；
不能流的模块用 is_last_data 作为聚合边界；
整个对话链路仍然保持 streaming pipeline，不阻塞到最后统一输出。
```

### 1.5 流式推理的三个时间轴

这套系统同时维护三个时间轴：

1. **输入音频时间轴**

```text
Browser mic 16 kHz -> MIC_AUDIO timestamp -> VAD slicing -> HUMAN_DUPLEX_AUDIO
```

它决定 VAD/ASR 什么时候开始、什么时候结束。

2. **文本生成时间轴**

```text
HUMAN_TEXT -> streaming LLM delta -> streaming TTS text input
```

它决定首字响应和 TTS 何时开始。

3. **播放输出时间轴**

```text
TTS 24 kHz audio -> FlashHead frame/audio pair -> RTC audio/video queues -> browser playback
```

它决定数字人最终是否音画同步、是否可被打断。

迁移时要避免把这三个时间轴混成一个全局锁。正确做法是：

```python
input_audio_timeline = vad/asr_stream_id
text_timeline = llm_stream_id
playback_timeline = client_playback_stream_id
```

然后用 stream 依赖关系把它们关联起来。

### 1.6 打断本质是取消 playback stream 及其上游，而不是杀线程

流式推理里一定会出现“旧 chunk 已经在队列里”的情况，所以打断必须在生产侧和消费侧都检查 cancel 状态：

```python
# production-side guard
if stream.status == CANCELLED:
    return

# consumer-side guard
if input_data.stream_id and stream_manager.is_cancelled(input_data.stream_id):
    continue
```

当前工程的做法：

- `InterruptHandler` 取消当前 `CLIENT_PLAYBACK` stream chain。
- LLM streaming loop 每个 token 检查 `active_stream_keys`。
- TTS callback 每次 `on_data()` 检查 `session.cancelled`。
- FlashHead processor 清空 pending audio 和 frame/audio output queue。
- handler pumper 对已经 cancel 的输入 stream 丢弃残留 chunk。

迁移要点：

- 不要指望一个 cancel flag 能立刻清掉所有队列。
- 每个 streaming callback 都要自己检查 cancel 状态。
- 每个 handler input queue 消费前也要检查 stream 是否已 cancel。
- 对用户来说，真正需要取消的是 `CLIENT_PLAYBACK`，因为这是用户正在听到/看到的输出。

### 1.7 最小可迁移框架模型

可以把整套框架抽象成下面几个对象：

```python
class ChatData:
    type: ChatDataType
    data: DataBundle
    stream_id: StreamIdentity
    is_first_data: bool
    is_last_data: bool
    timestamp: tuple[int, int]


class Handler:
    def get_handler_detail(self) -> HandlerDetail:
        return inputs, outputs, signal_filters

    def handle(self, context, chat_data, output_definitions):
        ...

    def on_signal(self, context, signal):
        ...


class ChatStreamer:
    def stream_data(self, data, finish_stream=False):
        ensure_stream()
        emit_stream_begin_if_needed()
        packet_and_distribute_chunk()
        finish_stream_if_needed()


class HandlerPumper:
    def loop(self):
        while active:
            data = input_queue.get()
            if not cancelled(data.stream_id):
                handler.handle(data)
```

只要保留这四个概念，就可以在其他工程里实现同类的 VAD/ASR/LLM/TTS/Avatar 流式推理链路。

## 2. Handler 串联不是硬编码，而是按 ChatDataType 建流

OpenAvatarChat 的 handler 会声明自己消费和产出的 `ChatDataType`。框架根据这些类型创建 streamer 和 data sink，把上游输出投递到下游输入队列。

当前数据类型定义在：

`/root/autodl-tmp/chatrobot_rebuild_workspace/OpenAvatarChat/src/chat_engine/data_models/chat_data_type.py:12-30`

关键类型：

```python
MIC_AUDIO
HUMAN_DUPLEX_AUDIO
HUMAN_DUPLEX_TEXT
HUMAN_TEXT
AVATAR_TEXT
AVATAR_AUDIO
AVATAR_VIDEO
CLIENT_PLAYBACK
```

SenseVoice ASR 的源码本体声明的是普通链路：

```python
inputs = {
    HUMAN_AUDIO: ...
}
outputs = {
    HUMAN_TEXT: ...
}
```

但当前配置里写了：

```yaml
SenseVoice:
  input_type_override:
    HUMAN_AUDIO: HUMAN_DUPLEX_AUDIO
  output_type_override:
    HUMAN_TEXT: HUMAN_DUPLEX_TEXT
```

框架在 `chat_session.py` 里做两件事：

```python
if input_type_override:
    # 实际路由时把 HUMAN_DUPLEX_AUDIO 投给 ASR
    io_detail.inputs[HUMAN_DUPLEX_AUDIO] = io_detail.inputs.pop(HUMAN_AUDIO)

if output_type_override:
    # 实际建流时创建 HUMAN_DUPLEX_TEXT streamer
    io_detail.outputs[HUMAN_DUPLEX_TEXT] = io_detail.outputs.pop(HUMAN_TEXT)

# handler.handle() 内部仍看到它声明的原始类型
input_data.type = original_type
handler.handle(context, input_data, handler_visible_output_info)
```

这个设计让同一个 ASR handler 不需要改代码，就可以接入普通半双工链路或 duplex 插话链路。

## 3. RtcClient：把 WebRTC 输入变成引擎数据

RtcClient 接收浏览器发来的麦克风/摄像头帧，在 `RtcClientSessionDelegate.submit()` 中按 channel type 打包成 `ChatData`：

```python
def submit(modality, data, timestamp):
    if modality == AUDIO:
        bundle.set_main_data(data.squeeze()[None, ...])
        chat_data_type = MIC_AUDIO
    elif modality == VIDEO:
        bundle.set_main_data(data[None, ...])
        chat_data_type = CAMERA_VIDEO
    elif modality == TEXT:
        bundle.set_main_data(data)
        chat_data_type = HUMAN_TEXT
        finish_stream = True

    submit_to_engine(ChatData(
        source="client",
        type=chat_data_type,
        data=bundle,
        timestamp=timestamp,
    ))
```

当前 WebRTC 语音输入参数是 16 kHz、单声道，因此后续 VAD/ASR 都按 16 kHz 处理。

## 4. VAD：Duplex Silero 持续监听麦克风

当前启用的是 duplex VAD：

```yaml
SileroVad:
  module: vad/silerovad/duplex_vad_handler
  speaking_threshold: 0.25
  start_delay: 1024
  end_delay: 32000
  early_end_delay: 5000
  volume_threshold: -50
```

它和普通 VAD 的区别是：

- 输入：`MIC_AUDIO`
- 输出：`HUMAN_DUPLEX_AUDIO`
- 数字人正在播放时也继续处理麦克风输入
- 进入 START 时记录 `avatar_was_speaking_at_stream_start`

伪代码：

```python
def on_mic_audio(chat_data):
    audio = chat_data.audio.squeeze()
    audio = normalize_to_float32(audio)

    for clip in slice(audio, clip_size=512):
        db = rms_to_db(clip)
        speech_prob = silero_vad(clip)

        if status in [END, POST_END] and db < volume_threshold:
            speech_prob = 0

        status, audio_clip, flags = update_status(speech_prob, clip)

        if flags.human_speech_start:
            avatar_speaking = session_history.was_avatar_speaking_at(now)
            context.avatar_was_speaking_at_stream_start = avatar_speaking

        if audio_clip is not None:
            output = DataBundle(HUMAN_DUPLEX_AUDIO)
            output.audio = audio_clip
            output.metadata.update(flags)

            if flags.human_speech_start:
                current_stream.metadata["avatar_was_speaking_at_stream_start"] = avatar_speaking

            submit(output, finish_stream=flags.human_speech_end)
```

VAD 状态大致是：

```text
END
  -> PRE_START    # speech_prob 开始超过阈值
  -> START        # speech_length >= start_delay
  -> POST_END     # silence_length >= end_delay 或 EOU 确认结束
  -> END          # 监控期结束
```

这里的 `HUMAN_DUPLEX_AUDIO` 是一段完整的人声 stream。`is_last_data=True` 时，后面的 ASR 才真正开始出最终文本。

## 5. ASR：SenseVoice 把 duplex audio 转成 duplex text

SenseVoice handler 的源码本体：

```python
input:  HUMAN_AUDIO
output: HUMAN_TEXT
```

但通过配置 override 后，运行时实际链路是：

```text
HUMAN_DUPLEX_AUDIO -> SenseVoice -> HUMAN_DUPLEX_TEXT
```

ASR 的处理逻辑：

```python
def handle(input_audio):
    for audio_segment in slice(audio, slice_size=16000):
        context.output_audios.append(audio_segment)

    if not input_audio.is_last_data:
        return

    remainder = flush_slice_context()
    if remainder is not None:
        pad_to_16000_samples(remainder)
        context.output_audios.append(remainder)

    output_audio = concatenate(context.output_audios)
    result = sensevoice_model.generate(input=output_audio, batch_size_s=10)
    text = remove_tags(result[0]["text"])

    if text:
        submit_text(text, finish_stream=True)
```

也就是说，当前 ASR 不是每个音频 chunk 都输出文字，而是等 VAD 判断这一段人声结束后，把累积音频送给 SenseVoice 生成文本。

注意：SemanticTurnDetector 代码里有 `HUMAN_DUPLEX_AUDIO_PARTIAL -> HUMAN_DUPLEX_TEXT_PARTIAL` 的早期打断设计入口，但当前 6006 配置没有给 SenseVoice 配 partial input/output override，所以主路径仍是完整 VAD 段结束后输出 `HUMAN_DUPLEX_TEXT`。

## 6. SemanticTurnDetector：决定是普通输入，还是打断

SemanticTurnDetector 在 duplex 模式下声明：

```python
inputs:
  HUMAN_DUPLEX_TEXT
  HUMAN_DUPLEX_AUDIO
  HUMAN_DUPLEX_TEXT_PARTIAL

outputs:
  HUMAN_TEXT
  HUMAN_DUPLEX_AUDIO_PARTIAL
```

它的核心职责有两个：

1. 如果用户是在数字人不说话时发言，把 `HUMAN_DUPLEX_TEXT` 转成 `HUMAN_TEXT` 给主 LLM。
2. 如果用户开始说话时数字人正在播放，先判断这是不是打断。

伪代码：

```python
def on_human_duplex_text(text, metadata, is_last_data):
    avatar_was_speaking = metadata["avatar_was_speaking_at_stream_start"]
    continue_from_stream = metadata.get("continue_from_stream")

    if avatar_was_speaking and enable_interrupt_detection:
        check_interrupt(text)
        return

    if not avatar_was_speaking:
        if enable_completion_detection:
            check_completion_or_wait(text)
        elif is_last_data:
            submit_human_text(text)
```

打断判断：

```python
def check_interrupt(text):
    if len(text) < min_text_length_for_interrupt:
        return

    if is_pure_stop_command(text):
        emit_interrupt(should_send_text=False)
        return

    avatar_text = get_current_avatar_text()

    if interrupt_on_any_speech:
        intent = judge_interrupt_intent(text, avatar_text)
        emit_interrupt(should_send_text=(intent == "has_new_topic"))
        return

    result = llm_detect_interrupt(text, avatar_text)

    if result == "打断":
        emit_interrupt(should_send_text=False)

        intent = judge_interrupt_intent(text, avatar_text)
        if intent == "has_new_topic":
            submit_human_text(text)
```

当前配置：

```yaml
SemanticTurnDetector:
  duplex_mode: true
  enable_interrupt_detection: true
  interrupt_on_any_speech: false
  enable_completion_detection: false
  min_text_length_for_interrupt: 2
  request_timeout: 3.0
```

所以当前行为是：

- 非打断场景：VAD 结束后，ASR 文本直接透传成 `HUMAN_TEXT`。
- 打断场景：需要语义 LLM 判断是否为打断。
- 如果判断为 `has_new_topic`，会先打断旧播放，再把插话文本作为新问题提交给主 LLM。

## 7. 主 LLM：HUMAN_TEXT 流式生成 AVATAR_TEXT

主 LLM handler：

```text
input:  HUMAN_TEXT
output: AVATAR_TEXT
```

当前配置：

```yaml
LLMOpenAICompatible:
  model_name: "qwen-plus"
  enable_video_input: False
  history_length: 20
  api_url: "https://dashscope.aliyuncs.com/compatible-mode/v1"
```

处理逻辑：

```python
def on_human_text(text, is_last_data):
    context.input_texts += text

    if not is_last_data:
        return

    messages = history.generate_next_messages(context.input_texts, image=None)
    completion = openai_client.chat.completions.create(
        model=model_name,
        messages=[system_prompt] + messages,
        stream=True,
    )

    for chunk in completion:
        if stream_key not in active_stream_keys:
            completion.close()
            break

        if chunk.delta.content:
            submit_avatar_text(chunk.delta.content)

    if not cancelled:
        history.add(human_text)
        history.add(avatar_text)
        submit_avatar_text("", finish_stream=True)
```

这里 `AVATAR_TEXT` 是流式输出的，后面的 TTS 可以边收到文本边合成音频。

取消逻辑：

```python
def on_stream_cancel(stream_key):
    active_stream_keys.discard(stream_key)
```

LLM streaming loop 每个 chunk 都检查 `active_stream_keys`，如果当前 stream 被 cancel，就关闭 completion 并停止继续产出。

## 8. TTS：AVATAR_TEXT 流式合成 AVATAR_AUDIO

CosyVoice TTS handler：

```text
input:  AVATAR_TEXT
output: AVATAR_AUDIO
```

当前配置：

```yaml
CosyVoice:
  voice: "longxiaochun"
  model_name: "cosyvoice-v1"
  sample_rate: 24000
```

处理逻辑：

```python
def on_avatar_text(data):
    input_stream = data.stream_id
    session = sessions.get(input_stream.key)

    if session is None:
        cancel_all_old_tts_sessions()
        session = new_tts_session(input_stream)
        open_avatar_audio_stream(source=input_stream)

    text = clean_text(data.text)

    if not data.is_last_data:
        if session.synthesizer is None:
            session.synthesizer = SpeechSynthesizer(
                model=model_name,
                voice=voice,
                callback=callback,
                format=PCM_24000HZ_MONO_16BIT,
            )
        session.synthesizer.streaming_call(text)
    else:
        session.synthesizer.streaming_call(text)
        session.synthesizer.streaming_complete()
        close_session()
```

TTS callback：

```python
def on_data(pcm_bytes):
    if session.cancelled:
        return

    temp_bytes += pcm_bytes

    if len(temp_bytes) > 24000:
        audio = int16_pcm_to_float32(temp_bytes)
        submit_avatar_audio(audio)
        temp_bytes = b""

def on_complete():
    if session.cancelled:
        clear_temp_bytes()
        return

    submit_remaining_audio()
    submit_small_silence_end_frame(finish_stream=True)
```

打断时：

```python
def on_stream_cancel(stream_key):
    if stream_key belongs to input_or_output_session:
        session.cancelled = True
        session.synthesizer.streaming_cancel()
```

## 9. FlashHead：AVATAR_AUDIO 驱动数字人视频，并回传同步音频

FlashHead handler：

```text
input:  AVATAR_AUDIO from TTS
output: AVATAR_VIDEO + AVATAR_AUDIO passthrough/synchronized audio
```

FlashHead 加载阶段：

```python
load():
    create output definitions:
        AVATAR_AUDIO: output_audio_sample_rate
        AVATAR_VIDEO: fps

    resolve ckpt_dir, wav2vec_dir, cond_image_path
    load FlashHead pipeline
    load condition image
    infer_params = get_infer_params()
```

session 启动：

```python
create_context():
    processor = FlashHeadProcessor(
        pipeline=shared_pipeline,
        infer_params=copy(infer_params),
        output_audio_sample_rate=24000,
    )
    processor.set_callbacks(on_video_frame, on_audio_frame, on_speech_end)

start_context():
    create CLIENT_PLAYBACK lifecycle streamer
    processor.start()
```

每次收到 TTS 音频：

```python
def on_avatar_audio(input_audio):
    stream_key = input_audio.stream_id

    if stream_key changed:
        processor.reset_interrupt()
        open_CLIENT_PLAYBACK_stream(source=input_audio.stream_id)

    playback_audio = input_audio.audio_float32_24k
    model_audio = resample(playback_audio, from_sr=24000, to_sr=16000)

    processor.add_audio(
        audio_data_16k=model_audio,
        original_audio=playback_audio,
        speech_id=stream_key,
        end_of_speech=input_audio.is_last_data,
    )
```

FlashHeadProcessor 内部：

```python
samples_per_video_frame = output_sample_rate // fps
# 24000 // 25 = 960 samples = 40 ms

def process_chunk(model_audio, playback_audio):
    video_frames = flashhead_pipeline.generate(model_audio)

    for i, frame in enumerate(video_frames):
        audio_segment = playback_audio[
            i * samples_per_video_frame:
            (i + 1) * samples_per_video_frame
        ]

        output_queue.put({
            "video_frame": frame,
            "audio_segment": audio_segment,
            "speech_id": speech_id,
            "end_of_speech": is_last_frame and end_of_speech,
        })

def frame_collector_loop():
    every 1 / fps seconds:
        item = output_queue.get_or_none()

        if interrupted and item is speech_frame:
            item = None

        if item:
            emit_video(item.video_frame)
            emit_audio(item.audio_segment)
        else:
            emit_video(idle_frame)
            emit_audio(silence(samples_per_video_frame))
```

这里 FlashHead 输出的 `AVATAR_AUDIO` 不是重新 TTS，而是和视频帧配对后的原始播放音频片段。这样 RTC 输出侧拿到的音频和视频在时间粒度上是一致的。

## 10. CLIENT_PLAYBACK：把“数字人正在播放”变成可观察生命周期

FlashHead 不只输出音视频，还维护一个 `CLIENT_PLAYBACK` lifecycle stream：

```python
when new TTS stream starts:
    open CLIENT_PLAYBACK stream

when processor reports speech_end:
    finish CLIENT_PLAYBACK stream

when interrupt cancels CLIENT_PLAYBACK:
    FlashHead receives STREAM_CANCEL
    processor.interrupt()
```

这个 stream 没有真实音视频数据，作用是：

- 让 session history 能知道数字人什么时候正在播放。
- 让 VAD 在用户说话开始时判断 `avatar_was_speaking_at_stream_start`。
- 让 InterruptHandler 有明确目标可取消。
- 让 SemanticTurnDetector 能判断当前是否存在 active playback。

## 11. 端到端伪代码

下面是去掉工程细节后的主链路伪代码：

```python
def on_browser_audio(audio_16k):
    submit(ChatData(type=MIC_AUDIO, audio=audio_16k))


def duplex_vad_on_mic_audio(audio_16k):
    speech_state = vad.update(audio_16k)

    if speech_state.started:
        metadata["avatar_was_speaking_at_stream_start"] = playback_is_active()

    if speech_state.audio_to_emit:
        submit(ChatData(
            type=HUMAN_DUPLEX_AUDIO,
            audio=speech_state.audio_to_emit,
            metadata=metadata,
            is_last_data=speech_state.ended,
        ))


def sensevoice_on_human_duplex_audio(audio_stream):
    # handler code sees HUMAN_AUDIO because of input_type_reverse_mapping
    buffer.append(audio_stream.audio)

    if audio_stream.is_last_data:
        text = sensevoice.transcribe(concat(buffer))
        submit(ChatData(
            type=HUMAN_DUPLEX_TEXT,
            text=text,
            metadata=audio_stream.metadata,
            is_last_data=True,
        ))


def semantic_turn_detector_on_duplex_text(text, metadata):
    if metadata.avatar_was_speaking_at_stream_start:
        if semantic_llm_says_interrupt(text):
            emit(INTERRUPT)
            if intent_is_new_topic(text):
                submit(ChatData(type=HUMAN_TEXT, text=text, is_last_data=True))
        return

    submit(ChatData(type=HUMAN_TEXT, text=text, is_last_data=True))


def main_llm_on_human_text(text):
    for delta in llm.stream(text):
        if cancelled:
            break
        submit(ChatData(type=AVATAR_TEXT, text=delta, is_last_data=False))

    submit(ChatData(type=AVATAR_TEXT, text="", is_last_data=True))


def tts_on_avatar_text(text_delta):
    audio_chunks = cosyvoice.streaming_call(text_delta)
    for audio_24k in audio_chunks:
        submit(ChatData(type=AVATAR_AUDIO, audio=audio_24k))

    if text_delta.is_last_data:
        submit(ChatData(type=AVATAR_AUDIO, audio=end_silence, is_last_data=True))


def flashhead_on_avatar_audio(audio_24k):
    model_audio_16k = resample(audio_24k, 24000, 16000)
    processor.add_audio(model_audio_16k, original_audio=audio_24k)


def flashhead_frame_collector():
    every 40_ms:
        frame, audio_960_samples = processor.next_frame_audio_pair()
        submit(ChatData(type=AVATAR_VIDEO, frame=frame))
        submit(ChatData(type=AVATAR_AUDIO, audio=audio_960_samples))


def rtc_output():
    audio = output_queues[AUDIO].get()
    video = output_queues[VIDEO].get()
    send_to_webrtc(audio, video)
```

## 12. 迁移实现时要保留的关键边界

1. **VAD 只负责切人声段，不直接调用 LLM。**

VAD 输出的是带 metadata 的人声音频 stream。

2. **ASR 只负责 audio -> text，不判断是否打断。**

是否打断交给 SemanticTurnDetector。

3. **SemanticTurnDetector 是人声文本到主对话文本的闸门。**

它决定 `HUMAN_DUPLEX_TEXT` 是否变成 `HUMAN_TEXT`。

4. **主 LLM 只消费 `HUMAN_TEXT`。**

这样普通文本输入、语音输入、插话新问题最终都能汇聚到同一个 LLM handler。

5. **TTS 是 streaming text -> streaming audio。**

LLM 不需要等完整回复结束，TTS 可以边收到 token 边合成。

6. **FlashHead 是 audio -> video + synchronized audio。**

它不是只产视频，而是重新按帧输出已经和视频对齐的播放音频。

7. **CLIENT_PLAYBACK 是打断和 duplex 判断的关键生命周期。**

没有它，VAD 和 SemanticTurnDetector 很难可靠知道数字人是否正在被用户听到。
