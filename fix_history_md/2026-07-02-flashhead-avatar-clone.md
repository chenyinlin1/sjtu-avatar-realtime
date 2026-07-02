# FlashHead 数字人克隆功能修改记录

日期：2026-07-02

## 修改背景

原来的 FlashHead 数字人形象固定使用：

```text
resource/avatar/flashhead/girl.png
```

本次修改的目标是让前端支持上传或拍摄人物图片，并把该图片传给后端作为 FlashHead 的驱动人物图。同时，为了避免用户误触，克隆必须在对话停止后进行，且拍摄/选择图片后需要先预览确认，不能直接开始克隆。

## 已实现内容

### 1. 后端支持上传人物图片

新增图片上传处理模块：

```text
src/service/frontend_service/avatar_image_upload.py
```

主要能力：

- 接收 JPEG、PNG、WebP 图片。
- 限制上传大小，默认最大 10 MB。
- 限制图片像素规模，避免异常大图占用资源。
- 使用 Pillow 读取图片并统一转换为 RGB PNG。
- 上传后的图片保存到：

```text
resource/avatar/flashhead/uploads/
```

### 2. 新增 FlashHead 克隆上传接口

修改文件：

```text
src/handlers/client/rtc_client/client_handler_rtc.py
```

新增接口：

```text
POST /openavatarchat/avatar/flashhead/image
```

接口行为：

- 如果当前存在活跃对话，会返回 `409`，提示需要先停止当前对话。
- 如果 FlashHead handler 未启用，会返回 `404`。
- 上传成功后，后端会调用 FlashHead handler 更新当前人物图。
- `initconfig` 会向前端下发：

```json
{
  "avatar_clone": {
    "enabled": true,
    "upload_route": "/openavatarchat/avatar/flashhead/image"
  }
}
```

### 3. FlashHead 支持动态更新人物图

修改文件：

```text
src/handlers/avatar/flashhead/avatar_handler_flashhead.py
```

新增能力：

- 增加 `update_condition_image(...)` 方法。
- 复用现有 FlashHead pipeline，不重新加载模型权重。
- 重新调用 FlashHead 的 `get_base_data(...)`，刷新 `cond_image`、`ref_img_latent` 和参考图。
- 保留已有 `use_face_crop`、`base_seed` 等配置逻辑。
- 使用锁保护更新过程，避免更新人物图时和推理流程同时修改共享 pipeline 状态。

### 4. 前端新增数字人克隆入口

修改/新增前端文件：

```text
src/service/frontend_service/frontend/src/renderer/src/components/ActionGroup.vue
src/service/frontend_service/frontend/src/renderer/src/components/AvatarCloneControl.vue
src/service/frontend_service/frontend/src/renderer/src/apis/index.ts
src/service/frontend_service/frontend/src/renderer/src/store/app.ts
```

前端行为：

- 工具栏显示“数字人克隆”入口。
- 对话进行中时禁止进入克隆流程，并提示先停止对话。
- 支持两种图片来源：
  - 本地图片
  - 摄像头拍摄
- 选择或拍摄图片后，不会直接上传。
- 用户会先进入预览确认状态。
- 只有点击“使用这张照片”后，才会调用后端克隆接口。

### 5. 优化摄像头拍摄界面

修改文件：

```text
src/service/frontend_service/frontend/src/renderer/src/components/AvatarCloneControl.vue
```

新增 UI 流程：

- 点击“数字人克隆”后打开克隆弹层。
- 弹层中可以切换“摄像头拍摄 / 本地图片”。
- 摄像头预览区域显示推荐人像轮廓：
  - 脸部椭圆框
  - 中心线
  - 肩部参考线
- 拍摄后展示照片预览。
- 提供：
  - 重新拍摄
  - 重新选择
  - 使用这张照片

这样用户可以明确知道自己正在进行数字人克隆，并确认最终上传的是哪张图片。

### 6. 工具栏新增独立打断按钮

修改文件：

```text
src/service/frontend_service/frontend/src/renderer/src/components/ActionGroup.vue
```

前端行为：

- 右侧工具栏新增“打断当前回复”入口，使用手掌图标。
- 当前没有回复播放时，按钮保留在工具栏中但处于禁用弱化状态。
- 当前正在回复时，按钮高亮为红色，点击后立即触发打断。
- WebRTC 模式下调用 `videoChatStore.interrupt()`。
- WebSocket 模式下调用 `wsChatStore.interrupt()`。

这样语音/视频对话界面不再只依赖输入框右侧的临时打断按钮，用户在常规工具栏里也能明确看到打断入口。

## 新增测试

新增测试文件：

```text
tests/test_flashhead_avatar_upload_service.py
tests/test_flashhead_avatar_refresh.py
tests/test_rtc_client_flashhead_avatar_upload.py
tests/test_frontend_avatar_clone_dialog.py
tests/test_frontend_action_group_interrupt.py
```

覆盖内容：

- 上传图片会被转换并保存为 PNG。
- 非图片文件会被拒绝。
- 超大文件会被拒绝。
- FlashHead 可以重新加载上传的人物图。
- 当前有活跃对话时，上传接口会拒绝克隆。
- 上传接口成功时会调用 FlashHead 更新方法。
- 前端克隆入口包含弹层、轮廓、预览确认。
- 摄像头拍照不会直接上传，只有确认后才上传。
- 前端工具栏包含独立打断按钮，并根据当前回复状态切换可用/禁用样式。
- 工具栏打断按钮会根据当前聊天模式分发到 WebRTC 或 WebSocket store。

## 验证记录

执行过的验证：

```bash
/root/autodl-tmp/miniconda3/envs/openavatarchat/bin/python -m pytest \
  tests/test_frontend_action_group_interrupt.py \
  tests/test_frontend_avatar_clone_dialog.py \
  tests/test_flashhead_avatar_upload_service.py \
  tests/test_flashhead_avatar_refresh.py \
  tests/test_rtc_client_flashhead_avatar_upload.py -q
```

结果：

```text
16 passed, 1 warning
```

前端构建：

```bash
cd src/service/frontend_service/frontend
./node_modules/.bin/vite build
```

结果：

```text
✓ built
```

最新构建产物包含：

```text
dist/assets/main.BKevWeKP.js
dist/assets/main.DtaOmPoq.css
```

## 注意事项

- 当前克隆能力是全局更新 FlashHead 人物图，不是每个 session 独立绑定不同人物图。
- 设计上要求用户先停止当前对话，再进行克隆。
- `vue-tsc` 目前仍会报告项目已有的未使用变量和缺失类型声明问题，但这些报错没有指向本次新增的 `AvatarCloneControl.vue`。
- 前端源码目录当前没有出现在主仓库 `git status` 的跟踪列表中，但远端运行使用的 `dist` 已经重新构建。
