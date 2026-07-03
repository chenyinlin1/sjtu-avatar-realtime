# 2026-07-03 OpenAvatarChat 功能合并总说明

本文档把 `fix_history_md` 目录中已有的修改记录合并成一份总说明，方便后续两个分支合并时使用。

阅读对象默认是不熟悉项目代码的人，所以本文会尽量用直白语言说明：

- 现在项目新增了哪些功能。
- 每个功能解决了什么问题。
- 合并分支时哪些代码和行为必须保留。
- 合并后应该怎么测试。

---

## 1. 一句话总结

这批修改主要做了两件事：

1. **数字人克隆**：用户可以在前端上传本地图片或拍摄人物照片，确认后把这张图传给后端，FlashHead 会用这张图作为新的数字人形象。
2. **语音打断优化**：用户在数字人说话时插话，系统会更快停掉旧回答，并且不会再轻易出现“还在读旧回答”或“用户插话后没有回复”的问题。

另外，还补充了：

- 前端工具栏的独立打断按钮。
- 打断链路的时间戳日志。
- 面向多人协作的 Git 分支合并流程说明。

---

## 2. 先理解几个名词

如果你第一次看这个项目，可以先看这一节。

| 名词 | 通俗解释 |
| --- | --- |
| FlashHead | 负责把一张人物图驱动成会说话的数字人。 |
| 数字人克隆 | 用户上传或拍摄一张人物照片，让 FlashHead 改用这张照片作为数字人形象。 |
| VAD | 判断用户有没有开始说话、什么时候说完。 |
| ASR | 把用户语音转成文字。 |
| LLM | 大语言模型，负责根据用户文字生成回复。 |
| TTS | 把 LLM 生成的文字转成语音。 |
| `AVATAR_TEXT` | 数字人回复的文字流。 |
| `AVATAR_AUDIO` | 数字人回复的音频流。TTS 会产生一类，FlashHead 也会输出一类。 |
| `CLIENT_PLAYBACK` | 表示客户端或 FlashHead 正在播放某段数字人回复的生命周期流。 |
| speech-start barge-in | 用户一开口就打断数字人的机制，不等 ASR 完整识别。 |

一个普通回复的大致流程是：

```text
用户说话
  -> ASR 转文字
  -> LLM 生成数字人回复文本 AVATAR_TEXT
  -> TTS 生成数字人语音 AVATAR_AUDIO
  -> FlashHead 生成口型和音频输出
  -> 前端播放 CLIENT_PLAYBACK
```

打断要做的事情，就是把这条旧回复链路尽快停掉，同时继续处理用户刚刚插话说的新内容。

---

## 3. 数字人克隆功能

### 3.1 解决的问题

原来 FlashHead 固定使用这张图片：

```text
resource/avatar/flashhead/girl.png
```

也就是说，数字人形象是固定的。现在希望用户可以自己上传或拍摄一张人物照片，让 FlashHead 使用这张图作为新的数字人形象。

### 3.2 用户现在可以怎么用

前端工具栏里新增了“数字人克隆”入口。

用户点击后会进入克隆弹层，而不是直接开始克隆。弹层里有两种方式：

1. **本地图片**：从电脑里选择人物图片。
2. **摄像头拍摄**：打开摄像头拍摄人物照片。

摄像头界面会显示推荐人像轮廓，包括脸部椭圆框、中心线和肩部参考线，帮助用户把脸放到合适位置。

无论是选择图片还是拍照，前端都不会马上上传。用户会先看到预览，只有点击“使用这张照片”后才会真正提交给后端。

### 3.3 后端做了什么

新增图片上传处理：

```text
src/service/frontend_service/avatar_image_upload.py
```

主要能力：

- 支持 JPEG、PNG、WebP。
- 默认最大 10 MB。
- 检查图片像素规模，避免异常大图占用资源。
- 用 Pillow 读取图片，并统一转换为 RGB PNG。
- 上传后的图片保存在：

```text
resource/avatar/flashhead/uploads/
```

新增接口：

```text
POST /openavatarchat/avatar/flashhead/image
```

接口行为：

- 如果当前正在对话，会返回 `409`，要求先停止对话再克隆。
- 如果没有启用 FlashHead，会返回 `404`。
- 上传成功后，会调用 FlashHead 的更新逻辑，让数字人改用新图片。

前端初始化配置会拿到：

