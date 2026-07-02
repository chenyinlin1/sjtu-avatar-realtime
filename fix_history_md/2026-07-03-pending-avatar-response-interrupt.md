# 2026-07-03 Pending Avatar Response Interrupt

## 背景

快速打断已经可以在数字人正在播放 `CLIENT_PLAYBACK` 时立即生效，但有一个空窗期：

1. LLM 已经生成了上一条 `AVATAR_TEXT`。
2. TTS 已经创建或开始处理上一条 `AVATAR_AUDIO`。
3. FlashHead 还没有创建对应的 `CLIENT_PLAYBACK`。

如果用户在这个阶段再次说话，旧逻辑只检查 `CLIENT_PLAYBACK`，因此不会取消上一条待播放响应。后续新的文本回复会正常显示，但 FlashHead 仍可能继续播放上一条音频。

## 修改内容

- `src/handlers/llm/semantic_turn_detector/semantic_turn_detector_handler.py`
  - 新增活跃数字人响应统计：`CLIENT_PLAYBACK`、`AVATAR_AUDIO`、`AVATAR_TEXT`。
  - 说话开始打断判断不再只看 `CLIENT_PLAYBACK`，也会把待播放的 `AVATAR_AUDIO/AVATAR_TEXT` 视为可打断响应。
  - `_emit_interrupt_and_cancel` 的二次检查同步改为活跃数字人响应检查，避免已经判断要打断但最后因无 `CLIENT_PLAYBACK` 又跳过。
  - 日志补充 `active_avatar_audio_count` 和 `active_avatar_text_count`，便于定位待播放响应空窗期。

- `src/handlers/logic/interrupt/interrupt_handler.py`
  - `INTERRUPT` 不带 `related_stream` 时，取消目标选择从只支持 `CLIENT_PLAYBACK` 扩展为：
    1. 优先取消活跃 `CLIENT_PLAYBACK`。
    2. 没有播放流时取消活跃 `AVATAR_AUDIO`。
    3. 没有音频流时取消活跃 `AVATAR_TEXT`。
  - 保持只取消数字人响应链路，不主动取消当前用户正在输入的人声流。

- `tests/test_semantic_speech_start_interrupt.py`
  - 增加源码约束，确保说话开始打断会覆盖待播放数字人响应。

- `tests/test_interrupt_handler_pending_avatar_cancel.py`
  - 增加行为测试，验证无 `CLIENT_PLAYBACK` 但存在 `AVATAR_AUDIO` 时，`InterruptHandler` 会取消该待播放音频链路。

## 预期效果

用户在上一条回复已经显示、但数字人还没来得及开口时再次说话，上一条待播放音频会被立即取消。之后系统应该播放最新一轮回复，而不是继续播放上一轮已经过期的音频。
