# 音箱端 MusicStatus 对接说明

## 目标

服务端会继续通过 WebRTC data channel 下发 `metadata.client_action`，让音箱端执行音乐播放或控制。音箱端执行后，需要通过同一条 WebRTC data channel 上行发送 `MusicStatus`，让服务端知道真实播放状态。

不需要新增 HTTP 接口，也不需要修改现有 `music.play` / `music.control` 的接收格式。

## 上行消息格式

```json
{
  "header": {
    "name": "MusicStatus",
    "request_id": "任意唯一ID"
  },
  "payload": {
    "state": "playing",
    "reason": "play_started",
    "title": "孤勇者",
    "artist": "陈奕迅",
    "url": "https://example.com/song.mp3",
    "position_ms": 12000,
    "duration_ms": 240000,
    "error": "optional error message"
  }
}
```

`payload.state` 必填，其余字段可选。

允许的 `state`：

```text
loading
playing
paused
stopped
ended
error
```

## 什么时候发送

音箱端执行 `music.play`：

| 时机 | state | reason |
| --- | --- | --- |
| 收到播放任务并开始加载 | `loading` | `play_requested` |
| 真正开始播放 | `playing` | `play_started` |
| 播放失败 | `error` | `play_failed` |
| 自然播完 | `ended` | `play_ended` |

音箱端执行 `music.control`：

| 控制动作 | 成功 state | reason |
| --- | --- | --- |
| `pause` | `paused` | `pause_control` |
| `resume` | `playing` | `resume_control` |
| `replay` / `restart` | `playing` | `replay_control` |
| `stop` | `stopped` | `stop_control` |
| `next` | `ended` | `next_control` |
| 控制失败 | `error` | `<action>_failed` |

## 最小实现要求

1. 复用当前 WebRTC data channel 发送 JSON。
2. `header.name` 固定为 `MusicStatus`。
3. `payload.state` 必须是允许枚举之一。
4. 播放失败或控制失败时发送 `state=error`，并尽量带上 `error` 字段。
5. 服务端暂不要求 `MusicStatusAck`，音箱端发送后无需等待确认。

为兼容尚未实现 `music.control/replay` 的音箱版本，服务端在保留了最近歌曲 URL 时，
会把用户的重播请求转换成一次新的 `music.play` 下发。音箱端应按普通点歌流程替换当前音频并从头播放。

## 示例

播放开始：

```json
{
  "header": { "name": "MusicStatus", "request_id": "music-status-001" },
  "payload": {
    "state": "playing",
    "reason": "play_started",
    "title": "孤勇者",
    "url": "https://example.com/song.mp3"
  }
}
```

暂停成功：

```json
{
  "header": { "name": "MusicStatus", "request_id": "music-status-002" },
  "payload": {
    "state": "paused",
    "reason": "pause_control"
  }
}
```

播放失败：

```json
{
  "header": { "name": "MusicStatus", "request_id": "music-status-003" },
  "payload": {
    "state": "error",
    "reason": "play_failed",
    "error": "audio decode failed"
  }
}
```

## 验收标准

用户点歌后，服务端日志能看到：

```text
MusicStatus received: state=playing active=True
```

用户说“暂停。”后，服务端应下发：

```text
Music client_action dispatch: type=music.control action=pause
```

音箱端暂停成功后，再上报：

```text
MusicStatus state=paused
```