```json
{
  "avatar_clone": {
    "enabled": true,
    "upload_route": "/openavatarchat/avatar/flashhead/image"
  }
}
```

### 3.4 FlashHead 做了什么

修改文件：

```text
src/handlers/avatar/flashhead/avatar_handler_flashhead.py
```

FlashHead 新增动态更新人物图能力：

- 新增 `update_condition_image(...)`。
- 不重新加载大模型权重，只刷新人物图相关的基础数据。
- 重新生成 `cond_image`、`ref_img_latent` 和参考图。
- 保留 `use_face_crop`、`base_seed` 等原有配置。
- 使用锁保护更新过程，避免正在推理时同时修改共享状态。

### 3.5 前端涉及文件

主要涉及：

```text
src/service/frontend_service/frontend/src/renderer/src/components/ActionGroup.vue
src/service/frontend_service/frontend/src/renderer/src/components/AvatarCloneControl.vue
src/service/frontend_service/frontend/src/renderer/src/apis/index.ts
src/service/frontend_service/frontend/src/renderer/src/store/app.ts
```

### 3.6 合并时必须保留的行为

合并分支时，数字人克隆相关行为要同时保留：

- 工具栏有“数字人克隆”入口。
- 对话进行中不能克隆，要提示用户先停止对话。
- 支持本地图片和摄像头拍摄。
- 拍照界面要有人像轮廓提示。
- 图片选择或拍摄后必须先预览确认，不能直接上传。
- 后端必须校验图片类型、大小和像素规模。
- 后端必须把图片统一转换成 PNG。
- FlashHead 更新人物图时不能重新加载整套大模型。

---

## 4. 前端独立打断按钮

### 4.1 解决的问题

原来用户不容易在界面上找到“打断数字人当前回复”的入口。

现在右侧工具栏新增了独立打断按钮，使用手掌图标。

### 4.2 当前行为

- 没有数字人回复播放时，按钮保留但弱化禁用。
- 数字人正在回复时，按钮高亮为红色。
- 点击后立即触发打断。
- WebRTC 模式调用 `videoChatStore.interrupt()`。
- WebSocket 模式调用 `wsChatStore.interrupt()`。

### 4.3 合并时必须保留的行为

- 工具栏里要保留打断按钮。
- 按钮状态要跟“当前是否正在回复”联动。
- WebRTC 和 WebSocket 两种聊天模式都要能分发打断。

---

## 5. 打断链路日志

### 5.1 解决的问题

之前用户觉得“打断慢半拍”，但不知道慢在哪里。

为了定位问题，新增了一批 `INTERRUPT_TRACE` 时间戳日志。它们不改变业务逻辑，只记录每个环节花了多久。

### 5.2 主要日志点

新增日志覆盖这些阶段：

- VAD：用户开始说话、早停、说话结束。
- ASR：开始识别、识别完成。
- SemanticTurnDetector：收到音频、收到 ASR 文本、开始语义判断、发出中断。
- InterruptHandler：收到中断、取消完成。
- TTS / FlashHead：收到取消、清空队列。
- RTC client：把取消信号发给前端。

复现问题后可以看：

```bash
grep "INTERRUPT_TRACE" logs/*.log
```

常见判断方式：

- 如果慢在 `interrupt_llm_detect_done` 之前，通常是 ASR 或 LLM 判断慢。
- 如果慢在 `flashhead_processor_interrupt_done` 之后，通常要看 FlashHead、RTC 或前端播放缓冲。

### 5.3 合并时必须保留的行为

- 保留 `INTERRUPT_TRACE` 日志。
- 不要把时间戳日志删掉，否则后续打断问题会很难定位。

---

## 6. 语音打断优化

这一部分是最容易合并出错的地方，请重点看。

### 6.1 原来的问题

原来自动打断大致是：

```text
用户开口
  -> 等 VAD 判断用户可能说完一小段
  -> ASR 转文字
  -> LLM 判断是不是打断
  -> 发出 INTERRUPT
  -> 取消数字人播放
```

这个链路太长，所以用户开口后数字人还会继续读一段。

短回复甚至可能在系统判断完成前就已经读完。

### 6.2 现在的核心思路

现在开启了 `interrupt_on_speech_start`：

```text
用户一开口
  -> VAD 确认是人声
  -> 立即发出 speech_start_barge_in 中断
  -> 先停掉旧数字人声音
  -> 后续 ASR 和语义判断继续运行
  -> 再决定用户这句话要不要提交给 LLM
```

