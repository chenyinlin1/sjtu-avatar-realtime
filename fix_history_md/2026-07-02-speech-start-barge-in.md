# 2026-07-02 speech start barge-in

## 背景

`INTERRUPT_TRACE` 日志显示，自动打断原先需要等待 VAD early end、ASR 和 LLM 语义判断之后才发出 `INTERRUPT`。这会导致用户开口后数字人继续读一段，短句回复甚至可能已经播完。

## 修改内容

- 在 `SemanticTurnDetector` 中新增 `interrupt_on_speech_start` 配置，默认开启。
- 当 `HUMAN_DUPLEX_AUDIO` 新流进入时，如果当前存在活跃 `CLIENT_PLAYBACK`，立即发出 `INTERRUPT`，原因标记为 `speech_start_barge_in`。
- 这个预打断不刷新原有语义打断 cooldown，避免 ASR 文本很快返回时被 cooldown 拦住。
- 后续 ASR 和语义判断仍照常运行，用于判断用户这句话是否应该作为新的 `HUMAN_TEXT` 继续提交。
- `DuplexVAD` 会把 `avatar_was_speaking_at_stream_start` 直接写入音频 `DataBundle` metadata，保证第一帧音频也能带上开口时的播放状态。

## 预期效果

用户在数字人说话时再次开口，系统不再等待 ASR 和 LLM 语义判断才停止播放，而是在 VAD 确认人声开始后立即取消当前 `CLIENT_PLAYBACK`。这会把体感打断延迟从“VAD early end + ASR + LLM”缩短到“VAD speech start + cancel propagation”。

复现后仍可用以下命令看时间线：

```bash
grep "INTERRUPT_TRACE" logs/*.log
```

重点看：

- `vad_speech_start`
- `semantic_audio_stream_begin`
- `speech_start_barge_in_check`
- `interrupt_signal_emitted`
- `interrupt_handler_cancel_done`
- `flashhead_cancel_received`
