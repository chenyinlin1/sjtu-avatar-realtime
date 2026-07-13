# 灵心智伴 · 数字人 AI-Bot 双向事件通道设计 v1

> **文档面向**：ai-bot（OpenAvatarChat / sjtu-avatar-realtime）、灵心智伴音箱端、后台、联调人员
> **状态**：v1 联调落地中——通道 + 收尾（§9 走 A）已实现；任务动作族（§10）待落地。本文只定协议与语义，不改任何既有消息的格式。
> **配套**：《ai-bot-avatar-realtime-接口规范-v2.0》（WebRTC 建连 / DeviceInfo / MusicStatus / client_action 基础）

---

## 0. 为什么要这个

现在「会话怎么结束、沉默多久算走神、提醒到点了要不要打断对话」这些**语义判断散在音箱端**，靠本地关键词表硬扛：

- 结束语靠 `END_PHRASES` 关键词匹配 —— 老人说「先退出吧」词表里没有就漏了，加了「退出」又漏「我去睡了」，越加越脆；
- 沉默兜底靠端上定时器 + 固定文案，服务端并不知道「这轮对话其实已经结束」；
- 语音聊天里老人说「明天早上八点提醒我吃药」，端上**根本没有能力**把它变成一条提醒任务。

这些本质都是**语义**，应该由持有 ASR + LLM 的 ai-bot 判断。音箱端只做两件事：**把发生的事上报**、**把服务端的指令执行**。

**已经验证过这条路**：音乐播放（`music.play` / `music.control` + 端侧 `MusicStatus` 回执）就是这个模式，跑通了。本设计把它推广成一套**对称的双向事件通道**。

---

## 1. 一张图

```
                        WebRTC data channel（label="text"，已存在）
   ┌─────────────┐  ── ClientEvent（端 → 云，本文新增） ────────►  ┌─────────────┐
   │   音箱端     │                                                  │   ai-bot    │
   │ (device)    │  ◄─ client_action（云 → 端，复用 EchoAvatarText）─ │ (server)    │
   └─────────────┘  ── *Status / *Ack（端 → 云，执行回执） ────────►  └─────────────┘
```

- **不新增连接、不新增端口**：全部走已经打开的 data channel（`InitializeAvatarSession` 之后那条）。
- **端 → 云** 统一信封 `ClientEvent`：音箱把「发生了什么」告诉服务端。
- **云 → 端** 统一信封 `client_action`（已存在，音乐在用）：服务端让音箱「做什么」。
- **执行回执**：端侧执行完一个有状态的动作后，用对应的 `*Status` 上报真实结果（如音乐的 `MusicStatus`），让服务端和端侧状态对齐。**无需 Ack 的动作不回执**。

---

## 2. 挂在现有协议上，不另起炉灶

对齐 ai-bot 侧 `ws_message_protocol.py` 的既有结构，本设计**只加两个 `MessageType`、扩一个已有字段**：

| 方向 | 消息 | 状态 | 说明 |
|---|---|---|---|
| 端→云 | **`ClientEvent`** | **新增** | 本文核心：端侧事件统一信封 |
| 云→端 | `EchoAvatarText.payload.metadata.client_action` | 已有，**扩 type** | 新增 `say` / `session.end` / `reminder.confirm` 等 type |
| 端→云 | `MusicStatus` | 已有 | 执行回执样板，其它有状态动作照抄 |
| 端→云 | `Interrupt` / `SendHumanText` / `DeviceInfo` | 已有，不动 | — |
| 云→端 | `ChatSignal` / `EchoAvatarText` / `DeviceInfoAck` / `Error` | 已有，不动 | — |

所有消息沿用统一头部：

```json
{ "header": { "name": "<MessageType>", "request_id": "<uuid>" }, "payload": { ... } }
```

---

## 3. 端 → 云：`ClientEvent`

音箱把「刚发生的事」上报给服务端。**只描述事实，不夹带指令**——要不要因此做点什么（播报、结束、建提醒），由 ai-bot 判断后用 `client_action` 回下来。

