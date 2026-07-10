# 灵心智伴 · 数字人 AI-Bot（OpenAvatarChat）对接规范

> **文档面向**：灵心智伴后台（data-cloud）、Android 音箱端、联调人员
> **对端**：ai-bot = OpenAvatarChat（SJTU-avatar-realtime 分支）
> **版本**：v2.0 —— **依据 ai-bot 侧现网代码定稿，非草案**
> **状态**：已实施。v1.0 是我方单方面提出的草案，其中 NDJSON 聊天流、独立 WS 数字人流、`X-Api-Key`/`X-Device-Key` 分家等设计**对端未采纳**，见 §11 与附录 B。

---

## 0. 修订记录

| 版本 | 日期 | 变更 |
|---|---|---|
| v1.0 | 2026-07-04 | 我方草案：persona 一等资源、NDJSON 聊天流、WS 帧流、双密钥鉴权 |
| **v2.0** | **2026-07-09** | **按 ai-bot 现网实现重写**。三处硬变更：① 鉴权收敛为单把 `secretKey`，`X-Api-Key` 废弃；② 主对话链路定为 **WebRTC**，NDJSON 聊天流与独立 WS 帧流不实现；③ TTS 路径改 `POST /api/v1/tts/synthesize`，响应内联 base64 WAV，不再返回 `audio_url` |
| v2.1 | 2026-07-10 | 新增 `client_action` 类型 **`session.end`**（§8.5.4）：**会话结束的语义判断收归服务端**，设备端不再做结束语关键词匹配 |

### 0.1 一句话现状

- **persona（角色）仍是一等资源**，管理面 `/api/v1/personas...` 全部落地 —— 草案的核心概念被采纳。
- **主对话链路走 WebRTC**（音频上行 / 语音+数字人下行 / data channel 信令），不是草案里的 NDJSON + WS 帧流。
- **主动播报走 `POST /api/v1/tts/synthesize`**，一次性 HTTP 合成，返回 base64 WAV，**不驱动数字人**。
- **鉴权只有一把钥匙**：HTTP header `secretKey`。

### 0.2 术语

| 术语 | 含义 |
|---|---|
| **elder（老人）** | 被陪伴的老人，平台内唯一 `elder_id`（属于某租户） |
| **persona（角色形象）** | 老人身边的一个「人设」（儿子·小明 / 女儿·小丽）。各自独立的**音色 + 形象 + 关系称谓 + 人设 prompt**。老人 : persona = **1 : N** |
| **device（音箱）** | 老人家中的 Android 音箱，唯一 `device_sn`（来自 `ro.serialno`） |
| **控制面 / 数据面 / 体征面** | 控制面 = 设备 ↔ 灵心后台（绑定/激活/心跳/提醒）；数据面 = 设备 ↔ ai-bot（WebRTC 实时对话）；体征面 = 雷达 → 后台 → ai-bot，**端侧不参与** |

---

## 1. 两条链路

```
                    ┌──────────────────────────┐
                    │   灵心后台 data-cloud      │
     控制面(HMAC)    │  (iot-server)            │
  ┌───────────────▶ │                          │
  │  绑定/心跳/提醒   │  管理面 secretKey         │
  │                 │  · persona 注册/音色/形象  │──┐
┌─┴──────────┐      │  · TTS 主动播报            │  │
│ Android音箱 │      └──────────────────────────┘  │
│ (device_sn)│                                     │
└─┬──────────┘                                     ▼
  │            数据面 WebRTC          ┌──────────────────────────────┐
  └────────────────────────────────▶ │        ai-bot 服务            │
     mic上行 / 语音+数字人下行 / DC信令  │ ASR→LLM(人设)→TTS(persona音色) │
                                     │        →Avatar(persona形象)   │
                                     └──────────────────────────────┘
```

**管理面（后台 → ai-bot）**：注册/更新 persona、上传音色、上传形象、查询状态、TTS 主动播报。
**数据面（设备 → ai-bot）**：WebRTC 直连实时对话。

> **为什么设备直连 ai-bot**：数字人音视频是长连接、大流量、低时延的实时流，经后台中转会显著增加时延与卡顿风险。后台只负责下发直连所需的 `base_url` 与 `secretKey`（握手接口下发）。
>
> **为什么 TTS 播报反而经后台**：主动播报不是实时流，且**设备端只允许下载我方文件库地址**（守三平面）。后台合成后转存文件库，再把我方 URL 推给设备。

### 1.1 全流程

| 步骤 | 参与方 | 接口 |
|---|---|---|
| ① 小程序创建角色（关系/称谓） | 后台 → ai-bot | `PUT /api/v1/personas/{persona_id}` |
| ② 上传声音、照片素材 | 后台 → ai-bot | `POST …/voice`、`POST …/face` |
| ③ ai-bot 落库并建标识关系 | ai-bot | 上述接口内部完成；`GET …/personas/{id}` 查就绪 |
| ④ 查看 / 修改 / 多角色 | 后台 ↔ ai-bot | `GET /personas`、`PUT`、`DELETE` |
| ⑤ 音箱唤醒，建 WebRTC | 设备 → ai-bot | `POST /webrtc/offer` |
| ⑥ 登记身份 + 选 persona | 设备 → ai-bot | data channel `DeviceInfo` → `DeviceInfoAck` |
| ⑦ 说话 / 应答 / 抢话 | 双向 | WebRTC audio/video track + data channel `Interrupt` |
| ⑧ 主动播报（提醒/留言） | 后台 → ai-bot → 设备 | `POST /api/v1/tts/synthesize` → 转存文件库 → 推设备 |

时序图见附录 A。

---

## 2. Base URL 与鉴权

### 2.1 Base URL

| 环境 | 地址 |
|---|---|
| 联调 | `http://115.159.81.115:6006` |
| 本机 | `http://127.0.0.1:6006` |
| 生产（反代） | `https://avatar.example.com` |

- ai-bot 的 `base_url` 由后台配置并在设备握手时下发，**设备与 ai-bot 都不硬编码**。
- 我方配置项：`lingxin.iot.vendor.chatrobot.base-url`（不带末尾斜杠）。
- 地址走 ngrok 时变更频繁，**引用前一律去 `application*.yaml` 确认当前值**，勿凭记忆写死。

