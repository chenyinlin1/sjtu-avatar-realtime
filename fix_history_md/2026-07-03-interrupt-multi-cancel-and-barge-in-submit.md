# 2026-07-03 多次打断旧音频残留与新请求丢失修复

## 背景

日志中出现两类问题：

1. 用户多次打断后，前端已经显示新的第 n 次回答，但 FlashHead 仍在播放更早的旧回答音频。
2. 用户打断后说了新话，有时没有显示用户文本，也没有触发新的数字人回复。

## 原因

旧音频残留的主要原因是 `InterruptHandler` 原先只选择一个最高优先级流取消。多次打断时，同一轮旧回答可能同时存在 `AVATAR_TEXT`、TTS `AVATAR_AUDIO`、`CLIENT_PLAYBACK` 和 FlashHead 输出音频。只取消一个 `CLIENT_PLAYBACK` 叶子流时，旧 TTS 的后续兄弟流仍可能重新打开播放流。

新请求丢失的主要原因是 speech-start barge-in 会先以 `should_send_text=False` 快速停播，后续 ASR 文本只有被意图判断成 `has_new_topic` 才会提交给 LLM。像“不要说话了，换个别的话说”这种复合句容易被判成 `pure_interrupt`，导致只停播不提交。另外 ASR 偶尔只识别到 1 个字时，会被 `text_too_short` 分支直接跳过。

## 修改内容

- `src/handlers/logic/interrupt/interrupt_handler.py`
  - 无 `related_stream` 的中断不再只选择一个目标。
  - 改为按顺序取消所有活跃数字人响应目标：`AVATAR_TEXT`、上游 TTS `AVATAR_AUDIO`、`CLIENT_PLAYBACK`、FlashHead passthrough `AVATAR_AUDIO`。
  - 排除当前用户输入流，避免把正在识别/提交的用户文本误取消。

- `src/handlers/llm/semantic_turn_detector/semantic_turn_detector_handler.py`
  - 新增 speech-start barge-in 后的文本提交保护。
  - 如果最终 ASR 文本不是低信息语气词，也不是明确纯停止命令，即使意图分类返回 `pure_interrupt`，也提交为 `HUMAN_TEXT`。
  - `text_too_short` 和 interrupt cooldown 分支中，允许 speech-start barge-in 的实质性短文本继续提交。

## 测试

新增/更新测试：

- `tests/test_interrupt_handler_pending_avatar_cancel.py`
  - 覆盖一次中断同时取消旧 `AVATAR_TEXT`、TTS `AVATAR_AUDIO`、`CLIENT_PLAYBACK` 和 FlashHead 输出音频。

- `tests/test_semantic_barge_in_passthrough.py`
  - 覆盖“不要说话了 + 换个别的话说”被判为 `pure_interrupt` 时仍提交用户文本。
  - 覆盖 speech-start barge-in 后 1 字实质性文本不被静默吞掉。
  - 覆盖“好你不要说话了”这类明确纯停止命令不会被误提交。

验证命令：

```bash
PYTHONPATH=src /root/autodl-tmp/miniconda3/envs/openavatarchat/bin/pytest \
  tests/test_interrupt_handler_pending_avatar_cancel.py \
  tests/test_semantic_barge_in_passthrough.py \
  tests/test_semantic_speech_start_interrupt.py \
  tests/test_frontend_action_group_interrupt.py -q
```

结果：`12 passed`。

补充纯停止边界后结果：`13 passed`。