```json
{
  "header": { "name": "ClientEvent", "request_id": "evt-1783700000-abcd" },
  "payload": {
    "type": "wake",
    "ts": 1783700000123,
    "data": { }
  }
}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `type` | 是 | 事件类型，见 §3.1 |
| `ts` | 是 | 端侧事件发生时刻（epoch 毫秒），用于服务端排序 / 判超时 |
| `data` | 否 | 该事件类型的附加字段，见下表 |

### 3.1 事件类型表（`type`）

| `type` | 触发时机 | `data` 关键字段 | ai-bot 典型反应 |
|---|---|---|---|
| `wake` | 老人喊唤醒词、会话刚建立 | `persona_id`（冗余，DeviceInfo 已带） | 可选：下发一句主动招呼（`client_action: say`） |
| `user_silence` | 端侧检测到老人已静默 N 秒（分级：`level=1` 挽留 / `level=2` 准备退出） | `level` `silence_ms` | 判断是否真的该结束；要挽留就 `say`，要收尾就 `session.end` |
| `user_exit_hint` | ASR 文本疑似结束意图（端侧粗筛命中，交服务端定夺） | `text`（原始转写） | LLM 判真伪：是→`session.end`，不是→忽略 |
| `reminder_capture` | 语音里老人提到要提醒的事（端侧不解析，原样上报） | `text` | LLM 抽取时间/事项 → `reminder.create` 回下来（见 §5） |
| `music_state` | 本地音乐播放器状态变化 | 见 `MusicStatus`（已有，等价） | 对齐播放状态 |
| `play_error` | 端侧执行某个 `client_action` 失败 | `action` `error` | 记录 / 重试 / 换方案 |
| `ui_end` | 老人点了「结束对话」按钮 | — | 直接收尾（也可端侧自行结束，仅通知） |

> **约定**：`type` 用小写 + 下划线；未来加事件只往表里追加，端侧对不认识的 `type` 只发不管、服务端对不认识的 `type` 忽略即可（**双向前向兼容**）。

### 3.2 端侧粗筛 vs 服务端定夺（重要边界）

`user_exit_hint` / `reminder_capture` 这类，**端侧只做"疑似"粗筛**（比如结束语关键词命中、或"提醒/记得/别忘了"等触发词），把原始文本上报；**真正的判断在 ai-bot**。这样：

- 端侧关键词只用来**减少无谓上报**，判错了也只是多发一条事件，不会误结束对话；
- 语义准确性全靠 LLM，端侧词表不再是正确性的瓶颈。

（若 ai-bot 希望**全量文本**都上报、端侧完全不粗筛，把 `user_exit_hint` / `reminder_capture` 换成一个 `user_utterance` 事件即可，端侧改动极小。哪种由 ai-bot 定，见 §7 待确认项。）

---

## 4. 云 → 端：`client_action` 扩展

复用现有 `EchoAvatarText.payload.metadata.client_action`（音乐已在用）。新增以下 `type`：

### 4.1 `say` —— 让端侧用克隆音色说一句

```json
{
  "header": { "name": "EchoAvatarText", "request_id": "act-say-001" },
  "payload": { "text": "", "metadata": { "client_action": {
    "type": "say",
    "audio_url": "https://file.../hi.wav",
    "text": "您歇会儿，想聊天再喊我",
    "then": "keep"
  } } }
}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `audio_url` | 否 | 已合成的音频；空则端侧用系统 TTS 念 `text` |
| `text` | 否 | 文案（`audio_url` 空时兜底念它，也用于字幕） |
| `then` | 否 | 播完做什么：`keep`（默认，继续会话）/ `end`（收尾回主屏） |

> 这一条能统一掉端侧现在那堆本地提示语（wake/ready/idle/bye/farewell/fail/busy）——服务端想什么时候说、说什么、说完退不退，全由它定。**端侧本地提示语作为断网兜底保留，但联网时以服务端 `say` 为准。**

### 4.2 `session.end` —— 结束会话

（已在《接口规范 v2.0》§8.5.4 定义，此处归并统一）

```json
{ "type": "session.end", "reason": "user_farewell" }
```

端侧收到即释放 WebRTC、停音乐、退回主屏。**若要先说告别语再退，用 `say` 带 `then=end`，或先发一条 `say` 再发 `session.end`——务必在告别语音频推完后再发 `session.end`**（端侧一收到就立刻关，早发会截断）。

### 4.3 `reminder.create` —— 语音里生成提醒任务

> 🆕〔2026-07-12〕本节现为「任务动作族」的**第一个具体范例**；通用框架（建/改/删 + 结合工单 + 工具注册表 + 确认/幂等/ack 规范）见 **§10**。

老人在聊天中说「明天早上八点提醒我吃降压药」，ai-bot 的 LLM 抽取时间/事项后下发：

```json
{
  "header": { "name": "EchoAvatarText", "request_id": "act-rem-001" },
  "payload": { "text": "好的，明天早上八点我提醒您吃降压药", "metadata": { "client_action": {
    "type": "reminder.create",
    "kind": "custom",
    "title": "吃降压药",
    "remind_at": 1783759200000,
    "repeat": "none",
    "speak_text": "该吃降压药啦"
  } } }
}
```

