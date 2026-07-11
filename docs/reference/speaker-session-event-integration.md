# 音箱 ClientEvent 与会话动作接入

服务端复用 WebRTC `text` data channel，不新增连接。所有消息沿用：

```json
{"header":{"name":"ClientEvent","request_id":"唯一ID"},"payload":{"type":"wake","ts":1783700000123,"data":{}}}
```

`ts` 是 epoch 毫秒。服务端默认拒绝超过 120 秒或超前 30 秒的事件；同一个 `request_id` 只处理一次。音箱必须先完成 `DeviceInfo`，建议额外上报 `timezone`，例如 `Asia/Shanghai`。

## 端到云事件

| type | data | 服务端行为 |
|---|---|---|
| `wake` | `{}` | 重置本轮沉默状态 |
| `user_silence` | `level`, `silence_ms` | 60 秒下发挽留；90 秒下发告别并结束 |
| `user_exit_hint` | `text` | 后台语义判断；明确结束才下发告别 |
| `ui_end` | `{}` | 打断当前服务端输出并下发 `session.end` |
| `reminder_capture` | `text` | 抽取时间和事项，下发 `reminder.create` |
| `reminder_ack` | `action_id`, `ok`, `reminder_id?`, `error?` | 成功后才确认“已记好”；失败提示重说 |
| `reminder_due` | `reminder_id`, `occurrence_id?`, `kind`, `priority`, `speak_text` | 根据优先级决定立即打断或当前回复后播报 |
| `play_error` | `action`, `error` | 记录执行失败 |

音箱的沉默计时必须排除用户说话、数字人播放、本地提示音和音乐播放时间。用户重新说话后重新从 Level 1 开始。

## 云到端动作

动作位于 `EchoAvatarText.payload.metadata.client_action`。动作都带 `action_id`，音箱应原样带回需要回执的事件。

### say

```json
{
  "type":"say",
  "action_id":"act-1",
  "text":"您还在听吗？",
  "then":"keep",
  "delivery":"after_current"
}
```

- `then=keep`：播完保持会话。
- `then=end`：播完后关闭 WebRTC，不能提前关闭。
- `delivery=interrupt`：停止当前数字人输出后立即播报。
- `delivery=after_current`：当前用户/数字人语句结束后播报，不能叠音。
- `delivery` 缺省时按现有 `say` 行为处理。

### session.end

```json
{"type":"session.end","action_id":"act-2","reason":"ui_end"}
```

收到后停止当前输出并关闭 WebRTC。

### reminder.create

```json
{
  "type":"reminder.create",
  "action_id":"rem-1",
  "kind":"custom",
  "title":"吃药",
  "remind_at":1783759200000,
  "repeat":"none",
  "speak_text":"该吃药啦",
  "timezone":"Asia/Shanghai"
}
```

音箱调用后台落库成功后上报：

```json
{"type":"reminder_ack","ts":1783700000123,"data":{"action_id":"rem-1","ok":true,"reminder_id":"123"}}
```

失败时 `ok=false` 并带 `error`。服务端在成功回执前不会确认提醒已创建。

## 到点打断规则

- `priority=critical/high` 或 `kind=medication/health`：服务端先发内部打断，再下发 `say.delivery=interrupt`。
- 其他提醒：下发 `say.delivery=after_current`，由音箱排队到当前语句结束。
- 音箱离线或 ai-bot 超时：继续走原有后台/端侧到点播报兜底，不能因 ai-bot 不可用丢提醒。

## 服务端配置

当前生产配置位于 `RtcClient.session_policy`。普通对话不调用额外模型；只有收到 `user_exit_hint` 或 `reminder_capture` 才进入最多 2.5 秒的后台模型任务。