### 2.2 secretKey 单把钥匙

**所有受保护接口统一使用请求头**：

```
secretKey: <secretKey>
```

- 服务端从环境变量按序取第一个非空值：`DEVICE_SECRET_KEY` → `DEVICE_KEY` → `CHATROBOT_SECRET_KEY`。
- **`X-Api-Key` 已彻底废弃**。只传 `X-Api-Key` 会被 persona 接口与 TTS 接口直接拒绝（401 `invalid api key`）。
- 我方配置项：`lingxin.iot.vendor.chatrobot.device-secret-key`，值必须与服务端环境变量一致。建议放环境变量，勿提交进 git。
- HTTP Header 名大小写不敏感，但文档与代码统一写 `secretKey`。

### 2.3 鉴权覆盖面

**需要 `secretKey`**

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/api/v1/personas?elder_id=…&tenant_id=…` | 列出老人全部 persona |
| PUT | `/api/v1/personas/{persona_id}` | 创建 / 更新 persona 元数据 |
| GET | `/api/v1/personas/{persona_id}` | 查询单个 persona |
| DELETE | `/api/v1/personas/{persona_id}` | 删除 persona |
| POST | `/api/v1/personas/{persona_id}/voice` | 上传音色素材 |
| POST | `/api/v1/personas/{persona_id}/face` | 上传形象素材 |
| POST | `/api/v1/personas/{persona_id}/voice:reset` | 重置音色 |
| POST | `/api/v1/personas/{persona_id}/face:reset` | 重置形象 |
| POST | `/api/v1/tts/synthesize` | 文字转语音 |

**当前不需要 `secretKey`**

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/v1/health` | 健康检查 |
| GET | `/api/v1/version` | 版本检查 |
| POST | `/webrtc/offer` | WebRTC 建连，现网核心代码未校验 |
| GET | `/openavatarchat/initconfig` | WebUI 初始化配置 |

> 音箱端调 `/webrtc/offer` **仍建议带上 `secretKey`**，便于日后网关或服务端统一收口，届时无需改端。

---

## 3. 统一响应与错误码

### 3.1 成功（非流式 `/api/v1` 接口）

```json
{
  "code": 0,
  "message": "ok",
  "request_id": "req_8b2a1c...",
  "data": {}
}
```

- `request_id` 由 ai-bot 生成，**我方须在日志中透传**，便于两侧对账排查。
- 成功时 `code` 是数字 `0`；**失败时 `code` 是字符串错误码**（如 `"UNAUTHORIZED"`）。客户端解析时按文本比对，勿按 int 反序列化。

### 3.2 失败

```json
{
  "code": "UNAUTHORIZED",
  "message": "invalid api key",
  "request_id": "req_8b2a1c...",
  "data": {}
}
```

| HTTP | code | message 示例 | 常见原因 |
|---|---|---|---|
| 401 | `UNAUTHORIZED` | `DEVICE_SECRET_KEY is not configured` | 服务端未配置密钥环境变量 |
| 401 | `UNAUTHORIZED` | `invalid api key` | 没传 `secretKey` / 值不匹配 / 误传 `X-Api-Key` / 代理层剥了自定义头 |
| 400 | `INVALID_PARAM` | `Field required` | 缺必填字段，如 `display_name`、`text` |
| 400 | `INVALID_PARAM` | `elder_id and tenant_id cannot be changed` | 更新已有 persona 时试图改 owner |
| 403 | `FORBIDDEN` | `persona does not belong to tenant` | `persona_id` 与 `tenant_id`/`elder_id` 不匹配 |
| 404 | `PERSONA_NOT_FOUND` | `persona not found` | persona 未创建或 id 写错 |
| 502 | `UPSTREAM_TIMEOUT` | `failed to synthesize speech: TTS upstream timeout` | 百炼 TTS 超时 |
| 502 | `UPSTREAM_ERROR` / `INTERNAL_ERROR` | `failed to create voice clone…` / `failed to synthesize speech…` | 素材 URL 拉取失败、百炼克隆/合成失败、SDK 不可用 |

---

## 4. 身份与资源模型

### 4.1 关系图

```
tenant (租户 tenant_id)
  └── elder (老人 elder_id)
        ├── persona A  ← 音色A + 形象A + 关系"儿子" + 人设A   (is_default=true)
        ├── persona B  ← 音色B + 形象B + 关系"女儿" + 人设B
        └── persona C  …
  └── device (音箱 device_sn) ──绑定──▶ elder_id
                                        └─ 会话内经 DeviceInfo 选定一个 persona_id
```

- **`persona_id` 由后台生成并下发**，ai-bot 当**不透明字符串**存储、不解析其结构。我方格式：`p_{tenantId}_{elderId}_{uuid}`。
- **`elder_id` / `tenant_id` 一律传纯数字字符串**（如 `"10086"`、`"20007"`）。注册 persona 与调 TTS 时口径必须一致，否则 ai-bot 找不回默认 persona。
- 一个老人至多一个 `is_default=true` 的 persona。老人无任何 persona 时，设备不下发 `persona_id`，ai-bot 用默认音色 / 默认形象。

### 4.2 persona 对象

`GET /api/v1/personas/{persona_id}` 的 `data`：

