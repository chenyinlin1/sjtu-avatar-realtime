# OpenAvatarChat 正式目录 TTS v3.5 修复落地说明

适用目录：`/root/autodl-tmp/chatrobot_rebuild_workspace/OpenAvatarChat`

目标：让正式目录 6006 聊天机器人继续使用 `cosyvoice-v3.5-flash`，通过正确 voice 和参数修复 TTS 418，恢复机器人音频和 FlashHead 数字人播报。

## 1. 当前需要修复的问题

正式目录当前 6006 配置中仍是：

```yaml
CosyVoice:
  voice: "longanhuan_v3"
  model_name: "cosyvoice-v3.5-flash"
  instruction: "请用四川话表达。"
```

这个组合会被 DashScope/CosyVoice 服务端拒绝，典型错误：

```text
InvalidParameter / [cosyvoice:]Engine return error code: 418
```

已验证结论：

- `cosyvoice-v3.5-flash` 模型本身可用；
- `longanhuan_v3` 不是当前 v3.5 可用 voice；
- 当前账号下可用的 v3.5 克隆音色是：

```text
cosyvoice-v3.5-flash-fhd4c62a5-8983ff75d36a4ac3baf9ab34854d916a
```

## 2. 修改 6006 配置

文件：

```text
/root/autodl-tmp/chatrobot_rebuild_workspace/OpenAvatarChat/config/chat_with_openai_compatible_bailian_cosyvoice_flashhead_6006.yaml
```

把 `CosyVoice` 段改为：

```yaml
      CosyVoice:
        enabled: True
        module: tts/bailian_tts/tts_handler_cosyvoice_bailian
        voice: "cosyvoice-v3.5-flash-fhd4c62a5-8983ff75d36a4ac3baf9ab34854d916a"
        model_name: "cosyvoice-v3.5-flash"
        instruction: "请用四川话表达。"
        language_hints:
          - "zh"
        # api_key: "" # default=os.getenv("DASHSCOPE_API_KEY")
```

同时确认 `BailianASR` 保持为：

```yaml
      BailianASR:
        model_name: "fun-asr-realtime"
        sample_rate: 16000
        format: "pcm"
```

正式目录当前 ASR 已经是 `fun-asr-realtime`，一般不需要再改。

## 3. 修改 TTS handler

文件：

```text
/root/autodl-tmp/chatrobot_rebuild_workspace/OpenAvatarChat/src/handlers/tts/bailian_tts/tts_handler_cosyvoice_bailian.py
```

需要同步实验目录里已经验证过的 TTS handler 改动。最直接方式：

```bash
cp /root/autodl-tmp/chatrobot_rebuild_workspace/OpenAvatarChat_experiment/src/handlers/tts/bailian_tts/tts_handler_cosyvoice_bailian.py    /root/autodl-tmp/chatrobot_rebuild_workspace/OpenAvatarChat/src/handlers/tts/bailian_tts/tts_handler_cosyvoice_bailian.py
```

该文件包含两类必要改动：

1. 支持 v3.5 参数透传：

```python
volume
speech_rate
pitch_rate
seed
synthesis_type
language_hints
additional_params
```

这些参数会传入 `SpeechSynthesizer(...)`。

2. 保留 TTS 失败流保护：

```python
failed_input_stream_keys
_mark_session_failed(...)
```

作用：TTS 服务端报错后，同一个 LLM 文本流的后续 chunk 不再反复新建 TTS/FlashHead 流，避免 `speech_id mismatch` 这类次生错误。

## 4. 同步测试文件

文件：

```text
/root/autodl-tmp/chatrobot_rebuild_workspace/OpenAvatarChat/tests/test_tts_skips_client_action.py
```

建议同步实验目录测试：

```bash
cp /root/autodl-tmp/chatrobot_rebuild_workspace/OpenAvatarChat_experiment/tests/test_tts_skips_client_action.py    /root/autodl-tmp/chatrobot_rebuild_workspace/OpenAvatarChat/tests/test_tts_skips_client_action.py
```

该测试覆盖：

- client action 空文本不会创建 TTS；
- TTS 失败后同一输入流不会反复重建；
- v3.5 新增参数会实际传给 `SpeechSynthesizer`。

## 5. 验证

进入正式目录：

```bash
cd /root/autodl-tmp/chatrobot_rebuild_workspace/OpenAvatarChat
```

运行 TTS 单测：

```bash
PYTHONPATH=src /root/autodl-tmp/miniconda3/envs/openavatarchat/bin/python -m pytest tests/test_tts_skips_client_action.py
```

预期：

```text
3 passed
```

检查语法：

```bash
/root/autodl-tmp/miniconda3/envs/openavatarchat/bin/python -m py_compile src/handlers/tts/bailian_tts/tts_handler_cosyvoice_bailian.py
```

检查 YAML 是否能解析：

```bash
/root/autodl-tmp/miniconda3/envs/openavatarchat/bin/python -c "import yaml; from pathlib import Path; c=yaml.safe_load(Path('config/chat_with_openai_compatible_bailian_cosyvoice_flashhead_6006.yaml').read_text())['default']; print(c['chat_engine']['handler_configs']['CosyVoice'])"
```

输出中应看到：

```text
model_name: cosyvoice-v3.5-flash
voice: cosyvoice-v3.5-flash-fhd4c62a5-8983ff75d36a4ac3baf9ab34854d916a
language_hints: ['zh']
```

## 6. 重启服务

```bash
cd /root/autodl-tmp/chatrobot_rebuild_workspace/OpenAvatarChat
./start_realtime_human.sh
```

如果只想让启动脚本启动服务并退出 tail，可以用：

```bash
timeout 25s ./start_realtime_human.sh
```

`timeout` 返回 124 是正常的，它只会停止 tail，后端服务会继续运行。

## 7. 启动后检查日志

查看最新日志：

```bash
LATEST=$(ls -t /root/autodl-tmp/chatrobot_rebuild_workspace/OpenAvatarChat/logs/openavatarchat_6006_*.log | head -n 1)
grep -n "Registered handler CosyVoice\|BailianASR loaded\|TTS: Synthesis complete\|TTS: Service error\|Engine return error code: 418" "$LATEST"
```

应看到：

```text
model_name='cosyvoice-v3.5-flash'
voice='cosyvoice-v3.5-flash-fhd4c62a5-8983ff75d36a4ac3baf9ab34854d916a'
BailianASR loaded, model=fun-asr-realtime
```

前端对话后，期望看到：

```text
TTS: Synthesis complete
```

不应再出现：

```text
Engine return error code: 418
Model not found (fun-asr-flash-realtime)
```

## 8. 回滚

如果 v3.5 克隆音色后续被删除或失效，可以临时回退到早期止血方案：

```yaml
voice: "longanhuan_v3"
model_name: "cosyvoice-v3-flash"
```

但常规运行建议保持本说明中的 v3.5 配置，因为该组合已实际合成成功。
