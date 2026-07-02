# 2026-07-03 Barge-in Text Passthrough

## 背景

快速打断改为“说话开始就取消数字人输出”后，出现一个新问题：

用户在数字人说话时开口，系统会立刻停止数字人；但后续 ASR 文本如果被语义模型判为“不打断”，旧逻辑只会结束判断，不会把这句话作为普通用户输入提交给 LLM。

结果就是用户实际说了内容，但界面不显示这句用户输入，也不会生成回复。

## 修改内容

- `src/handlers/llm/semantic_turn_detector/semantic_turn_detector_handler.py`
  - 在说话开始快速打断触发时，给当前 `HUMAN_DUPLEX_AUDIO` 流写入可继承 metadata：
    - `speech_start_barge_in_triggered=True`
  - 下游 `HUMAN_DUPLEX_TEXT` 会继承该标记。
  - 当完整 ASR 文本到达后，如果语义打断模型返回“不打断”，但该文本带有 `speech_start_barge_in_triggered=True`，则把它作为普通 `HUMAN_TEXT` 继续提交。
  - 对低信息量语气词做保护，例如“嗯”“哦”“好的”等，不会因为快速打断兜底逻辑而强行触发一轮回复。

- `tests/test_semantic_barge_in_passthrough.py`
  - 验证快速打断会标记当前人声流。
  - 验证带有快速打断标记的完整 ASR 文本，即使被判为“不打断”，也会继续提交为 `HUMAN_TEXT`。

## 预期效果

数字人正在说话时，用户插话会先快速停止旧音频；当用户这句话识别完成后，即使语义打断模型没有把它判成“打断”，只要它是实际内容，也会正常显示并进入后续 LLM 回复流程。
