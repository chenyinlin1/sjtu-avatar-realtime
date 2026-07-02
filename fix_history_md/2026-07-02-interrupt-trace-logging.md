# 2026-07-02 interrupt trace logging

## 背景

当前数字人支持打断，但语音打断时体感会慢半拍。为了区分延迟发生在 VAD、ASR、语义判断、取消传播、FlashHead 队列还是 RTC 转发，本次先增加诊断日志，不调整任何打断策略、阈值或播放逻辑。

## 修改内容

- 在 Duplex VAD 中增加 `INTERRUPT_TRACE vad_speech_start`、`vad_early_end`、`vad_speech_end` 日志。
- 在 SenseVoice ASR 中增加 `asr_audio_stream_begin`、`asr_generate_start`、`asr_generate_done` 日志。
- 在 SemanticTurnDetector 中增加语音流、partial ASR、ASR 文本、打断检查、LLM 请求、打断信号发出等阶段日志。
- 在 InterruptHandler 中增加收到 `INTERRUPT` 和取消完成日志。
- 在 TTS、FlashHead handler、FlashHead processor 中增加收到取消、清理队列的日志。
- 在 RTC client handler 中增加向前端 DataChannel 转发 `stream_cancel` 的日志。

## 使用方式

复现一次语音打断后，可以在日志目录执行：

```bash
grep "INTERRUPT_TRACE" logs/*.log
```

重点观察这些阶段的耗时：

- `vad_speech_start -> vad_early_end`
- `semantic_partial_asr_sent -> semantic_partial_text_received`
- `asr_generate_start -> asr_generate_done`
- `interrupt_llm_parallel_start -> interrupt_llm_detect_done`
- `interrupt_signal_emitted -> interrupt_handler_received`
- `interrupt_handler_cancel_done -> flashhead_cancel_received`
- `flashhead_processor_interrupt_start -> flashhead_processor_interrupt_done`
- `rtc_signal_forward_start -> rtc_signal_forward_done`

如果慢在 `interrupt_llm_detect_done` 之前，主要是触发判断链路慢；如果慢在 `flashhead_processor_interrupt_done` 之后，主要看 FlashHead/RTC/前端播放缓冲。