| 字段 | 必填 | 说明 |
|---|---|---|
| `kind` | 否 | `custom`（默认，一次性/关怀提醒）；<b>不建议由语音改用药方案</b>，见下 |
| `title` | 是 | 提醒事项 |
| `remind_at` | 是 | 提醒时刻（epoch 毫秒） |
| `repeat` | 否 | `none` / `daily` / `weekly`（预留） |
| `speak_text` | 否 | 到点播报文案，空则用 `title` |

#### 落到哪张表 —— 关键区分（读过后台代码后定）

后台**已有两套提醒，语义不同，别混**：

1. **用药提醒**（`health_medication` → 按天展开成 `health_medication_reminder`）：从<b>用药方案</b>（药名/剂量/`remindTimes`/周期）每天自动生成的结构化日程，可打卡、逾期产工单。**这套不该被语音随口改**——老人说一句「提醒我吃药」既不是在维护用药方案，也不该塞进周期展开逻辑。
2. **关怀 / 一次性提醒**：非结构化的「到点说一句话」。语音生成的提醒应落在**这一类**（`kind=custom`）。

> 结论：`reminder.create` 默认落成**一次性/关怀提醒**，与家属在小程序建的提醒、以及用药提醒**并存但分类**。真要「以后每天这个点提醒吃这个药」，那是在建<b>用药方案</b>，应引导家属去小程序做，而不是靠一句语音——语音端只做 `custom`。

#### 落地路径（端侧转后端，鉴权同源）

端侧收到后**不自己管理提醒**（音箱会离线/断电，本地定时不可靠），而是<b>转调后端 app-api 落库</b>，到点由现有的 `ReminderAnnounceJob` 统一播报（跟家属建的提醒同一套出口）。

**新增端点**：`POST /app-api/iot/device/reminder/create`，<b>照现有 `/iot/device/reminder/ack` 那个 controller 的模子</b>（`AppIotDeviceReminderAckController`，已在生产跑）：

- `@PermitAll` + **产品密钥验签**（`DeviceSignatureVerifier`，与握手/打卡同源）；
- **elderId 由服务端按设备 SN 解析绑定关系得出，设备不上报**——杜绝「设备伪造给别的老人建提醒」；
- 复用现有关怀/一次性提醒的 create 逻辑，发起人记为 `elder`（老人主动）/来源标 `voice`。

**回执**：端侧落库成功后回一条 `ClientEvent{type:"reminder_ack", data:{ok:true, reminder_id, remind_at}}`；失败回 `{ok:false, error}`，ai-bot 据此决定是否让机器人改口（「没记上，您再说一遍时间？」）。

#### 时间归一化（谁来做）

「明天早上八点」→ epoch 毫秒的解析<b>由 ai-bot 的 LLM 完成</b>（它有上下文和当前时间），端侧只收绝对时间戳、不解析自然语言。ai-bot 需注意用<b>设备所在时区</b>（随 DeviceInfo 上报，或后端按老人档案带）——老人在国内，`remind_at` 必须按东八区算，别用服务器 UTC。

> ⚠️ 这条唯一需要三方（ai-bot 抽取 + 端转发 + 后台落库/播报）联动。工作量集中在后端加一个 controller（照 ack 那个抄，约半天）。建议二期；一期先把 `say` / `session.end` / `ClientEvent` 打通验证通道。

---

## 5. 时序示例

### 5.1 沉默 → 服务端判定结束

```
端：user_silence {level:1, silence_ms:60000}           ── ClientEvent →
                                              ← client_action  云：say {text:"还在吗？想聊什么都行", then:keep}
（老人仍无应答，端侧再报）
端：user_silence {level:2, silence_ms:90000}           ── ClientEvent →
                                              ← client_action  云：say {audio_url:"bye.wav", then:end}
端：播完告别语 → 释放 WebRTC → 回主屏
```

### 5.2 语音建提醒（二期）

```
端：reminder_capture {text:"明天八点提醒我吃药"}        ── ClientEvent →
                                    （LLM 抽取时间/事项）
                                              ← client_action  云：reminder.create {title:"吃药", remind_at:...}
端：转后端 /app-api 落库 → 回 reminder_ack {ok:true}   ── ClientEvent →
                                              ← EchoAvatarText 云：（同时机器人说"好的，明天八点提醒您"）
```

---

## 5.5 连接时把老人档案带给 ai-bot（个性化对话）

**目的**：让 LLM「认识」这位老人——叫对称呼、知道大概年龄/籍贯、聊得到点子上。<b>不是</b>给 ai-bot 展示或落库。

### 为什么走「端侧随 DeviceInfo 送」，不走「ai-bot 回查」