```json
{
  "persona_id": "p_1_10086_son",
  "elder_id": "10086",
  "tenant_id": "1",
  "relationship": "儿子",
  "display_name": "儿子小明",
  "address_to_elder": "爸",
  "self_reference": "小明",
  "gender": "male",
  "persona_prompt": "你是老人的儿子小明，说话亲切、短句、语速慢，多关心身体。",
  "is_default": true,
  "voice": {
    "status": "READY",
    "ref_text": "爸，我是小明，今天感觉怎么样？",
    "sample_duration_ms": 8000,
    "voice_id": "cosyvoice-xxx",
    "model_name": "cosyvoice-v3-flash",
    "sample_path": "…",
    "clone_source_url": "https://files.example.com/voice.wav",
    "fail_reason": null,
    "updated_at": 1783155600000
  },
  "face": {
    "status": "READY",
    "image_path": "…/face.jpg",
    "fail_reason": null,
    "updated_at": 1783155600000
  },
  "status": "READY",
  "created_at": 1783155500000,
  "updated_at": 1783155600000
}
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `elder_id` | string | 是 | 归属老人，**创建后不可改** |
| `tenant_id` | string | 是 | 租户隔离，**创建后不可改** |
| `display_name` | string | 是 | 展示名 |
| `relationship` | string | 否 | 关系文案，**自由文本**（"儿子"/"女儿"/"孙女"），非枚举 |
| `address_to_elder` | string | 否 | 角色对老人的称呼（"爸"/"妈"），注入 LLM |
| `self_reference` | string | 否 | 角色自称（"小明"） |
| `gender` | string | 否 | 性别描述，服务端当普通字符串保存 |
| `persona_prompt` | string | 否 | 人设提示词，注入 LLM system prompt |
| `is_default` | bool | 否 | 是否该老人默认 persona |

> **`relationship` / `gender` 不是枚举**。v1.0 草案里的 `SON/DAUGHTER/…`、`MALE/FEMALE/UNKNOWN` 枚举**未被采纳**，现网当自由字符串存。我方 `AiBotPersonaClient` 仍发 `male/female/unknown`，属兼容写法，无害。

### 4.3 状态机

```
PUT 元数据              → status=DRAFT
上传 voice（URL方式成功） → voice.status=READY
上传 face（成功）        → face.status=READY
voice & face 均 READY   → status=READY
任一失败                → 对应 status=FAILED, 附 fail_reason
```

| 字段 | 取值 | 含义 |
|---|---|---|
| `status` | `DRAFT` | 元数据已建，但 voice/face 未全部 READY |
| | `READY` | voice + face 都 READY |
| | `FAILED` | voice 或 face 有失败 |
| `voice.status` / `face.status` | `NONE` | 未上传或已重置 |
| | `PROCESSING` | 已上传但运行时可用资源未完成 |
| | `READY` | 可用 |
| | `FAILED` | 失败 |

> **陷阱**：voice 走 **multipart 文件方式**只会保存样本、**不生成 `voice_id`**，状态停在 `PROCESSING`，运行时会静默回退默认音色。**要真音色必须走 URL 方式**（§5.5）。

---

## 5. 管理面 · persona 接口

以下示例统一使用：

```bash
BASE_URL='http://127.0.0.1:6006'
SECRET_KEY='replace-with-shared-secret'
TENANT_ID='1'
ELDER_ID='10086'
PERSONA_ID='p_1_10086_son'
```

### 5.1 创建 / 更新 persona（幂等 upsert）

```
PUT /api/v1/personas/{persona_id}
secretKey: <secretKey>
Content-Type: application/json
```

```json
{
  "elder_id": "10086",
  "tenant_id": "1",
  "relationship": "儿子",
  "display_name": "儿子小明",
  "address_to_elder": "爸",
  "self_reference": "小明",
  "gender": "male",
  "persona_prompt": "你是老人的儿子小明，说话亲切、短句、语速慢，多关心身体。",
  "is_default": true
}
```

**响应**：`{"code":0,"message":"ok","request_id":"req_…","data":{"persona_id":"p_1_10086_son","status":"DRAFT"}}`

- `display_name` 必填，漏传报 400 `Field required`。
- `elder_id` + `tenant_id` 首次创建后不可变，改则 400。
- 设 `is_default=true` 时，ai-bot 把同 elder 其它 persona 的 `is_default` 置 false。

### 5.2 查询单个 persona

```bash
curl "$BASE_URL/api/v1/personas/$PERSONA_ID" -H "secretKey: $SECRET_KEY"
```

`data` = §4.2 完整对象。用于小程序展示「角色是否就绪」。

### 5.3 列出老人全部 persona

```bash
curl "$BASE_URL/api/v1/personas?elder_id=$ELDER_ID&tenant_id=$TENANT_ID" -H "secretKey: $SECRET_KEY"
```

```json
{ "code": 0, "data": {
    "elder_id": "10086",
    "tenant_id": "1",
    "default_persona_id": "p_1_10086_son",
    "items": [
      { "persona_id": "p_1_10086_son", "display_name": "儿子小明", "is_default": true,  "status": "READY" },
      { "persona_id": "p_1_10086_dau", "display_name": "女儿小丽", "is_default": false, "status": "DRAFT" }
    ] } }
```

### 5.4 删除 persona

```bash
curl -X DELETE "$BASE_URL/api/v1/personas/$PERSONA_ID" -H "secretKey: $SECRET_KEY"
```

响应 `{"code":0,"data":{"persona_id":"…","deleted":true}}`。**幂等**：重复删除仍返回成功，便于后台重试。

### 5.5 上传音色素材 · URL 方式（推荐，唯一能产生真音色的方式）

```
POST /api/v1/personas/{persona_id}/voice
secretKey: <secretKey>
Content-Type: application/json
```

```json
{
  "audio_url": "https://files.example.com/voice_samples/xiaoming.wav",
  "ref_text": "爸，我是小明，今天感觉怎么样？",
  "source_duration_ms": 8000
}
```

**响应**：`{"code":0,"data":{"persona_id":"…","voice_status":"READY"}}`

约束：

- `audio_url` **必须能被 ai-bot 服务端直接访问**（公网可下载、不过期、免鉴权）。
- `ref_text` 须与录音内容尽量一致，否则克隆质量差。
- 服务端需配 `DASHSCOPE_API_KEY` 才能创建百炼 voice clone。
- 默认目标模型 `cosyvoice-v3-flash`，可用 `V1_PERSONA_VOICE_TARGET_MODEL` 调整。
- 覆盖上传：同一 persona 再传即替换旧音色。

### 5.6 上传音色素材 · multipart 方式（不产生 voice_id）

```bash
curl -X POST "$BASE_URL/api/v1/personas/$PERSONA_ID/voice" \
  -H "secretKey: $SECRET_KEY" \
  -F "file=@./xiaoming.wav" \
  -F "ref_text=爸，我是小明，今天感觉怎么样？" \
  -F "source_duration_ms=8000"
