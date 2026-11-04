#!/usr/bin/env python3
"""
s01_agent_loop.py - The Agent Loop

The entire secret of an AI coding agent in one pattern:

    while True:
        response = LLM(messages, tools)
        if response contains no tool_use:
            break
        execute tools
        append results

    +----------+      +-------+      +---------+
    |   User   | ---> |  LLM  | ---> |  Tool   |
    |  prompt  |      |       |      | execute |
    +----------+      +---+---+      +----+----+
                          ^               |
                          |   tool_result |
                          +---------------+
                          (loop continues)

This is the core loop: feed tool results back to the model
until the model decides to stop. Later chapters add policy,
hooks, and lifecycle controls around it.

Usage:
    pip install anthropic python-dotenv
    ANTHROPIC_API_KEY=... python s01_agent_loop/code.py
"""

import os
import subprocess

try:
    import readline
    # 配置终端输入，改善 macOS libedit 对 UTF-8 字符和退格的处理。
    readline.parse_and_bind('set bind-tty-special-chars off')
    readline.parse_and_bind('set input-meta on')
    readline.parse_and_bind('set output-meta on')
    readline.parse_and_bind('set convert-meta off')
except ImportError:
    pass

# Anthropic Python SDK 的同步API客户端类, 负责让Python与模型服务通信
# 自身包含: 1)认证配置:api_key、auth_token... 配置访问API所需身份凭证
#          2)请求配置:base_url、timeout... 服务地址和超时
#          3)消息接口:message 调用llm生成的回复
#          4)模型接口:models 查询模型信息   
#          5)其他接口:如skills、files...
from anthropic import Anthropic
from dotenv import load_dotenv

# 从.env加载配置，并将其加载到当前Python进程的环境变量中,
# 后续可通过os.getenv("name")取出环境变量的值
# override=true说明覆盖同名环境变量,命令行运行从当前工作目录os.getcwd()逐级向上查找
load_dotenv(override=True)

# 移除额外的Token认证配置。
if os.getenv("ANTHROPIC_BASE_URL"):
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

# 初始化 API 客户端，模型名称由当前环境变量读取。
client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
MODEL = os.environ["MODEL_ID"]

# 根据当前的操作系统，告诉模型应该使用那种命令行语法,若使windows使用cmd.exe不要使用Unix Bash Powershell
#                  若是Linux/macos则使用系统Shell
ENVIRONMENT_PROMPT = (
    "Windows: the bash tool runs through cmd.exe; use cmd.exe syntax, not Unix "
    "Bash or PowerShell syntax"
    if os.name == "nt"
    else "Unix-like: the bash tool runs the system shell"
)
# System Prompt喂给llm：角色设定、当前工作目录、使用cmd.exe/system shell去运行bash tool
SYSTEM = (
    f"You are a coding agent at {os.getcwd()}. Environment: {ENVIRONMENT_PROMPT}. "
    "Use bash to solve tasks. Act, don't explain."
)

# 是ANTHROPIC官方支持的工具声明格式,Tools使用的是Claude Messages API
# 官方文档：https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools#specifying-client-tools
# Claude规定工具声明如下: name、description、input_schema (描述调用tool时,输入参数形式)
# input_schema 用来说明tool调用时需要使用什么参数,参数的类型,以及必须有的字段。
TOOLS = [{
    "name": "bash",
    "description": "Run a shell command.",
    "input_schema": {
        "type": "object",   # 输入应该是一个对象
        "properties": {"command": {"type": "string"}},  # command字段/参数名,其值类型是string
        "required": ["command"],    # command字段必须得有
    },
}]


# 执行模型请求的命令，并将输出或错误转换为文本。
def run_bash(command: str) -> str:
    # 简单拦截包含危险片段的命令（并非完整的安全隔离机制）。
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    if any(d in command for d in dangerous):
        return "Error: Dangerous command blocked"
    try:
        # 在当前目录执行命令，捕获标准输出和错误，最多等待 120 秒。
        # command是要执行的命令字符串,shell=True时通过shell解释执行命令
        r = subprocess.run(command, shell=True, cwd=os.getcwd(),
                           capture_output=True, text=True, errors="replace", timeout=120)
        # 把命令行标准输出放前面,标准错误放后面,去掉字符串首尾空白字符
        out = (r.stdout + r.stderr).strip()
        # 限制返回内容长度，避免工具输出占用过多上下文。
        return out[:50000] if out else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"
    except (FileNotFoundError, OSError) as e:
        return f"Error: {e}"


# 核心循环：调用模型 → 执行工具 → 回传结果，直到模型不再调用工具。
def agent_loop(messages: list):
    while True:
        # client.messages.create(...), 调用前面创建Anthropic客户端发送请求,等待模型response
        # model指定使用的模型名称, system是system prompt, messages提供当前对话历史
        # tools传递工具列表(官方支持的声明方式name,description,input_schema...)
        response = client.messages.create(
            model=MODEL, system=SYSTEM, messages=messages,
            tools=TOOLS, max_tokens=8000,
        )

        # 保留模型的完整回复，包括文本和工具调用请求,追加到messages列表中,元素是dict
        messages.append({"role": "assistant", "content": response.content})

        # 提取工具调用；没有工具调用时，本轮任务结束。
        # 只保留类型为"tool_use"的工具调用的content_block
        tool_calls = [
            block for block in response.content if block.type == "tool_use"
        ]
        if not tool_calls:
            return

        # 依次执行工具调用，并用调用ID将结果关联到对应请求。
        results = []
        # 筛选所有的工具调用,block.input类似{"command":"ls"}这种{参数名:值}字典
        # output是命令行输出结果
        for block in tool_calls:
            print(f"\033[33m$ {block.input['command']}\033[0m")
            output = run_bash(block.input["command"])
            print(output[:200])
            results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": output,
            })

        # 按 API 协议以 user 消息回传工具结果，供下一轮模型调用使用。
        messages.append({"role": "user", "content": results})


# 交互入口：持续读取用户输入，复用 history 保留多轮对话上下文。
if __name__ == "__main__":
    print("s01: Agent Loop")
    print("Enter a question, press Enter to send. Type q to quit.\n")

    history = []
    while True:
        try:
            # \001/\002 标记不占显示宽度的颜色控制符，避免光标位置错乱。
            query = input("\001\033[36m\002s01 >> \001\033[0m\002")
        except (EOFError, KeyboardInterrupt):
            break
        if not query.strip():
            continue
        if query.strip().lower() in ("q", "exit"):
            break
        # 记录历史对话记录,使agent拥有记忆
        # 先记录user的query问题
        history.append({"role": "user", "content": query})
        agent_loop(history)
        # 只打印模型最终回复中的文本块。
        response_content = history[-1]["content"]
        if isinstance(response_content, list):
            for block in response_content:
                if getattr(block, "type", None) == "text":
                    print(block.text)
        print()