这让体感打断速度明显变快。

### 6.3 待播放响应也要能打断

后来发现还有一个空窗期：

```text
旧回答文字已经出来了
TTS 音频也可能开始生成了
但 FlashHead 还没创建 CLIENT_PLAYBACK
```

如果这时用户插话，旧逻辑只看 `CLIENT_PLAYBACK`，就会漏掉这条“还没开始播放但已经在生成”的旧回答。

现在已经改成：说话开始打断时，不只看 `CLIENT_PLAYBACK`，也会看：

- `AVATAR_TEXT`
- TTS 的 `AVATAR_AUDIO`
- `CLIENT_PLAYBACK`

这样用户在旧回复“显示了但还没开口读”的时候插话，也能取消旧回复。

### 6.4 多次打断时要取消完整旧回答链路

日志里还发现一种更复杂的问题：

用户多次打断后，前端已经显示第 n 次回答，但 FlashHead 还在读更早的第 n-3 次旧回答。

原因是旧逻辑有时只取消一个 `CLIENT_PLAYBACK` 叶子流，没有把同一轮旧回答的其他兄弟流一起取消。

现在 `InterruptHandler` 的规则是：

无 `related_stream` 的 `INTERRUPT` 到来时，一次性收集并取消所有活跃的数字人响应目标，顺序是：

1. `AVATAR_TEXT`
2. 上游 TTS `AVATAR_AUDIO`
3. `CLIENT_PLAYBACK`
4. FlashHead passthrough `AVATAR_AUDIO`

这样可以同时停掉旧 LLM 文本流、旧 TTS 音频流、旧播放流和 FlashHead 输出，避免旧音频重新打开播放。

### 6.5 用户插话后不能丢文本

快速打断后又出现过另一个问题：

用户插话确实打断了数字人，但用户刚刚说的话没有显示，也没有触发新回复。

原因是 speech-start barge-in 一开始只负责停播，所以会先用：

```text
should_send_text=False
```

后续必须等 ASR 文本回来，再决定是否提交给 LLM。

现在规则是：

- 如果语义判断为 `has_new_topic`，提交用户文本。
- 如果语义判断为 `pure_interrupt`，但这句话来自 speech-start barge-in，且不是低信息语气词，也不是明确纯停止命令，仍然提交用户文本。
- 如果进入 `text_too_short` 或 interrupt cooldown 分支，只要是 speech-start barge-in 后的实质性文本，也允许提交。
- 明确纯停止命令仍不提交。

举例：

| 用户说的话 | 系统应该怎么做 |
| --- | --- |
| “停” | 只停止旧回答，不生成新回复。 |
| “别说了” | 只停止旧回答，不生成新回复。 |
| “好你不要说话了” | 只停止旧回答，不生成新回复。 |
| “好你不要说话了换个别的话说” | 停止旧回答，并把这句话提交给 LLM，让数字人继续回复。 |
| “那算了，换个别的说” | 停止旧回答，并回复这个新请求。 |
| “对” | 如果来自 speech-start barge-in，作为实质性短文本提交，避免被静默吞掉。 |
| “嗯”“哦”“好的” | 低信息语气词，不强行触发新回复。 |

### 6.6 主要涉及文件

```text
src/handlers/llm/semantic_turn_detector/semantic_turn_detector_handler.py
src/handlers/logic/interrupt/interrupt_handler.py
src/handlers/vad/silerovad/duplex_vad_handler.py
src/handlers/asr/sensevoice/asr_handler_sensevoice.py
src/handlers/avatar/flashhead/avatar_handler_flashhead.py
src/handlers/avatar/flashhead/flashhead_processor.py
src/handlers/client/rtc_client/client_handler_rtc.py
```

其中合并时最容易冲突的是：

```text
src/handlers/llm/semantic_turn_detector/semantic_turn_detector_handler.py
src/handlers/logic/interrupt/interrupt_handler.py
```

### 6.7 合并时必须保留的行为

合并分支时，不要只保留其中一部分。下面这些都要保留：