```

响应 `voice_status` 通常是 `PROCESSING`。**运行时无 `voice_id` 会回退默认音色** —— 生产链路不要用这条路。

### 5.7 上传形象素材

URL 方式：

```bash
curl -X POST "$BASE_URL/api/v1/personas/$PERSONA_ID/face" \
  -H "secretKey: $SECRET_KEY" -H "Content-Type: application/json" \
  -d '{"image_url":"https://files.example.com/faces/xiaoming.jpg"}'
```

multipart 方式：`-F "file=@./xiaoming.jpg"`。响应 `{"code":0,"data":{"persona_id":"…","face_status":"READY"}}`。

### 5.8 重置音色 / 形象

```bash
curl -X POST "$BASE_URL/api/v1/personas/$PERSONA_ID/voice:reset" -H "secretKey: $SECRET_KEY"
curl -X POST "$BASE_URL/api/v1/personas/$PERSONA_ID/face:reset"  -H "secretKey: $SECRET_KEY"
```

响应把对应 `voice_status` / `face_status` 置 `NONE`。删除默认角色后 ai-bot **不自动改选默认**，由后台重新 `PUT is_default`。

---

## 6. 管理面 · 文字转语音（TTS）

**用途**：后台已有一段文本（用药提醒、家属留言、健康报告播报），需要合成为音箱可播放的音频。

**边界**（务必分清，否则联调必踩）：

- 这是**一次性 HTTP 合成**，**不走 WebRTC**。
- **只返回音频**，不驱动数字人视频，也不会推到 WebRTC 远端 audio track。
- 实时聊天、机器人回复、数字人口型仍走 WebRTC 主链路。
- 返回音频格式固定：**WAV / 24000 Hz / 单声道 / 16-bit PCM**。

### 6.1 合成

```
POST /api/v1/tts/synthesize
secretKey: <secretKey>
Content-Type: application/json
```

| 字段 | 类型 | 必填 | 说明 |
|---|---|---|---|
| `text` | string | 是 | 待合成文本，服务端去首尾空白；**上限 5000 字符** |
| `persona_id` | string | 否 | 指定音色。该 persona 的 `voice.status=READY` 且有 `voice_id` 时优先使用 |
| `elder_id` | string | 否 | 不传 `persona_id` 时，用于查该老人的**默认 persona** |
| `tenant_id` | string | 否 | 与 `elder_id` 一起限定租户，防跨租户误用 persona |

**音色选择优先级**：

1. 请求带 `persona_id`，或经 `elder_id`+`tenant_id` 找到默认 persona，且该 persona `voice.status=READY` 且有 `voice_id` → **用 persona 音色**。
2. 否则 → 用运行配置里的 CosyVoice 默认 `voice` / `model_name` / `instruction`。
3. 若默认模型是 `cosyvoice-v3.5-flash` 且无 persona 音色，服务端自动回退 `cosyvoice-v3-flash`。

```bash
# 指定 persona
curl -X POST "$BASE_URL/api/v1/tts/synthesize" -H "secretKey: $SECRET_KEY" \
  -H "Content-Type: application/json" \
  -d '{"text":"爸，我是小明，今天感觉怎么样？","persona_id":"p_1_10086_son"}'

# 按老人默认 persona（我方后台走这条）
curl -X POST "$BASE_URL/api/v1/tts/synthesize" -H "secretKey: $SECRET_KEY" \
  -H "Content-Type: application/json" \
  -d '{"text":"妈，记得按时吃药。","elder_id":"10086","tenant_id":"1"}'

# 不用 persona，走默认音色
curl -X POST "$BASE_URL/api/v1/tts/synthesize" -H "secretKey: $SECRET_KEY" \
  -H "Content-Type: application/json" -d '{"text":"这是一条系统提示语。"}'
```

### 6.2 响应

```json
{
  "code": 0,
  "message": "ok",
  "request_id": "req_…",
  "data": {
    "audio_base64": "UklGRi…",
    "audio_format": "wav",
    "sample_rate": 24000,
    "channels": 1,
    "sample_width_bytes": 2,
    "duration_ms": 1800,
    "model_name": "cosyvoice-v3-flash",
    "voice": "cosyvoice-xxx",
    "voice_source": "persona",
    "persona_id": "p_1_10086_son"
  }
}
```

| 字段 | 说明 |
|---|---|
| `audio_base64` | WAV 文件字节的 base64，**须解码后播放** |
| `audio_format` / `sample_rate` / `channels` / `sample_width_bytes` | 固定 `wav` / `24000` / `1` / `2` |
| `duration_ms` | 按 PCM 字节数估算的时长 |
| `voice_source` | `persona`=命中角色音色；`default`=默认配置音色 |
| `persona_id` | 实际命中的 persona；未命中为 `null` |

> **无 `audio_url`、无 `resource_id`**。旧接口的「返回相对地址 → 二次带 `secretKey` GET 下载」两步流程已消失。

### 6.3 我方处理约定

1. 先看 `code=0`。
2. `data.audio_base64` base64 解码 → WAV 字节 → **转存我方文件库** → 把我方 URL 写入 `iot_speaker_call.audio_url` 推给设备。
3. **best-effort**：未启用 / 合成失败 / 解码失败一律降级为「纯文本推送」，设备端用系统 TTS 念文本，**不阻断推送流水落库**。
4. 文本超 5000 字**直接不合成**（不截断 —— 截断会让音箱念半句，而流水里 `message` 仍是全文）。
5. 响应体是内联 base64，体积不可信，**须流式限幅读取**（base64 按 4/3 膨胀折算上限），禁止一次性 `body()` 读进堆。

---

## 7. 数据面 · WebRTC 建连

### 7.1 建连步骤

1. 设备创建 `RTCPeerConnection`。
2. 添加本地麦克风 audio track。
3. 添加 `recvonly` 的 video transceiver（**没有摄像头也必须加**，否则 offer 无 video m-line，服务端无从回传数字人视频）。
4. 创建 data channel，**名称 `text`**。
5. `POST /webrtc/offer` 交 SDP offer，收 SDP answer，`setRemoteDescription`。
6. data channel 打开后立即发 `DeviceInfo`，等 `DeviceInfoAck`。
7. `persona_active=true` 后再开始第一轮正式语音输入。

### 7.2 Offer

```
POST /webrtc/offer
Content-Type: application/json
secretKey: <secretKey>   # 建议携带，当前未强校验
```

```json
{ "sdp": "v=0…", "type": "offer", "webrtc_id": "speaker_001_1783155600000_5757" }
```

- `webrtc_id`：本次连接 ID，建议 `{device_sn}_{ts}_{rand}`，**须唯一**。
- 成功响应：`{"sdp":"v=0…","type":"answer"}`（注意：**此接口不套 v1 统一封装**）。

### 7.3 ICE candidate

后续 ICE candidate 继续 POST 到**同一个** `/webrtc/offer`：

```json
{
  "type": "ice-candidate",
  "webrtc_id": "speaker_001_1783155600000_5757",
  "candidate": { "candidate": "candidate:…", "sdpMid": "0", "sdpMLineIndex": 0, "usernameFragment": "abcd" }
}
```

### 7.4 断线重连

生成**新的** `webrtc_id`，重建 PeerConnection，**重新发送 `DeviceInfo`**。

---

## 8. 数据面 · Data Channel 消息协议

所有 data channel 消息都是 JSON 字符串，统一 `{header:{name, request_id}, payload:{…}}` 结构。

### 8.1 `DeviceInfo`（设备 → 服务端）

**作用**：登记本次 WebRTC 会话的设备身份 + 选定 persona。
**时机**：data channel open 后、用户开始说话前。

```json
{
  "header": { "name": "DeviceInfo", "request_id": "req_device_001" },
  "payload": {
    "device_sn": "speaker_001",
    "tenant_id": "1",
    "elder_id": "10086",
    "persona_id": "p_1_10086_son"
  }
}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `device_sn` | 是 | 音箱序列号（`ro.serialno`） |
| `tenant_id` | 推荐 | 启用 persona 时建议传 |
| `elder_id` | 推荐 | 不传 `persona_id` 时用于找默认 persona |
| `persona_id` | 推荐 | 指定本次会话使用的 persona |