- ai-bot 侧 `client_handler_rtc.py` 现在**根本没解析 elder_id / persona_id**，也没有访问我方后端的能力与凭据。让他们回查等于要开库、发 token、加鉴权，重且危险。
- 端侧握手时本来就要拿会话凭证，顺手让后端多下发几个档案字段、端侧塞进 `DeviceInfo` 一起送过去，ai-bot **无脑塞进 system prompt** 即可，零回查。

### 传哪些字段（**白名单，严格控制**）

老人档案里绝大多数是敏感 PII（身份证、医保号、住址、收入、残疾等级……），<b>一律不传</b>——喂进第三方 LLM 是隐私红线。只传对个性化陪伴真正有用的。**用户拍板：慢病 / 过敏原 / 进行中工单也带**（都是老人画像依据，过敏原尤其用于避免 AI 推荐致敏食物）；实时体征按「**乙案：仅连接快照**」——建连时把最新体征随画像带一份，不做会话内推送、也不给 ai-bot 开回查接口（预警另有工单链路兜底）。

```json
{
  "header": { "name": "DeviceInfo", "request_id": "req_di_xxx" },
  "payload": {
    "device_sn": "...", "tenant_id": "...", "elder_id": "...", "persona_id": "...",
    "elder_profile": {
      "nickname": "王爷爷",
      "gender": "male",
      "age": 78,
      "native_place": "四川成都",
      "chronic": ["高血压", "2型糖尿病"],
      "allergy": ["青霉素", "花生"],
      "work_orders": [
        { "title": "血压异常（等级3）", "type": "ALERT", "phase": 10, "phase_name": "待受理" }
      ],
      "vitals": [
        { "code": "bp", "name": "血压", "value": "135/85", "unit": "mmHg", "level": 1, "at": 1720684800000 },
        { "code": "heart_rate", "name": "心率", "value": "72", "unit": "bpm", "level": 0, "at": 1720684800000 }
      ],
      "sleep": { "date": "2026-07-10", "duration_min": 410, "deep_pct": 22, "wake_ups": 2 }
    }
  }
}
```

| 字段 | 来源（后端 RPC） | 给 LLM 的用处 |
|---|---|---|
| `nickname` | `ElderInfoApi.getElderProfile` → `nickname`（空则退回 `name`） | 怎么称呼老人 —— 最有用的一个 |
| `gender` | 同上 `gender` → `male`/`female`（未知不下发） | 用词、话题 |
| `age` | 同上 `age`（`birthDate` 实时算） | 话题深浅、年代共鸣 |
| `native_place` | 同上 `nativePlace` | 家乡话题、方言亲切感 |
| `chronic[]` | `ElderHealthProfileApi` → 慢病表 `diseaseName`（仅在管/未治愈、去重） | 关怀话题、忌口提醒 |
| `allergy[]` | 同上 → 过敏原表 `allergenName`（去重） | **避免推荐致敏食物 / 药物** |
| `work_orders[]` | `WorkOrderApi.listActiveByElder`（全类型，phase<50，≤20 条） | 知道老人当前有哪些待办护理（上门 / 回访 / 提醒 / 维修…） |
| `vitals[]` | `ElderHealthProfileApi` → 最近一次体征（**有数据才带**；血压合并一条） | 老人问「我血压 / 心率」时能答；关怀切入 |
| `sleep` | iot 本地 `IotSleepReportService.getLastNight` | 「昨晚睡得好不好」话题 |

<b>不传的（红线）</b>：`idCardNo` / `phone` / `*Address` / `medicalInsuranceNo` / `monthlyIncome` / `disability*` —— 全部禁传（基础画像走 `getElderProfile`，DTO 本就不含这些）。

### 落地改动（已实现 2026-07-11）

1. **后端 `-api`（跨 3 个模块）**：
   - `ElderInfoApi.getElderProfile(elderId)` → 基础画像（`resident-server`，服务端 `executeIgnore`）。
   - `ElderHealthProfileApi.getHealthProfile(elderId)` → 慢病 / 过敏原 / 最近体征（**新建**，`resident-server`，`executeIgnore`）。
   - `WorkOrderApi.listActiveByElder({elderId, tenantId})` → 进行中工单（`order-server`，带租户 `execute`，须显式传 tenantId）。
2. **后端握手**：`AppIotDeviceHandshakeRespVO` 加 `elderProfile` 块；`IotDeviceHandshakeServiceImpl.loadElderProfile(tenantId, elderId)` 调上述 3 个 RPC + iot 本地睡眠组装，四源各自 try/catch 降级，**绝不阻断握手**。
3. **端侧**：`BackendModels.HandshakeResponse` 以原始 `JsonObject` 收下 `elderProfile`（不建结构、camelCase 原样）；`SessionManager` 内存存其字符串（随每次唤醒前重握手刷新）；`AiBotRealtimeClient.DeviceInfo` 加 `elderProfileJson`，`sendDeviceInfo()` 里**递归把键 camelCase→snake_case** 后整棵挂到 `payload.elder_profile`。
4. **ai-bot（待接）**：解析 `elder_profile`，把 nickname/gender/age/native_place + chronic/allergy/work_orders/vitals/sleep 拼进该会话 LLM 的 system prompt。