- 用户一开口就能快速打断，不能重新变成“等 ASR + LLM 判断后才打断”。
- 打断判断不能只看 `CLIENT_PLAYBACK`，还要覆盖待播放的 `AVATAR_TEXT` 和 TTS `AVATAR_AUDIO`。
- 无 `related_stream` 的中断要取消完整旧回答链路，不能只取消一个最高优先级流。
- TTS `AVATAR_AUDIO` 和 FlashHead passthrough `AVATAR_AUDIO` 要区分，不能只取消 FlashHead 输出。
- speech-start barge-in 后的实质性 ASR 文本要能提交给 LLM。
- 低信息语气词和明确纯停止命令不能误触发新回复。
- 纯停止判断不能退回简单 `startswith` / `endswith`，否则“不要说话了换个别的说”会被误吞。

---

## 7. 分支合并时重点检查哪些文件

如果两个分支同时改了这些文件，请认真处理冲突。

### 7.1 数字人克隆相关

```text
src/service/frontend_service/avatar_image_upload.py
src/handlers/client/rtc_client/client_handler_rtc.py
src/handlers/avatar/flashhead/avatar_handler_flashhead.py
src/service/frontend_service/frontend/src/renderer/src/components/ActionGroup.vue
src/service/frontend_service/frontend/src/renderer/src/components/AvatarCloneControl.vue
src/service/frontend_service/frontend/src/renderer/src/apis/index.ts
src/service/frontend_service/frontend/src/renderer/src/store/app.ts
```

### 7.2 打断相关

```text
src/handlers/logic/interrupt/interrupt_handler.py
src/handlers/llm/semantic_turn_detector/semantic_turn_detector_handler.py
src/handlers/vad/silerovad/duplex_vad_handler.py
src/handlers/asr/sensevoice/asr_handler_sensevoice.py
src/handlers/avatar/flashhead/avatar_handler_flashhead.py
src/handlers/avatar/flashhead/flashhead_processor.py
src/handlers/client/rtc_client/client_handler_rtc.py
```

### 7.3 测试相关

```text
tests/test_flashhead_avatar_upload_service.py
tests/test_flashhead_avatar_refresh.py
tests/test_rtc_client_flashhead_avatar_upload.py
tests/test_frontend_avatar_clone_dialog.py
tests/test_frontend_action_group_interrupt.py
tests/test_semantic_speech_start_interrupt.py
tests/test_interrupt_handler_pending_avatar_cancel.py
tests/test_semantic_barge_in_passthrough.py
```

---

## 8. 合并后怎么测试

### 8.1 数字人克隆相关测试

```bash
/root/autodl-tmp/miniconda3/envs/openavatarchat/bin/python -m pytest \
  tests/test_frontend_action_group_interrupt.py \
  tests/test_frontend_avatar_clone_dialog.py \
  tests/test_flashhead_avatar_upload_service.py \
  tests/test_flashhead_avatar_refresh.py \
  tests/test_rtc_client_flashhead_avatar_upload.py -q
```

历史验证结果：

```text
16 passed, 1 warning
```

### 8.2 打断相关测试

```bash
PYTHONPATH=src /root/autodl-tmp/miniconda3/envs/openavatarchat/bin/pytest \
  tests/test_interrupt_handler_pending_avatar_cancel.py \
  tests/test_semantic_barge_in_passthrough.py \
  tests/test_semantic_speech_start_interrupt.py \
  tests/test_frontend_action_group_interrupt.py -q
```

历史验证结果：

```text
13 passed
```

### 8.3 全量测试

全量测试要在项目根目录执行，并使用双路径 `PYTHONPATH`：

```bash
PYTHONPATH=.:src /root/autodl-tmp/miniconda3/envs/openavatarchat/bin/pytest tests -q
```

历史验证结果：

```text
56 passed, 1 warning
```

如果只使用 `PYTHONPATH=src` 跑全量测试，部分测试可能会因为 `from src...` 导入路径报错。这个是测试运行方式问题，不一定是业务代码问题。

### 8.4 前端构建

```bash
cd src/service/frontend_service/frontend
./node_modules/.bin/vite build
```

历史验证结果：

```text
✓ built
```

---

## 9. 合并后手动验证建议

自动测试通过后，建议再人工试一下这些场景。

### 9.1 数字人克隆

1. 打开前端页面。
2. 点击“数字人克隆”。
3. 选择本地图片，确认会先预览，不会直接上传。
4. 点击“使用这张照片”，确认数字人形象更新。
5. 使用摄像头拍摄，确认界面有人像轮廓。
6. 对话进行中尝试克隆，确认系统提示先停止对话。

### 9.2 工具栏打断按钮

