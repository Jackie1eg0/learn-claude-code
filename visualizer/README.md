# Agent Atlas · 本地信息流实验室

为 `s01`–`s17` 提供统一的交互式信息流视图。网页服务只使用 Python 标准库，不需要 Node、npm 或额外的网页依赖。

## 启动网页

在项目根目录 `learn-claude-code/`，激活现有虚拟环境后运行：

```bash
python visualizer/server.py
```

打开 **http://127.0.0.1:8765**。端口被占用时：

```bash
python visualizer/server.py --port 8766
```

服务仅绑定本机回环地址；`Ctrl+C` 停止服务。不要使用普通的文件服务器暴露整个项目目录；这个服务只提供明确列出的网页文件、章节源码与录制接口。

## 推荐学习顺序

1. 在 s01 的「教学演示」中逐步播放「创建文件 → 验证 → 回答」。
2. 选中「发送完整 LLM 请求」，查看 `model`、`system`、`messages`、`tools`、`max_tokens`。
3. 对比前后两次请求的「上下文」，观察 assistant 的 `tool_use` 与 user 的 `tool_result` 如何进入历史。
4. 查看 `tool_use.id` 与 `tool_result.tool_use_id` 的配对。
5. 切换为「直接回答」，理解为什么有些问题不需要工具。
6. 切换其他章节，观察权限、Hooks、子 Agent、记忆、后台任务、团队、Workflow 与 Goal 带来的额外边界。

支持播放、暂停、前后单步、进度拖动、倍速、任意事件选择、JSON 复制、JSON 导出、工具 Schema 查看和源码定位。左右方向键也可以单步切换。

## 录制真实运行

保持网页服务运行，在**另一个终端**进入项目根目录、激活同一个虚拟环境，然后将原命令：

```bash
python s01_agent_loop/code.py
```

换为：

```bash
python visualizer/record.py s01
```

照常在终端输入问题。网页选择同一章节，切换「真实记录」；每两秒刷新本章最新的一次录制。关闭「跟随新事件」后可回看旧步骤。每次启动录制都会创建新文件，网页自动切换到该章最新的文件。

其他章节使用相同入口：

```bash
python visualizer/record.py s06
python visualizer/record.py s13
python visualizer/record.py s16
python visualizer/record.py s17

# 原章节支持的命令行参数直接跟在章节编号后面
python visualizer/record.py s16 demo
python visualizer/record.py s17 "/goal 检查 hello.py 能否正常运行，并报告退出码"
```

真实模式运行原章节代码：会使用 `.env`、消耗 API 额度并按原逻辑执行工具；所有输入和权限确认仍在终端完成。网页本身没有远程执行命令的接口。录制入口保留启动时的工作目录。

## 数据的含义

| 内容 | 来源与准确性 |
|---|---|
| s01 完整循环演示 | 为学习构造的完整消息流，模型名、目录、调用 ID 均为示例 |
| s02–s17 教学演示 | 复用 `web/src/data/scenarios/` 中的关键事件节选，缺少的调用不伪造成完整历史 |
| 工具 Schema、源码 | 静态读取当前章节 `code.py`，不导入或执行；s16 同时读取其宿主 s15 的工具定义 |
| 真实 LLM 请求 | Anthropic SDK `messages.create()` 实际入参，写入时立即快照 |
| 真实 LLM 响应 | SDK 返回的 Message 对象，包括内容块、停止原因、用量等 |
| 函数事件 | 章节内同步工具函数、分发器和部分运行时函数的调用与返回边界 |
| 上下文页 | 按 call_id 匹配请求的 messages 快照；没有 call_id 时使用同线程最近一次请求，子 Agent / 判断器保留各自上下文 |

`call_id` 对应一次模型调用的请求、响应或异常；`thread` 区分线程；`source` 保存可定位到的源码位置。源码定位来自调用栈，线程池中的 SDK 调用未必具有章节源码栈。

记录发生在 **SDK 调用边界**，不是抓取 HTTP 网络包，也不是展示模型内部思维。SDK 自身的网络重试不会拆成多条请求。所有原始会话内容在提交 SDK 前记录，因此后续对 messages 的修改不会改变之前的事件。

目前覆盖项目使用的 Anthropic Messages **非流式**同步与异步调用。不会重写章节循环，不保证覆盖未来新增的 provider、流式接口或所有内部状态。异步函数会在暂停时多次进入/返回，录制器不把这些边界误报为工具完成；异步章节仍有 SDK 请求/响应，部分运行时 `emit()` 事件也可见。

工具分发器与实际工具函数都可能被记录，这表示嵌套调用，不表示执行了两次。函数异常展开有时会出现返回值 `null`，应结合后续错误和模型请求判断结果。

## 录制文件与隐私

录制保存在 `visualizer/traces/sXX-时间戳.jsonl`，已通过此目录下的 `.gitignore` 排除。录制器隐藏已知密钥环境变量、常见凭据字段及 `sk-` 格式密钥；提示词、源码和工具输出仍可能包含其他私人数据。分享导出文件前应检查内容。超长字符串在 200,000 字符处截断并标记。

网页仅提供本地只读接口，不加载 CDN，不读取 `.env`，不把密钥传到浏览器。网页中“数据留在本地”指可视化记录；真实运行中的原章节仍会正常向你配置的模型服务发送请求。

## 验证

使用当前项目的虚拟环境运行（无需 pytest）：

```bash
python -m unittest discover -s visualizer -p 'test_*.py' -v
node --test visualizer/test_frontend.cjs
```

Node 只用于可选的前端逻辑测试，启动和使用网页不需要 Node。测试使用模拟 HTTP 响应，不调用付费模型；s01 集成测试只执行 `printf atlas`。