> **命名约定**：后端 RespVO 出 camelCase（`nativePlace` / `workOrders` / `phaseName` / `durationMin`…），端侧 `sendDeviceInfo` 统一转 snake_case 再发 ai-bot（与 `device_sn` 等既有字段一致）。ai-bot 按 snake_case 解析。

> **送达可靠性（端侧已做 ack 重发，ai-bot 需配合）**：数据通道刚 OPEN 端侧即发 DeviceInfo，可能「发太早」——ai-bot 若此刻还没挂上该通道的 message 监听（handler 里 await 加载模型/persona）就会丢；瞬时发送失败同理。端侧已改为 **ack 驱动重发**：未收到 `DeviceInfoAck` 则每 700ms 重发、最多 4 次，收到即停。**ai-bot 两条要求**：① 该数据通道的 message handler <b>尽早注册</b>（勿在注册前 await）；② DeviceInfo 需<b>幂等</b>处理并<b>每次都回 `DeviceInfoAck`</b>（重发可能多次到达，重复应用同一画像/persona 应无副作用）。

> **合规提醒**：慢病 / 过敏原 / 体征属健康信息，传第三方 LLM 须在隐私政策覆盖「为提供 AI 陪伴，会将您的健康概况用于对话」。已按产品决策纳入；如需收紧，去掉握手里 `ElderHealthProfileApi` 一处调用即可（其余部分不受影响）。

---

## 6. 端侧落地映射（音箱现有代码）

| 职责 | 端侧位置 |
|---|---|
| 发 `ClientEvent` | `AiBotRealtimeClient.sendClientEvent(type, data)`（照 `sendMusicStatus` 加一个） |
| 收 `client_action` 分发 | `AiBotRealtimeClient` 的 `EchoAvatarText` 分支已在解析 `client_action`，`switch(type)` 加分支 |
| `say` 执行 | 交 `PromptPlayer`（已有：掐上行 + 克隆音色 + 播完回调） |
| `session.end` 执行 | `VoiceInteractionController.closeSession(true)`（已有） |
| `reminder.create` 执行 | 转 `BackendApi` 新端点（二期） |
| 事件来源 | 唤醒/沉默/结束语粗筛/按钮，都在 `VoiceInteractionController`，改为**发事件**而非本地决策 |

---

## 7. 待 ai-bot 确认

1. **端侧要不要粗筛**：`user_exit_hint`/`reminder_capture`（端侧关键词粗筛）还是 `user_utterance`（全量文本上报、服务端全判）？后者更纯粹，但会增加 data channel 文本量。
2. **`ClientEvent` 用新 `MessageType` 还是复用 `SendHumanText` 加 `mode`**？倾向新增 `ClientEvent`（语义清晰，不和"用户说的话"混），但若你们不想动枚举，也可挂在 `SendHumanText.payload.metadata`。
3. **`say` 的 `then=end` 时序** 【已定 2026-07-12：收尾走 A（数字人 WebRTC 说完 + 音频 `stream_end` 后发裸 `session.end`），详见 §9.2】：由 ai-bot 保证「音频推完再判 end」，还是端侧播完 `say` 后自己接 `session.end`？倾向前者（服务端只发一条），但要你们确认能拿到"音频已推完"的时机。
4. **提醒落库**（§4.3）走端侧转后端，还是 ai-bot 直接调后端 app-api？倾向端侧转（鉴权链现成、发起人天然是 elder），你们只管抽取 + 下发 `reminder.create`。

一期建议范围：`ClientEvent` 信封 + `wake`/`user_silence`/`user_exit_hint`/`ui_end` 四个事件 + `say`/`session.end` 两个 action。提醒（`reminder_capture`/`reminder.create`）二期。

---

## 8. 端侧落地状态（phase-1a，2026-07-11 已实现，供 ai-bot 对线）

音箱端已按本文 §7「倾向」实现，ai-bot 请按此对接联调：

**端→云（`ClientEvent`，新 `MessageType`，走已开的 data channel `text`）**：信封 `{header:{name:"ClientEvent",request_id:"evt-..."}, payload:{type, ts, data}}`。当前发出的事件：
- `wake`：会话就绪即发一次，`data:{persona_id?}`。ai-bot 可据此下发一句 `say` 主动招呼。
- `user_exit_hint`：端侧关键词**粗筛**命中「疑似结束」时发，`data:{text:"<ASR原文>"}`。**判真伪在 ai-bot**。
- `ui_end`：老人点了「结束对话」按钮（端侧同时自行收尾）。
- （`user_silence` 本次未接，二期；端侧暂仍用本地两级沉默提示。）