三种用法：带 `persona_id`（精确指定）/ 只带 `elder_id`+`tenant_id`（用默认 persona）/ 只带 `device_sn`（不启用 persona）。

### 8.2 `DeviceInfoAck`（服务端 → 设备）

```json
{ "header": { "name": "DeviceInfoAck", "request_id": "req_device_001" },
  "payload": { "ok": true, "persona_active": true, "persona_id": "p_1_10086_son" } }
```

- `persona_active=false` 表示本次会话未套用 persona，服务端使用默认配置。
- 失败时下发 `{"header":{"name":"Error"},"payload":{"code":"INVALID_DEVICE_INFO"|"PERSONA_NOT_FOUND","message":"…"}}`。
- **收到 `Error` 时修正字段后重发 `DeviceInfo` 即可，不需要重建 WebRTC。**

### 8.3 `Interrupt`（设备 → 服务端）

老人抢话或按结束键时下发，停止当前轮 LLM/TTS/Avatar 输出。

```json
{ "header": { "name": "Interrupt", "request_id": "req_interrupt_001" },
  "payload": { "reason": "user_speaking" } }
```

服务端主要识别 `header.name`；`payload.reason` 仅供端侧与日志诊断。

### 8.4 `SendHumanText`（设备 → 服务端，调试用）

绕过麦克风直接注入一段用户文本。**生产主链路仍走 audio track。**

```json
{ "header": { "name": "SendHumanText", "request_id": "req_text_001" },
  "payload": { "text": "今天天气怎么样？", "stream_key": "manual_text_001", "end_of_speech": true } }
```

### 8.5 `EchoAvatarText` + `client_action`（服务端 → 设备）

服务端要让设备执行**非 TTS 的端侧动作**时，把动作塞进 `payload.metadata.client_action` 下发。当前**音乐播放**（§8.5.1 / §8.5.2）与**会话结束**（§8.5.4）走这个机制。

> **关键**：`client_action` 是端侧动作，**不是**机器人语音。服务端不会把音乐音频混进 WebRTC audio track —— 设备必须用**本地播放器**拉 `url` 播放。

#### 8.5.1 `music.play`

```json
{
  "header": { "name": "EchoAvatarText", "request_id": "req_music_001" },
  "payload": {
    "stream_key": "stream_6493816_5",
    "mode": "increment",
    "text": "",
    "end_of_speech": false,
    "metadata": {
      "client_action": {
        "type": "music.play",
        "title": "稻香",
        "artist": "周杰伦",
        "url": "https://example.com/song.mp3",
        "source": "http://music-service.example.com",
        "query": "稻香",
        "candidates": [],
        "hints": ["暂停", "继续", "下一首", "音量小一点"]
      }
    }
  }
}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `type` | 是 | `music.play` / `music.control` / `session.end` |
| `url` | `music.play` 必填 | 端侧本地播放器要拉取播放的音频 URL |
| `title` / `artist` / `source` / `query` | 否 | 展示与日志诊断 |
| `candidates` / `hints` | 否 | 候选歌曲、可提示的控制口令，当前可忽略 |

#### 8.5.2 `music.control`

```json
{ "header": { "name": "EchoAvatarText" },
  "payload": { "text": "", "metadata": { "client_action": {
      "type": "music.control", "action": "pause", "delta": null,
      "hints": ["暂停", "继续", "下一首", "音量小一点"] } } } }
