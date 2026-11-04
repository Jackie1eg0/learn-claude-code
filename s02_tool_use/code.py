#!/usr/bin/env python3
"""
s02_tool_use.py - Tools

The agent loop from s01 does not change. This lesson adds four tools
and a dispatch map:

    +----------+      +-------+      +--------------------------+
    |   User   | ---> |  LLM  | ---> | Tool Dispatch            |
    |  prompt  |      |       |      | bash       -> run_bash   |
    +----------+      +---+---+      | read_file  -> run_read   |
                          ^          | write_file -> run_write  |
                          |          | edit_file  -> run_edit   |
                          +----------+ glob       -> run_glob   |
                          tool_result+--------------------------+

  + run_read / run_write / run_edit / run_glob
  + TOOL_HANDLERS instead of a hard-coded run_bash call
  + safe_path to keep file tools inside the workspace

Key insight: the loop stays the same; only tool registration and dispatch grow.
             Agent Loop保持不变,变化的是Tool的注册和调度会增加
"""

import os
import subprocess
from pathlib import Path

try:
    import readline
    readline.parse_and_bind('set bind-tty-special-chars off')
    readline.parse_and_bind('set input-meta on')
    readline.parse_and_bind('set output-meta on')
    readline.parse_and_bind('set convert-meta off')
except ImportError:
    pass

from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv(override=True)
if os.getenv("ANTHROPIC_BASE_URL"):
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

WORKDIR = Path.cwd()    # 当前工作目录
client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
MODEL = os.environ["MODEL_ID"]

ENVIRONMENT_PROMPT = (
    "Windows: the bash tool runs through cmd.exe; use cmd.exe syntax, not Unix "
    "Bash or PowerShell syntax, and prefer dedicated file tools for file operations"
    if os.name == "nt"
    else "Unix-like: the bash tool runs the system shell"
)

SYSTEM = (
    f"You are a coding agent at {WORKDIR}. Environment: {ENVIRONMENT_PROMPT}. "
    "Use tools to solve tasks. Act, don't explain."
)


# -- From s01 (unchanged) --

def run_bash(command: str) -> str:
    dangerous = ["rm -rf /", "sudo", "shutdown", "reboot", "> /dev/"]
    if any(d in command for d in dangerous):
        return "Error: Dangerous command blocked"
    try:
        r = subprocess.run(command, shell=True, cwd=WORKDIR,
                           capture_output=True, text=True, errors="replace",
                           timeout=120)
        out = (r.stdout + r.stderr).strip()
        return out[:50000] if out else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"
    except (FileNotFoundError, OSError) as e:
        return f"Error: {e}"


# -- New in s02: four tools --
# 在 s01 的 bash 基础上新增 read_file、write_file、edit_file、glob，共五个工具。
# 下方函数负责真正执行操作；模型只通过 TOOLS 中的名称和参数请求调用。

# 文件工具共用的路径检查函数,返回绝对路径
def safe_path(p: str) -> Path:
    # WORKDIR 是程序启动时的工作目录。相对路径以它为基准；绝对路径保持自身位置。
    # resolve() 将路径规范化为绝对路径，并解析 .. 和符号链接。
    path = (WORKDIR / p).resolve()
    # 检查解析后的路径是否仍在工作目录内，越界时抛出异常，由调用方转为错误文本。
    if not path.is_relative_to(WORKDIR):
        raise ValueError(f"Path escapes workspace: {p}")
    return path


# read_file：读取UTF-8文本；path为文件路径(必须有的)，limit(可选的)返回行数上限。
def run_read(path: str, limit: int | None = None) -> str:
    try:
        # 先检查路径再读入整个文件并按行拆分；limit不限制实际读取的文件大小。
        lines = safe_path(path).read_text(encoding="utf-8").splitlines()
        # 正常传入正整数时保留前 limit 行，并提示剩余行数；None 或 0 不截断。
        if limit and limit < len(lines):
            lines = lines[:limit] + [f"... ({len(lines) - limit} more lines)"]
        # 将各行重新拼成字符串，作为工具结果返回给agent_loop。
        return "\n".join(lines)
    except Exception as e:
        # 将读取失败等异常转成工具结果，让模型能够看到错误并调整下一步操作。
        return f"Error: {e}"


# write_file：将 content 写入指定文件；文件不存在则创建，已存在则覆盖全部内容。
# 其中path、content参数都是required
def run_write(path: str, content: str) -> str:
    try:
        # safe_path(path)检查路径是否越界,不检查文件是否存在,返回的是绝对路径
        file_path = safe_path(path)
        # 确保文件写入的目录存在,不存在创建,可创建多级子目录
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error: {e}"


# edit_file: 读取已有文件,将第一处完全匹配的old_text替换为new_text
def run_edit(path: str, old_text: str, new_text: str) -> str:
    try:
        file_path = safe_path(path)
        text = file_path.read_text(encoding="utf-8")
        # 若old_text找不到对应文本,就返回报错
        if old_text not in text:
            return f"Error: text not found in {path}"
        # old_text找到首处匹配的内容,进行文本内容替换
        file_path.write_text(text.replace(old_text, new_text, 1), encoding="utf-8")
        return f"Edited {path}"
    except Exception as e:
        return f"Error: {e}"