**云→端（`client_action`，挂在 `EchoAvatarText.payload.metadata.client_action`）**：端侧已能执行：
- `say`：`{type:"say", audio_url?, text?, then?}`。`audio_url` 有就播（远端流式），空则系统 TTS 念 `text`；`then="end"` 播完收尾回主屏，否则继续。播放期间自动掐上行、压低音乐。
- `session.end`：`{type:"session.end", reason?}`。端侧立即停音乐、释放、回主屏。

**两个关键约定（务必对齐，否则现象怪）**：
1. **要结束必须显式发 `session.end`（或 `say` 带 `then=end`）**。端侧收到 `user_exit_hint` 后**不再本地强退**，改等 ai-bot 定夺；ai-bot 只是聊一句「再见」而不发 `session.end`，会话不会结束。**安全兜底**：端侧发出 `user_exit_hint` 后，若 ai-bot **全程无任何回应**（无 `EchoAvatarText`/`say`/`session.end`）约 4s，端侧才本地告别收尾——防 ai-bot 未接该事件时老人退不出。ai-bot 只要对该 utterance 有任意回应，兜底即取消。
2. **`say` 的 `then=end` 时序**：端侧以「音频播完」为准再收尾；若 ai-bot 走「先 `say` 再单独 `session.end`」，**务必等告别语音频推完再发 `session.end`**，否则会截断告别语（同 §4.2）。

> DeviceInfo 送达已加 **ack 重发**（见《接口规范 v2.0》§8.5.3.1 命名约定后一条）：ai-bot 需对该数据通道**尽早挂 message 监听**并对 DeviceInfo **幂等 + 每次回 `DeviceInfoAck`**。

---

## 9. 联调纪要与收尾定稿（2026-07-12 首次真联调）

三方数据通道已打通并验证：`DeviceInfo → DeviceInfoAck`、`wake` / `user_exit_hint`（ai-bot 有 `Server received` 回显）、`say{then:end}` 下发均 OK。以下为联调暴露的两个问题与拍板结论。

### 9.1 握手顺序：DeviceInfo 必须先于 ClientEvent（已对齐）

ai-bot 现校验「未登记 DeviceInfo 就来 ClientEvent」→ 回 `{header:{name:"Error"}, payload:{code:"DEVICE_INFO_REQUIRED"}}`。实测 ai-bot 上电后该 DC handler ~2s 才就绪、`DeviceInfoAck` ~2.3s 才回；端侧原在「ICE 通 + 1s」就发 `wake`，抢在 ai-bot 摄入 DeviceInfo 之前到达 → 必被打回。

- **端侧已改**：`wake` 严格**等 `DeviceInfoAck` 之后**才发（Ack ≡ ai-bot 已登记 DeviceInfo）；就绪提示语「您说」仍走本地 1s 兜底、不受影响。DeviceInfo 重发间隔放宽 700→1500ms。
- **对 ai-bot 的要求**：对该通道**尽早挂监听**、DeviceInfo **幂等**、**每次回 `DeviceInfoAck`**（端侧以 Ack 为准放行 `wake`）。

### 9.2 收尾方案：拍板走 A（数字人 WebRTC 说完 + 裸 `session.end`）

实测 ai-bot 对 `user_exit_hint` 会**双发**：①数字人先用 WebRTC 音频说一句告别（`EchoAvatarText` token 串，如「拜拜爷爷，早点休息，明儿见~」）＋②紧跟一条 `say{text, then:end}`（另一句、且只有 text 无 `audio_url`）。端一收到 `say{then:end}` 立即关会话 → 把①的 WebRTC 告别音频**掐断**，②又没 `audio_url`、盒子无中文 TTS → **哑退**（“结束没声音”）。

**结论（本次拍板：走 A）：**

- **A｜数字人 WebRTC 说完 + 裸 `session.end`**：让数字人用 WebRTC 把告别说完 → **等这段 `client_playback` 的 `ChatSignal(type=stream_end)` 之后** → 再发**裸 `session.end`**。告别声来自数字人自己（音色最对），端侧只 `closeSession`。
- **收尾不要再对同一次结束额外发 `say{then:end}`**（会与①打架、且被端侧的 close 掐断）。
- **唯一硬约束**：`session.end` **务必在告别音频 `stream_end` 之后**再发——端侧一收到就立刻关，早发即截断（同 §4.2 / §7-3）。