```

| `action` | 端侧行为 |
|---|---|
| `pause` / `resume` / `stop` | 暂停 / 恢复 / 停止并清空当前音乐 |
| `next` | 当前等同于停止；后续接候选列表再扩展 |
| `volume` | 按 `delta`（如 `0.15` / `-0.15`）调整本地播放器音量 |
| `mute` / `unmute` | 本地播放器静音 / 取消静音 |

#### 8.5.3 端侧处理约定

1. 收到消息**先解析 `payload.metadata.client_action`，再处理 `payload.text`**。
2. `music.play` 且 `url` 非空 → 停旧音乐，拉新 URL 播放。
3. **不能靠「远端 audio track 有没有声」判断音乐是否在放** —— 音乐不走 WebRTC。
4. `payload.text` 为空且 `client_action` 已消费 → **不展示空字幕、不当异常空回复**。
5. 记录 `title` / `artist` / `source` / `url` 域名 / 播放器错误码，便于排查。

#### 8.5.4 `session.end`（会话结束）

老人在对话中表达「想结束」时，**由服务端做语义判断**并下发本动作。设备收到即关闭本轮会话、停止一切音频、退回主屏等唤醒。

> **为什么这件事必须由服务端判断**
> 1. **语义需要上下文**。端侧关键词匹配必然误伤：「希望明天能**再见**面」「上次**再见**到他很高兴」都含「再见」，但都不是结束意图。这类判断只有 LLM 做得对。
> 2. **只有服务端知道告别语什么时候播完**。全双工下机器人语音是 WebRTC audio track，设备**无法**判断「这段话说完了没」——它收到事件就必须立刻关闭。时机只能由持有 TTS/音频流的服务端来掐。

```json
{
  "header": { "name": "EchoAvatarText", "request_id": "req_end_001" },
  "payload": {
    "text": "",
    "end_of_speech": true,
    "metadata": {
      "client_action": {
        "type": "session.end",
        "reason": "user_farewell"
      }
    }
  }
}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `type` | 是 | 固定 `session.end` |
| `reason` | 否 | `user_farewell`（说了告别语）/ `user_request`（明确要求退出）/ `inactivity`（服务端判定长时间无应答）/ `other`。**仅用于埋点与日志**，设备行为不因它而异 |

**关键时序（务必遵守）**

1. **必须在告别语的音频全部推完之后再发 `session.end`。** 设备一收到就释放 WebRTC，提前发会把「好的，那您早点休息，再见」从中间掐断。
2. 若本轮不打算说告别语，可直接发 `session.end`，设备静默收尾。
3. **不需要 Ack**，设备收到后不回执、不重试。

**触发场景（交给 LLM 语义判断，请勿做关键词匹配）**

- 直接告别：再见 / 拜拜 / 不聊了 / 不说了 / 就这样吧 / 先到这儿 / 结束对话
- 间接结束：没什么事了 / 我去睡了 / 我忙去了 / 你退下吧 / 别说了
- **反例（不应触发）**：「希望明天能再见面」「这歌别说了多好听」「先不说这个，我问你另一件事」——含结束词但不是结束意图

**设备端行为（已实现，无需你方关心细节）**

收到 `session.end` → 释放 WebRTC（机器人语音、音乐立即停）→ 关闭对话页、退回主屏 → 回到「在听『小伴』」等唤醒。

**验收标准**

老人说「再见」→ 机器人把告别语**完整说完** → 服务端日志出现
`client_action dispatch: type=session.end reason=user_farewell`
→ 设备在 1 秒内关闭会话、声音停止、对话页退回主屏。

---

## 9. 我方落地映射

| 职责 | 代码位置 | 备注 |
|---|---|---|
| 配置（base-url / device-secret-key / timeout / 下载上限） | `iot` `vendor/chatrobot/config/ChatRobotProperties` | 前缀 `lingxin.iot.vendor.chatrobot` |
| persona 管理面客户端 | `iot` `vendor/chatrobot/client/AiBotPersonaClient` | `secretKey` 头；失败上抛，Service 决定回滚 |
| TTS 合成客户端 | `iot` `vendor/chatrobot/client/ChatRobotTtsClient` | `synthesize(elderId, tenantId, personaId, text)`；best-effort 返回 `null` |
| 限幅 HTTP 工具 | `iot` `vendor/chatrobot/client/ChatRobotDownloads` | `getWithLimit` / `postJsonWithLimit`；**禁止再用 `bodyBytes()`／`HttpUtil.downloadBytes()`** |
| 合成 + 转存文件库 + 落推送流水 | `iot` `service/speaker/IotSpeakerServiceImpl#synthesizeAndStore` | 转存走 `FileRpcApi`（platform-server） |
| persona 落库 | `iot` `vendor/chatrobot/dataobject/AiPersonaDO`（表 `ai_persona`） | `persona_id` 由 `AiPersonaServiceImpl` 生成 |
| 音箱端 WebRTC 客户端 | speaker `ui/widget/AiBotRealtimeClient` | `/webrtc/offer` + data channel `text` + `DeviceInfo`/`Interrupt` |
| 音箱端本地音乐播放器 | speaker `voice/MusicPlayer`、`voice/VoiceInteractionController` | 消费 `client_action` |

**已知待清理**：speaker 端 `data/remote/chatrobot/ChatRobotClient` 仍保留 `POST /api/chat/text/stream`、`/api/chat/audio/stream` 两个 NDJSON 方法 —— 对端**不提供**这两个端点（§11），属遗留死路径，待随 WebRTC 链路稳定后移除。

**跨端字段口径**：`elder_id` / `tenant_id` 在 `AiBotPersonaClient.upsertPersona`、`ChatRobotTtsClient.synthesize`、音箱端 `DeviceInfo` 三处必须**同一口径（纯数字字符串）**，否则 ai-bot 侧默认 persona 查不到，静默退化成默认音色 —— 这种失败**不报错**，只表现为「声音不对」，排查成本高。

---

## 10. 部署要求与验收清单

### 10.1 ai-bot 侧环境变量

```bash
export DEVICE_SECRET_KEY='replace-with-shared-secret'   # 或 DEVICE_KEY / CHATROBOT_SECRET_KEY
export DASHSCOPE_API_KEY='…'                            # 百炼：voice clone + TTS 必需
# 可选
export V1_PERSONA_RUNTIME_ENABLED=1                     # =0 时 DeviceInfoAck 恒 persona_active=false
export V1_PERSONA_VOICE_TARGET_MODEL='cosyvoice-v3-flash'
./start_realtime_human.sh
```

### 10.2 后端 / 管理面验收

- [ ] `DEVICE_SECRET_KEY` 已设置，`GET /api/v1/health` 返回 UP。
- [ ] `PUT /api/v1/personas/{id}` 带 `secretKey` 成功；带 `X-Api-Key` 应 401（反向验证密钥已收敛）。
- [ ] `GET /api/v1/personas/{id}` 查得到 persona。
- [ ] face 上传后 `face.status=READY`。
- [ ] voice **URL 方式**上传后 `voice.status=READY` **且 `voice_id` 非空**。
- [ ] `POST /api/v1/tts/synthesize` 返回 `audio_base64`，解码后是合法 WAV（24k/mono/16bit）。
- [ ] 传 `elder_id`+`tenant_id` 时 `voice_source=persona`（证明默认 persona 被命中，而非静默走了默认音色）。