1. 数字人不说话时，打断按钮应该是禁用弱化状态。
2. 数字人说话时，打断按钮应该高亮。
3. 点击打断按钮，数字人应该停止当前回复。

### 9.3 语音打断

建议试三组语音：

1. 数字人说长句时连续打断两到三次，确认不会继续读旧回答。
2. 数字人刚显示一段新文本但还没开始读时，说“那算了，换个别的说”，确认会回复最新这句话，而不是读上一段。
3. 分别测试：
   - “好你不要说话了”：应该只停止，不追加新回复。
   - “好你不要说话了换个别的话说”：应该停止旧回答，并继续回复“换个别的话说”这个新请求。

---

## 10. Git 分支合并建议

已有文档 `git_branch_merge_workflow.md` 记录了推荐流程。这里用更短的话总结。

推荐流程是：

```text
先让开发分支合并主分支最新代码
  -> 在开发分支上解决冲突
  -> 在开发分支上测试
  -> 测试通过后再合并回主分支
  -> 最后推送主分支
```

如果你的分支是：

```text
本地主分支：clean-main
开发分支：lgf-realtime
远程主分支：origin/main
```

推荐命令：

```bash
git status
git checkout clean-main
git pull origin main

git checkout lgf-realtime
git merge clean-main

# 如果有冲突：手动解决冲突
git add .
git commit

# 在 lgf-realtime 上跑测试

git checkout clean-main
git merge --no-ff lgf-realtime -m "merge: lgf-realtime into clean-main"
git push origin clean-main:main
```

不要轻易使用：

```bash
git push origin clean-main:main --force
```

`--force` 可能会覆盖别人已经推送到远程主分支的代码。

---

## 11. 最容易合并错的地方

### 11.1 只保留了快速打断，但丢了文本提交

表现：

```text
用户一开口，数字人停了；
但用户刚才说的话没有显示，也没有回复。
```

应该检查：

```text
src/handlers/llm/semantic_turn_detector/semantic_turn_detector_handler.py
```

确认 speech-start barge-in 后的实质性 ASR 文本会提交为 `HUMAN_TEXT`。

### 11.2 只取消了播放流，没有取消旧 TTS/LLM

表现：

```text
新回答已经显示了；
数字人却还在读旧回答。
```

应该检查：

```text
src/handlers/logic/interrupt/interrupt_handler.py
```

确认无 `related_stream` 的中断会取消完整旧回答链路，而不是只取消一个 `CLIENT_PLAYBACK`。

### 11.3 把复合句误判成纯停止

表现：

```text
用户说“不要说话了换个别的说”；
系统只停止，不回复“换个别的说”。
```

应该检查：

```text
_is_pure_stop_command(...)
_should_submit_barge_in_text_after_interrupt(...)
```

纯停止判断应该是精确判断，不要简单使用 `startswith` / `endswith`。

### 11.4 数字人克隆变成选择后直接上传

表现：

```text
用户一拍照或一选图，系统马上上传并克隆。
```

这是不符合当前设计的。必须保留预览确认步骤，只有点击“使用这张照片”后才上传。

---

## 12. 当前已知注意事项

- 数字人克隆目前是全局更新 FlashHead 人物图，不是每个 session 单独绑定不同人物图。
- 设计上要求先停止当前对话，再进行克隆。
- 前端源码目录在某些分支里可能没有完整出现在主仓库 `git status` 跟踪列表中，但运行使用的是构建后的 `dist`。
- `vue-tsc` 可能仍会报告项目已有的未使用变量或缺失类型声明问题，不能只看这一个命令判断本次功能是否坏了。
- 打断日志可能包含服务配置和 RTC 信息，分享日志时要注意不要暴露密钥或凭据。

---

## 13. 本文合并了哪些历史文档

本文综合了以下文件内容：

```text
fix_history_md/2026-07-02-flashhead-avatar-clone.md
fix_history_md/2026-07-02-interrupt-trace-logging.md
fix_history_md/2026-07-02-speech-start-barge-in.md
fix_history_md/2026-07-03-barge-in-text-passthrough.md
fix_history_md/2026-07-03-pending-avatar-response-interrupt.md
fix_history_md/2026-07-03-interrupt-multi-cancel-and-barge-in-submit.md
fix_history_md/2026-07-03-branch-merge-interrupt-fixes.md
fix_history_md/git_branch_merge_workflow.md
```

如果后续有人只想快速了解本轮功能合并，优先看本文即可。
