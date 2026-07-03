# Tool Call 模式说明

这份文档说明本项目的工具调用方式。它写给第一次接触 tool call 的工程师：只要照着这里的结构做，就可以把“让大模型决定是否调用工具”这件事迁移到别的项目里。

## 先说结论

本项目有两种相关但不同的机制：

1. **标准 Agent Tool Call 机制**
   代码位置主要在 `src/handlers/agent/` 和 `src/handlers/agent/tools/`。这是更标准、更可扩展的方式，接近 OpenAI/DeepSeek 的 `tools + tool_calls` 用法。

2. **主链路里的手写规则分流**
   当前 6006 配置主要使用 `src/handlers/llm/openai_compatible/llm_handler_openai_compatible.py`。这里为了实时数字人体验，手写了部分规则，例如点歌、音乐控制、音乐播放时的静默闸门、web search 上下文注入。

所以，不要误解成“项目里所有工具都已经完全由大模型 tool_call 自主调用”。现在是：**标准 tool_call 框架已经存在，但当前主链路仍有部分手写逻辑。**

## 标准 Tool Call 是什么

DeepSeek/OpenAI 兼容的 tool call 基本流程是：

1. 代码把工具列表传给大模型。
2. 大模型判断用户是否需要某个工具。
3. 如果需要，大模型不直接回答，而是返回 `tool_calls`。
4. 代码执行对应工具。
5. 代码把工具结果作为 `role: "tool"` 消息塞回对话。
6. 大模型基于工具结果生成最终自然语言回答。

DeepSeek 文档里的核心结构是：

```python
response = client.chat.completions.create(
    model="...",
    messages=messages,
    tools=tools,
)
```

工具定义大致长这样：

```json
{
  "type": "function",
  "function": {
    "name": "get_weather",
    "description": "Get weather of a location",
    "parameters": {
      "type": "object",
      "properties": {
        "location": {
          "type": "string",
          "description": "城市名"
        }
      },
      "required": ["location"]
    }
  }
}
```

本项目的 `BaseTool.get_openai_schema()` 生成的就是这种格式。

## 本项目的标准工具结构

每个标准工具都应该继承 `BaseTool`：

```python
from handlers.agent.tools.base_tool import BaseTool, ToolResult


class MyTool(BaseTool):
    @property
    def name(self) -> str:
        return "my_tool"

    @property
    def description(self) -> str:
        return "告诉大模型这个工具什么时候该用。"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "用户要查询的内容。",
                }
            },
            "required": ["query"],
        }

    def execute(self, args: dict) -> ToolResult:
        query = args.get("query", "")
        return ToolResult(success=True, data={"answer": query})
```

一个工具最重要的是四件事：

- `name`：工具名。必须唯一，大模型返回 tool call 时靠它匹配工具。
- `description`：告诉大模型什么时候使用这个工具。写得越清楚，模型越容易选对。
- `parameters`：JSON Schema，告诉模型应该传哪些参数。
- `execute(args)`：真正执行工具逻辑，返回 `ToolResult`。

## ToolResult 的作用

工具不要直接返回字符串，应该返回 `ToolResult`：

```python
return ToolResult(
    success=True,
    data={"temperature": "24C"}
)
```

失败时：

```python
return ToolResult(
    success=False,
    error="天气服务暂时不可用"
)
```

`ToolResult.to_content_str()` 会把结果转成 JSON 字符串，放进 `role: "tool"` 消息里，再交给大模型继续回答。

## 工具注册方式

工具必须先注册到 `ToolRegistry`，模型才能看到它。

当前 `ToolRegistry` 提供了几个核心方法：

```python
registry.register(tool)
registry.get_schemas()
registry.execute(name, args)
```

含义是：

- `register(tool)`：把工具放进注册表。
- `get_schemas()`：把所有工具转成 OpenAI/DeepSeek 兼容的 `tools` 参数。
- `execute(name, args)`：根据工具名执行对应工具。

当前 agent handler 中默认注册了 demo 工具：

```python
registry.register(GetCurrentTimeTool())
registry.register(GetSystemInfoTool())
```

新增工具后，也应该在构建 registry 的地方注册，或者让模块提供：

```python
def register_tools(registry, **_kwargs) -> None:
    registry.register(MyTool())
```

`music_request.py` 和 `music_control.py` 就使用了这种 `register_tools()` 形式，方便以后做模块化加载。

## Agent Loop 如何调用工具

标准调用链路在 `src/handlers/agent/chat_agent_handler.py` 中，核心过程如下：