### 10.3 音箱端验收

- [ ] `/webrtc/offer` 能返回 answer；ICE candidate 能继续发送。
- [ ] data channel 能 open；`DeviceInfo` 能收到 `DeviceInfoAck`。
- [ ] 指定 persona 时 `persona_active=true`。
- [ ] 远端 audio track 可播放；远端 video track 可渲染（**offer 必须含 video m-line**）。
- [ ] `Interrupt` 能打断当前回复。
- [ ] 断线重连时生成新 `webrtc_id` 并重发 `DeviceInfo`。
- [ ] 收到 `music.play` 时用本地播放器拉 URL 播放；`text` 为空不展示空字幕。

---

## 11. 未实现 / 不采用的 v1.0 草案接口

基于双方已确认的 WebRTC 方案，以下 v1.0 草案接口**不作为音箱端主链路**：

| 草案接口 | 状态 | 替代方式 |
|---|---|---|
| `POST /api/v1/chat/audio:stream` | 未实现 / 不使用 | WebRTC audio track 上行 |
| `POST /api/v1/chat/text:stream` | 未实现 / 不使用 | 调试可用 data channel `SendHumanText` |
| `POST /api/v1/chat/interrupt` | 未实现 / 不使用 | data channel `Interrupt` |
| `WS /api/v1/avatar/stream` | 未实现 / 不使用 | WebRTC video track 下行 |
| `POST /api/v1/tts:synthesize` | 路径不采用 | 实现路径为 `POST /api/v1/tts/synthesize`（§6） |
| `X-Api-Key` / `X-Device-Key` 双密钥 | 不采用 | 统一 `secretKey`（§2.2） |
| NDJSON `pcm_b64` / `pts_ms` / `audio_end` / `seq` 事件族 | 不适用 | WebRTC 同轨天然音画同步，无需软对齐 |

> 若后续产品重新要求 HTTP NDJSON 音频流或独立 WebSocket 数字人帧流，需重新评估并补实现。

---

## 附录 A · 时序图

**A.1 建角色 + 上传克隆（后台 → ai-bot）**

```mermaid
sequenceDiagram
    participant MP as 小程序
    participant BE as 灵心后台
    participant BOT as ai-bot
    MP->>BE: 创建角色(关系/称谓) + 上传声音/照片
    BE->>BOT: PUT /api/v1/personas/{persona_id} (元数据)
    BOT-->>BE: {status: DRAFT}
    BE->>BOT: POST /personas/{id}/voice (audio_url + ref_text)
    BOT-->>BE: {voice_status: READY}
    BE->>BOT: POST /personas/{id}/face (image_url)
    BOT-->>BE: {face_status: READY}
    MP->>BE: 查看角色列表
    BE->>BOT: GET /api/v1/personas?elder_id&tenant_id
    BOT-->>BE: {items, default_persona_id}
```

**A.2 实时对话一轮（设备 ↔ ai-bot，WebRTC）**

```mermaid
sequenceDiagram
    participant SPK as 音箱(device_sn)
    participant BOT as ai-bot
    Note over SPK: 唤醒词 → 建 PeerConnection(mic上行 + video recvonly + dc"text")
    SPK->>BOT: POST /webrtc/offer {sdp, type:offer, webrtc_id}
    BOT-->>SPK: {sdp, type:answer}
    SPK->>BOT: POST /webrtc/offer {type:ice-candidate}
    Note over SPK,BOT: WebRTC 建立
    SPK->>BOT: dc: DeviceInfo {device_sn, tenant_id, elder_id, persona_id}
    BOT-->>SPK: dc: DeviceInfoAck {ok, persona_active, persona_id}
    Note over SPK: 用户说话 → audio track 上行
    BOT-->>SPK: 远端 audio track (机器人语音)
    BOT-->>SPK: 远端 video track (数字人)
    BOT-->>SPK: dc: EchoAvatarText (字幕 / client_action)
    Note over SPK: 老人抢话
    SPK->>BOT: dc: Interrupt {reason:"user_speaking"}
```

**A.3 主动播报（提醒 / 家属留言）**

```mermaid
sequenceDiagram
    participant JOB as 提醒Job/家属喊话
    participant IOT as iot-server
    participant BOT as ai-bot
    participant FS as 我方文件库
    participant SPK as 音箱
    JOB->>IOT: playToElder(elderId, text)
    IOT->>BOT: POST /api/v1/tts/synthesize {text, elder_id, tenant_id}
    BOT-->>IOT: {audio_base64, voice_source:"persona"}
    IOT->>FS: 转存 WAV
    FS-->>IOT: 我方可下载 URL
    IOT->>IOT: 落 iot_speaker_call (status=10 待发, audio_url)
    SPK->>IOT: 轮询 /iot/device/speaker-calls
    IOT-->>SPK: {audioUrl, text}
    Note over SPK: 有 audioUrl 则下载播放；否则系统 TTS 念 text
    SPK->>IOT: /speaker-call/ack (played)
```

---

## 附录 B · v1.0 草案 → v2.0 现状 差异

| v1.0 草案 | v2.0 现状 | 结论 |
|---|---|---|
| persona 一等资源、`/api/v1/personas` 全套 | 同 | ✅ **采纳** |
| 音色/形象支持 URL 拉取 | 同（且 URL 是唯一能产真音色的方式） | ✅ **采纳** |
| `secretKey` 拆 `X-Api-Key`（管理面）/ `X-Device-Key`（数据面） | 统一 `secretKey` | ❌ 未采纳 |
| `relationship` / `gender` 用枚举 | 自由字符串 | ❌ 未采纳 |
| `POST /api/v1/tts:synthesize`，`return=url` 返回 `audio_url` | `POST /api/v1/tts/synthesize`，内联 `audio_base64` | ⚠️ **路径与响应都变了** |
| TTS 下行 16000 Hz | **24000 Hz** | ⚠️ 采样率变了 |
| 聊天走 NDJSON 流（`pcm_b64`/`pts_ms`/`audio_end`/`seq`） | WebRTC audio track | ❌ 不实现 |
| 数字人走独立 `WS /api/v1/avatar/stream` 帧流 | WebRTC video track | ❌ 不实现 |
| 打断走 `POST /api/v1/chat/interrupt` | data channel `Interrupt` | ⚠️ 改信道 |
| `session_id` + `turn_id` 显式管理、幂等键 | WebRTC 连接即会话，`webrtc_id` 标识 | ⚠️ 模型变了 |
| （无）端侧动作下发 | `EchoAvatarText.metadata.client_action`（音乐播放/控制） | ➕ **新增** |
| §6「实时性与防卡顿 SLA」整章 | 不适用（WebRTC 自带拥塞控制与音画同步） | ❌ 作废 |