**端侧防御兜底（已落，走 A 后正常不触发）**：`onSay` 收到 `then=end` 且**无 `audio_url`** 时，不再指望盒子 TTS，改播本地固定告别（克隆音色 WAV，`beginFarewell`：先 `sendInterrupt` 掐掉 ai-bot 那句避免重叠 → 播 farewell → close）。仅当 ai-bot 万一仍发无音频的 `say{then:end}` 时兜底。

另有一道**收尾时序兜底**（已落）：`onSessionEnd` 收到 `session.end` 时，若数字人告别音频仍在播（`avatarPlaying`，由 `ChatSignal` 的 stream_begin/stream_end 维护），**等那条 `stream_end` 再 `closeSession`**（≤8s 超时强制收尾），防 ai-bot 万一把 `session.end` 早发、告别被掐半截；走 A 的正常路径（end 在 stream_end 之后）照旧立即关。

---

## 10. 🆕 语音任务动作族：通用框架（建/改/删 · 结合工单）〔新增 2026-07-12〕

> 本节把 §4.3 的单条 `reminder.create` **提成通用框架**：一套统一的 `client_action` 约定，覆盖**建（create）/改（update）/删（cancel）**，并说明如何落到**现有工单枢纽**。工单侧完整闭环见《架构-老人待办闭环与预警接入.md》《架构-提醒统一走工单.md》，本节只定义「语音 action → 业务行 → 后端投影工单」这一段。

### 10.1 范式：LLM 工具调用 + 受控 fulfillment

业界标准（OpenAI function calling / Anthropic tool\_use / MCP，及经典 intent→entity→fulfillment）：**对话模型只决定「调哪个动作、填什么参数」，不碰写权限**；真正的写操作交给受控执行层。三层分工（沿用 §3.2 / §4.3）：

| 层 | 职责 |
|---|---|
| ai-bot（LLM） | 抽意图 → 出**结构化 action**（`client_action`），NL 解析 / 时间归一都在这 |
| 端 | **瘦执行器 + 鉴权持有者**：转后端 app-api（设备 HMAC、elderId 服务端按 SN 解析）→ 回 ack。**不解析自然语言** |
| 后端 | **真值源**：写**业务行**（关怀提醒 / 用药打卡…）+ `resolveBySource` 投影工单，回结果 |

新任务 = 注册一个 action type（见 10.6），端加一条 dispatch 分支，后端加一个 handler。

### 10.2 动作信封与通用字段

挂在已有的 `EchoAvatarText.metadata.client_action`（同 §4.1/§4.3）。**所有 action 共用**：

| 字段 | 必填 | 说明 |
|---|---|---|
| `type` | 是 | 动作名，`<domain>.<verb>`，如 `reminder.create` / `reminder.update` / `reminder.cancel` |
| `action_id` | 是 | **幂等键**（Stripe 式）；重发不重复执行；ack 原样回带 |
| `args` | 是 | 动作私有参数（见 10.6） |
| `confirm` | 否 | `true` = 破坏性/高危，需老人**口头确认后再执行**（见 10.5） |

**端回执**（把 §4.3 的 `reminder_ack` 泛化为统一 `action_ack`）：
```json
{ "header": {"name":"ClientEvent"}, "payload": { "type":"action_ack",
  "data": { "action_id":"...", "ok":true, "entity_id":123, "error":null } } }
```
`ok:false` 时 ai-bot 据 `error` 让机器人改口（「没记上，您再说一遍时间？」）。

### 10.3 建 / 改 / 删 生命周期（CRUD）

- **create** → 后端建业务行、返回稳定 `entity_id`（如 `reminder_id`）；ack 带回，**ai-bot 记住它**以便后续引用。
- **update / cancel(=删)** → `args` 带 `entity_id` 指向先前任务 + 变更字段。
- **后续交互如何定位「哪一个」**：老人说「把八点那个提醒改成九点」「那个提醒别了」——LLM 要先知道老人**当前有哪些任务**。复用已有 **DeviceInfo.elder\_profile 快照**（它已带 `work_orders`；再加一份**当前生效的语音/关怀提醒清单** `reminders:[{id,title,remind_at}]`），让 LLM 把「八点那个」解析成具体 `entity_id`。变更成功后端回一条快照刷新事件（或下次唤醒重握手带新的）。
- **改 / 删是破坏性操作 → `confirm:true`**（见 10.5）。
- 时间/实体归一在 LLM 侧（同 §4.3），端只收 `entity_id` + 绝对值、不解析 NL。

### 10.4 结合工单（铁律：老人不碰工单、碰业务行）