```text
用户输入
  ↓
准备 messages
  ↓
registry.get_schemas() 生成 tools 参数
  ↓
LLM 流式返回文本或 tool_calls
  ↓
如果没有 tool_calls：直接输出文本
  ↓
如果有 tool_calls：
  1. 把 assistant 的 tool_calls 追加到 messages
  2. json.loads() 解析工具参数
  3. registry.execute(tool_name, args)
  4. 把工具结果作为 role=tool 追加到 messages
  5. 再请求 LLM，让模型根据工具结果继续回答
```

伪代码如下：

```python
tools_param = registry.get_schemas()

response = llm_client.chat.completions.create(
    model=model,
    messages=messages,
    tools=tools_param,
    stream=True,
)

full_text, tool_calls = read_stream(response)

if not tool_calls:
    return full_text

messages.append({
    "role": "assistant",
    "content": full_text or None,
    "tool_calls": tool_calls,
})

for tc in tool_calls:
    args = json.loads(tc["arguments"])
    result = registry.execute(tc["name"], args)
    messages.append({
        "role": "tool",
        "tool_call_id": tc["id"],
        "content": result.to_content_str(),
    })

# 再调一次模型，拿最终回答
```

这就是标准 tool call 的闭环。

## 为什么不需要单独的意图识别模块

标准 tool call 模式下，一般不需要单独写“意图识别模块”。

原因是：工具的 `description` 和 `parameters` 本身就是给模型看的“意图说明”。模型会根据用户输入和工具说明，自己判断要不要调用工具。

例如：

```python
description = "当用户询问当前时间、今天几号、星期几时使用。"
```

用户问“今天星期几”，模型就应该返回：

```json
{
  "name": "get_current_time",
  "arguments": "{}"
}
```

也就是说，**意图理解主要由大模型 + tool schema 完成**。

## 但为什么本项目还有手写规则

实时数字人项目有一些普通文本机器人没有的问题：

- 用户说话会触发 ASR。
- ASR 输出会进入 LLM。
- LLM 回复会进入 TTS。
- TTS 又会驱动数字人口型和音频。

音乐播放时，如果用户跟着唱，ASR 可能把歌词当成用户问题。此时如果完全交给 LLM，就可能出现数字人一边听歌一边乱回答。

所以当前主链路里保留了一些规则闸门：

- 点歌语句：识别后发送前端播放器事件。
- 音乐控制词：只允许 `暂停`、`继续`、`音量小一点`、`停止音乐` 等通过。
- 音乐播放时：非音乐控制词会被吞掉，不触发 LLM 回答。
- web search：根据配置在 LLM 请求前注入搜索上下文。

这些不是标准 tool call 的核心机制，而是实时语音场景的保护逻辑。

## 当前已有工具概览

### `demo_tools.py`

示例工具，用来验证 tool call 链路：

- `get_current_time`
- `get_system_info`

适合学习最小工具怎么写。

### `music_request.py`

点歌工具。

职责：

- 根据歌曲名搜索音乐。
- 返回歌曲信息、候选列表、播放链接。
- 不负责前端播放。
- 不负责暂停/继续。

这体现了一个重要原则：**工具只做一件事。**

### `music_control.py`

音乐控制工具。

职责：

- 把 `pause`、`resume`、`next`、`volume`、`mute`、`unmute` 等动作规范成结构化结果。
- 实际播放控制由前端播放器完成。

它和 `music_request.py` 分开，是为了降低耦合。

### 其他工具

例如：

- `exec_approve.py`
- `pending_confirmations_tool.py`
- `spawn_agent.py`

这些工具服务于更复杂的 agent 任务流程，比如确认、子任务、异步执行等。

## 新增一个工具的推荐步骤

### 第一步：新建文件

在 `src/handlers/agent/tools/` 下新建：

```text
my_tool.py
```

### 第二步：继承 BaseTool

```python
from handlers.agent.tools.base_tool import BaseTool, ToolResult


class MyTool(BaseTool):
    @property
    def name(self) -> str:
        return "my_tool"

    @property
    def description(self) -> str:
        return "当用户需要执行某某操作时使用。"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "keyword": {
                    "type": "string",
                    "description": "要处理的关键词。",
                }
            },
            "required": ["keyword"],
        }

    def execute(self, args: dict) -> ToolResult:
        keyword = args.get("keyword", "")
        return ToolResult(success=True, data={"keyword": keyword})
```

### 第三步：提供注册函数

```python
def register_tools(registry, **_kwargs) -> None:
    registry.register(MyTool())
```

### 第四步：让 agent handler 注册它

目前可以在 `_build_tool_registry()` 里显式注册，或者后续扩展一个模块加载器，读取配置里的工具模块列表，再调用每个模块的 `register_tools()`。