**为什么 SLA 整章删除**：那套指标（分片节奏、`pts_ms` 软对齐、背压丢帧、无帧看门狗）全部是为「音频走 NDJSON、视频走 WS 帧流」这套自研传输设计的补丁。改走 WebRTC 后，拥塞控制、抖动缓冲、丢包重传、音画同步都由协议栈原生解决，再维护那套指标是无的放矢。

---

## 附录 C · curl 快速自测

```bash
BASE_URL='http://127.0.0.1:6006'
SECRET_KEY='replace-with-shared-secret'
PERSONA_ID='p_1_10086_son'

# 0) 健康检查（无需鉴权）
curl "$BASE_URL/api/v1/health"

# 1) 建角色
curl -X PUT "$BASE_URL/api/v1/personas/$PERSONA_ID" \
  -H "secretKey: $SECRET_KEY" -H "Content-Type: application/json" \
  -d '{"elder_id":"10086","tenant_id":"1","relationship":"儿子",
       "display_name":"儿子小明","address_to_elder":"爸","self_reference":"小明",
       "persona_prompt":"你是老人的儿子，说话亲切语速慢。","is_default":true}'

# 2) 上传音色（URL 方式，唯一能产真 voice_id 的方式）
curl -X POST "$BASE_URL/api/v1/personas/$PERSONA_ID/voice" \
  -H "secretKey: $SECRET_KEY" -H "Content-Type: application/json" \
  -d '{"audio_url":"https://files.example.com/clone/xxx.wav",
       "ref_text":"爸，我是小明，今天感觉怎么样？","source_duration_ms":8000}'

# 3) 上传形象
curl -X POST "$BASE_URL/api/v1/personas/$PERSONA_ID/face" \
  -H "secretKey: $SECRET_KEY" -F "file=@face.jpg"

# 4) 查就绪（期望 status/voice.status/face.status 均 READY，且 voice.voice_id 非空）
curl "$BASE_URL/api/v1/personas/$PERSONA_ID" -H "secretKey: $SECRET_KEY"

# 5) TTS 合成 → 存成 wav 试听（校验 voice_source 是否 persona）
curl -s -X POST "$BASE_URL/api/v1/tts/synthesize" \
  -H "secretKey: $SECRET_KEY" -H "Content-Type: application/json" \
  -d '{"text":"该吃药了","elder_id":"10086","tenant_id":"1"}' > /tmp/tts.json
python3 -c 'import json; d=json.load(open("/tmp/tts.json"))["data"]; print(d["voice_source"], d["duration_ms"], d["model_name"])'
python3 -c 'import json,base64; open("/tmp/tts.wav","wb").write(base64.b64decode(json.load(open("/tmp/tts.json"))["data"]["audio_base64"]))'

# 6) 反向验证密钥已收敛：X-Api-Key 必须 401
curl -s -o /dev/null -w '%{http_code}\n' "$BASE_URL/api/v1/personas/$PERSONA_ID" -H "X-Api-Key: $SECRET_KEY"
```

---

## 附录 D · 排查手册

| 现象 | 根因 | 处理 |
|---|---|---|
| 401 `DEVICE_SECRET_KEY is not configured` | ai-bot 未配密钥环境变量 | 启动前 `export DEVICE_SECRET_KEY=…` |
| 401 `invalid api key` | 没传 `secretKey` / 值不一致 / 误传 `X-Api-Key` / 代理剥了自定义头 | 核对 header 名与值；检查反代是否透传自定义头 |
| PUT persona 报 `Field required` | `display_name` 必填漏传 | 补字段 |
| `DeviceInfo` 收到 `PERSONA_NOT_FOUND` | persona 未创建，或租户/老人不匹配 | 先 `GET /api/v1/personas/{id}` 确认存在 |
| `DeviceInfoAck` 里 `persona_active=false` | 没传 `persona_id` 且找不到默认 persona；或 `V1_PERSONA_RUNTIME_ENABLED=0` | 后台下发明确 `persona_id`；或确保有 `is_default=true` 的 persona |
| WebRTC 有声音无视频 | offer 里没有 video m-line | 设备即使无摄像头也要加 `recvonly` video transceiver |
| voice URL 上传失败 | `audio_url` ai-bot 侧不可访问 / 过期 / 需鉴权 / 格式不适合；或缺 `DASHSCOPE_API_KEY` | 在 ai-bot 机器上直接 `curl` 该 URL 验证；优先 WAV 16k/mono/16bit |
| voice 状态停在 `PROCESSING`，声音是默认音色 | 用了 multipart 方式，未生成 `voice_id` | 改走 URL 方式（§5.5） |
| TTS 502 | 缺 / 无效 `DASHSCOPE_API_KEY`；百炼超时或合成失败；缺 DashScope TTS SDK；persona `voice_id` 或模型不可用 | 看 `code` 是 `UPSTREAM_TIMEOUT` 还是 `UPSTREAM_ERROR`；查 ai-bot 日志 `failed to synthesize speech`；临时不传 `persona_id` 验证默认音色 |
| 声音「不是克隆的那个人」但接口全 200 | `elder_id`/`tenant_id` 字符串口径与注册时不一致 → 找不到默认 persona → 静默回退默认音色 | 检查响应 `voice_source` 是否为 `persona`（§9 跨端字段口径） |
| 音乐点了没响 | 端侧只监听 WebRTC 远端 audio track | 音乐走 `client_action.music.play`，必须本地播放器拉 `url`（§8.5） |

---

*文档结束 · v2.0 · 依据 ai-bot 现网实现定稿*