- 语音 action 操作的是**业务行**（关怀提醒 `care` 行 / 用药打卡 `health_medication_reminder` …），**不是工单**。
- 工单是这些业务行的**投影 / 闭环载体**：后端建/改/结业务行后，由**系统 `resolveBySource`（source\_type + source\_id）** 把对应工单投影同步关/更新——幂等；合并单其余来源还在则不误关整单。
- **安全红线（照抄现有，勿破）**：危急预警（体征危急 `ALERT`）老人主观确认**绝不 `resolveBySource` / 关工单**，只写 ack。工单闭环**只走**家属 `familyTransit` / 运营 `transit` / 系统 `resolveBySource` 三条，**老人不是关单主体**。故语音里「我没事、别提醒了」对危急预警**只降打扰、不结单**（呼应待办闭环文档 §5.3；血压危急主观「没事」就关单会出人命）。

**动作 → 业务行 → 工单投影 映射**：

| 语音动作 | 业务行 | 工单投影 |
|---|---|---|
| `reminder.create/update/cancel` | 关怀/一次性提醒 `care` 行（`kind=custom`） | REMIND 工单；cancel/done 时 `resolveBySource` 关 |
| `medication.checkin`（已服/跳过） | `health_medication_reminder` ack（**已跑通**，复用 `/iot/device/reminder/ack`） | `resolveBySource(MEDICATION_OVERDUE, reminder_id)` 关逾期工单 |
| `help.request` / SOS | —（老人**可**主动求助） | 直接产 `ALERT` 工单（已有端点） |
| 预警「知道了」 | 只写 ack | **不关单**（红线） |

### 10.5 规范约束（固化）

1. **破坏性/高危先确认再执行**：`confirm:true` 的动作走 propose→老人确认→execute→ack；或乐观执行 + 语音回读 + 撤销窗口。**别让一句话改「用药方案」**（结构化/医疗，引导家属去小程序，同 §4.3）。
2. **幂等**：`action_id` 幂等键；后端按 `(elder, action_id)` 去重；update/cancel 对不存在/已删 entity 幂等返回 `ok:true`。
3. **鉴权端转发**：设备 HMAC 验签、elderId 服务端按 SN 解析、发起人=`elder`、来源标 `source=voice`；**不给 ai-bot 后端写凭据**。
4. **失败优雅降级**：`ack{ok:false,error}` → LLM 改口让老人再说；别静默吞。
5. **可审计**：每条 voice action 存 `source=voice` + `action_id` + 原始 ASR 文本 + `entity_id`。
6. **时区**：`remind_at` 等绝对时间按**设备时区**（随 DeviceInfo），别用服务器 UTC（同 §4.3）。

### 10.6 动作注册表（可扩展 · 分期）

| `type` | 语义 | 关键 `args` | 期次 |
|---|---|---|---|
| `reminder.create` | 建一次性/关怀提醒 | `title, remind_at, repeat?, speak_text?` | **一期** |
| `reminder.update` | 改提醒（时间/事项） | `entity_id, remind_at?, title?` | 一期 |
| `reminder.cancel` | 删提醒 | `entity_id` | 一期 |
| `medication.checkin` | 用药打卡（已服/跳过） | `reminder_id, status` | 一期（复用现成 ack） |
| `help.request` | 求助 / SOS | `note?` | 归并（已有端点） |
| `service.order` … | 服务下单等 | … | 二期 |

新增任务 = 注册一行 + 端 dispatch 加分支 + 后端 handler。

### 10.7 端 / 后端落地增量

- **端**：`AiBotRealtimeClient.dispatchClientAction` 已有 switch（`music.play` / `say` / `session.end`），加 `<domain>.<verb>` 分支 → 统一 handler（转后端 + 回 `action_ack`）。端保持 dumb、不解析 NL。
- **后端**：统一 `POST /app-api/iot/device/action`（按 `type` 分发）**或**照 `AppIotDeviceReminderAckController` 模子加 controller 家族；写业务行 service + `resolveBySource`，复用现成设备验签 / 租户 ignore。
- **ai-bot**：工具 schema 声明进 system prompt + 确认话术；时间/实体抽取归一。
- **一期**：先把 `reminder.create` 闭环打通（LLM→端→后端建 care 行→投影工单→`action_ack`→LLM 回读），再复制到 update/cancel 及其它任务。

---

## 修订记录

- **2026-07-12**：新增 §9（联调纪要与收尾定稿，走 A）、§10（🆕 语音任务动作族通用框架：建/改/删 + 结合工单）；§7-3 标记「已定：走 A」。§9/§10 为本轮首次真联调后补充；涉及端侧改动（`wake` 门控 `DeviceInfoAck`、收尾 `stream_end` 时序兜底、`onSay` 无音频退本地告别）均已落码。
