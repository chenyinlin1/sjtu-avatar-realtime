# 音箱端语音转文字接口对接说明

## 1. 接口信息

- 请求方式：`POST`
- 请求地址：`/api/v1/asr/transcribe`
- 请求类型：`application/json`
- 鉴权方式：请求头携带 `secretKey`

完整地址示例：

```text
http://<服务端IP>:<端口>/api/v1/asr/transcribe
```

## 2. 音频数据要求

音箱端需要先录制完整的一句话，再一次性提交。

| 项目 | 要求 |
|---|---|
| 音频格式 | 裸 PCM，不包含 WAV 文件头 |
| 采样格式 | 16 位有符号整数，小端序（PCM S16LE） |
| 采样率 | 16000 Hz |
| 声道数 | 单声道 |
| 单次时长 | 默认不超过 30 秒 |
| 传输方式 | PCM 字节进行标准 Base64 编码后放入 JSON |

注意：`audio_base64` 中只填写 Base64 字符串，不要添加
`data:audio/...;base64,` 前缀。

## 3. 请求示例

请求头：

```http
Content-Type: application/json
secretKey: your-device-secret
```

请求体：

```json
{
  "audio_base64": "AAABAAIAAQAAAP//...",
  "audio_format": "pcm",
  "sample_rate": 16000,
  "channels": 1
}
```

其中只有 `audio_base64` 必须由音箱端填入。其他三个字段可以省略，
服务端默认按 PCM、16000 Hz、单声道处理。

音箱端处理流程：

```text
录制一句话
  → 得到 PCM S16LE 字节
  → 对全部 PCM 字节进行 Base64 编码
  → 放入 audio_base64
  → 发送 HTTP POST 请求
  → 读取 data.text
```

## 4. Python 调用示例

假设本地已有符合要求的 `audio.pcm`：

```python
import base64
import requests

with open("audio.pcm", "rb") as audio_file:
    audio_base64 = base64.b64encode(audio_file.read()).decode("ascii")

response = requests.post(
    "http://127.0.0.1:6006/api/v1/asr/transcribe",
    headers={
        "Content-Type": "application/json",
        "secretKey": "your-device-secret",
    },
    json={
        "audio_base64": audio_base64,
        "audio_format": "pcm",
        "sample_rate": 16000,
        "channels": 1,
    },
    timeout=35,
)

result = response.json()
if result["code"] == 0:
    print("识别文字：", result["data"]["text"])
else:
    print("识别失败：", result["code"], result["message"])
```

## 5. 成功响应

```json
{
  "code": 0,
  "message": "ok",
  "request_id": "req_123456",
  "data": {
    "text": "今天天气怎么样",
    "model_name": "fun-asr-realtime",
    "audio_format": "pcm",
    "sample_rate": 16000,
    "channels": 1,
    "duration_ms": 2350
  }
}
```

音箱端主要读取 `data.text`，其值就是最终识别文字。

## 6. 失败响应示例

```json
{
  "code": "INVALID_AUDIO",
  "message": "audio_base64 is invalid",
  "request_id": "req_123456",
  "data": {}
}
```

常见错误：

| code | 说明 |
|---|---|
| `UNAUTHORIZED` | `secretKey` 缺失或错误 |
| `INVALID_PARAM` | JSON 字段缺失或参数格式错误 |
| `INVALID_AUDIO` | Base64 无效、音频为空或 PCM 数据不完整 |
| `AUDIO_TOO_LARGE` | 音频超过服务端允许的时长 |
| `UPSTREAM_TIMEOUT` | 语音识别超时 |
| `UPSTREAM_ERROR` | 语音识别服务调用失败 |

建议音箱端为本次 HTTP 请求设置约 35 秒超时时间，并在网络错误或服务端
`5xx` 时根据业务需要进行有限次数重试。