# run_glob的作用是:按照你提供的路径匹配规则,查找文件或文件夹,并返回它们的路径。
# 其中pattern时路径匹配规则,是required
def run_glob(pattern: str) -> str:
    import glob as g
    try:
        # 查找符合规则的路径,能搜索多层子目录
        matches = sorted({
            match for match in g.glob(
                pattern, root_dir=WORKDIR, recursive=True)
            if (WORKDIR / match).resolve().is_relative_to(WORKDIR)
        })
        # 最多展示 200 条结果，避免工具输出占用过多上下文；搜索本身并未限制为200 条。
        shown = matches[:200]
        if len(matches) > 200:
            shown.append("... (more matches omitted; narrow the pattern)")
        return "\n".join(shown) if shown else "(no matches)"
    except Exception as e:
        return f"Error: {e}"


# -- New in s02: tool definitions (one tool in s01, five in s02) --
# TOOLS 是发送给LLM工具说明：name 用于选择工具，description 说明用途，
# input_schema描述tool调用所需参数，required说明参数必须提供,注册5个Tool

TOOLS = [
    {"name": "bash", "description": "Run a shell command.",
     "input_schema": {"type": "object", "properties": {"command": {"type": "string"}}, "required": ["command"]}},
    {"name": "read_file", "description": "Read file contents.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "limit": {"type": "integer"}}, "required": ["path"]}},
    {"name": "write_file", "description": "Write content to a file.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}},
    {"name": "edit_file", "description": "Replace exact text in a file once.",
     "input_schema": {"type": "object", "properties": {"path": {"type": "string"}, "old_text": {"type": "string"}, "new_text": {"type": "string"}}, "required": ["path", "old_text", "new_text"]}},
    {"name": "glob", "description": "Find files matching a glob pattern; ** matches recursively.",
     "input_schema": {"type": "object", "properties": {"pattern": {"type": "string"}}, "required": ["pattern"]}},
]

# -- New in s02: dispatch map (replaces s01's hard-coded run_bash call) --
# TOOL_HANDLES是字典,根据工具名称找到对应的Python函数

TOOL_HANDLERS = {
    "bash": run_bash, "read_file": run_read, "write_file": run_write,
    "edit_file": run_edit, "glob": run_glob,
}


# -- The agent loop keeps the same shape as s01; only dispatch changes --
# Agent Loop流程不变,改变的是如何根据模型请求,选择并调用工具函数
# s01: 固定调用run_bash缺乏灵活性 
#      output = run_bash(block.input["command"]) 

# s02: 根据工具块中tool_name选择函数,再传入input字段(字典,llm给出的tool调用需要的参数及值) 
#      **进行关键字传参,最终调用tool
#      output = TOOL_HANDLERS[block.name](**block.input)

def agent_loop(messages: list):
    while True:
        response = client.messages.create(
            model=MODEL, system=SYSTEM, messages=messages,
            tools=TOOLS, max_tokens=8000,
        )
        messages.append({"role": "assistant", "content": response.content})

        # 如果content block的type属性是"tool_use"说明这是tool_use的block
        # 把llm回复的内容(包含input属性:LLM给出的tool_args以及value)加入tool_calls记录中
        tool_calls = [
            block for block in response.content if block.type == "tool_use"
        ]
        if not tool_calls:
            return

        results = []
        for block in tool_calls:
            print(f"\033[33m> {block.name}\033[0m")
            # 确定tool调用使用哪个fuc()
            handler = TOOL_HANDLERS.get(block.name)
            # **block.input 把参数字典展开为关键字参数传入，例如{"path": "a.txt"}
            output = handler(**block.input) if handler else f"Unknown: {block.name}"
            print(str(output)[:200])    # 预览结果,取前200个字符
            # 把tool调用的结果送入results列表中
            results.append({"type": "tool_result", "tool_use_id": block.id, "content": output})
        
        # 把本轮涉及的tool_use的result全部汇总成一条message加入列表中
        messages.append({"role": "user", "content": results})


if __name__ == "__main__":
    print("s02: Tool Use - four tools added to s01")
    print("Enter a question, press Enter to send. Type q to quit.\n")
    # 记录对话历史
    history = []
    while True:
        try:
            # \001/\002 tell Readline the ANSI escapes have zero display width.
            query = input("\001\033[36m\002s02 >> \001\033[0m\002")
        except (EOFError, KeyboardInterrupt):
            break
        if not query.strip():
            continue
        if query.strip().lower() in ("q", "exit"):
            break
        history.append({"role": "user", "content": query})
        agent_loop(history)
        for block in history[-1]["content"]:
            # 模型最后回答的text block作为最终文本传给User
            if getattr(block, "type", None) == "text":
                print(block.text)
        print()