最简单的显式注册：

```python
from handlers.agent.tools.my_tool import MyTool

registry.register(MyTool())
```

### 第五步：写清楚 description

`description` 是模型判断是否调用工具的关键。

不推荐：

```text
处理东西。
```

推荐：

```text
当用户要求查询订单状态、询问订单是否发货、提供订单号让系统查询时使用。
```

### 第六步：让参数尽量结构化

不推荐只有一个万能参数：

```json
{"text": "用户原话"}
```

更推荐：

```json
{
  "order_id": "订单号",
  "include_logistics": true
}
```

模型传结构化参数，工具会更稳定。

## 工具设计原则

### 1. 一个工具只做一类事情

例如：

- 点歌：`music_request`
- 控制播放器：`music_control`
- 搜索网页：`web_search`

不要把“点歌、暂停、下一首、收藏、搜索网页”都塞进一个工具。

### 2. 工具返回结构化数据

工具最好返回 JSON 数据，而不是一大段自然语言。

推荐：

```json
{
  "title": "稻香",
  "artist": "周杰伦",
  "play_url": "https://..."
}
```

不推荐：

```text
我帮你找到了周杰伦的稻香，链接是 https://...
```

因为结构化结果更方便前端、后端、模型继续处理。

### 3. 工具不要偷偷直接控制 UI

工具只返回“该做什么”，真正执行可以交给调用方。

例如点歌工具返回：

```json
{
  "type": "music.play",
  "url": "https://..."
}
```

前端播放器收到后再播放。

### 4. 失败信息要短

工具失败时不要把长栈信息直接展示给用户。

推荐：

```python
ToolResult(success=False, error="音乐源暂时不可用，请稍后再试。")
```

不推荐把 HTTP 405、SSL 证书、完整 URL 全部塞给用户。

### 5. 能配置的不要写死

比如音乐源、搜索 API、超时时间，最好通过环境变量或配置文件传入。

## 和 DeepSeek Tool Calls 的关系

本项目的 `BaseTool.get_openai_schema()` 输出的是 OpenAI/DeepSeek 兼容 schema：

```json
{
  "type": "function",
  "function": {
    "name": "...",
    "description": "...",
    "parameters": {...}
  }
}
```

这和 DeepSeek Tool Calls 文档中的基本结构一致。

如果未来要使用 DeepSeek strict 模式，需要额外注意：

- `function.strict` 需要设置为 `true`。
- `parameters.additionalProperties` 通常要是 `false`。
- strict 模式下 object 的属性要求更严格。

当前项目基础 schema 兼容普通 tool call，但并不是所有工具都已经完全按 strict 模式补齐。

## 重构其他项目时的最小模板

如果要把别的项目改造成类似模式，最少需要四个部分：

### 1. BaseTool

定义所有工具都必须实现：

```python
name
description
parameters
execute(args)
```

### 2. ToolRegistry

负责：

```python
register(tool)
get_schemas()
execute(name, args)
```

### 3. Agent Loop

负责：

```text
传 tools 给模型
接收 tool_calls
执行工具
把 role=tool 的结果塞回 messages
再次请求模型生成最终回答
```

### 4. 工具文件

每个工具单独一个文件，职责清晰，返回结构化 `ToolResult`。

## 最容易踩的坑

### 坑 1：工具写了，但没有注册

没有注册到 `ToolRegistry`，模型就看不到这个工具。

### 坑 2：description 写得太模糊

模型不知道什么时候该调用。

### 坑 3：参数 schema 太随意

参数不清楚，模型容易传错。

### 坑 4：工具返回自然语言太多

后续代码不好处理。优先返回结构化 JSON。

### 坑 5：把业务闸门和工具逻辑混在一起

例如“音乐播放时忽略哼唱”是实时语音业务闸门，不应该塞进 `music_request` 工具里。工具负责点歌，闸门负责决定什么时候允许进入 LLM。

## 推荐的未来演进方向

当前项目可以继续朝这个方向演进：

1. 给工具模块做配置化加载，例如配置 `tool_modules`。
2. 让 `music_request`、`music_control`、`web_search` 都通过标准 registry 暴露。
3. 把主链路里的手写点歌分流逐步迁移到 agent tool_call。
4. 保留少量实时语音安全闸门，例如音乐播放时只允许控制词通过。
5. 如果模型服务支持 strict tool call，再逐步补齐 `strict: true` 和更严格的 JSON Schema。

## 一句话记忆

**Tool call 不是工具自己会魔法运行，而是：工具先注册成 schema，模型看到 schema 后决定调用，代码执行工具，再把结果交回模型。**

