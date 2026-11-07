#!/usr/bin/env python3
"""
s05_todo_write.py - TodoWrite

The model tracks its progress through a TodoManager. After three rounds
without an update, the harness adds a reminder alongside the tool results.
======== TodoWrite作为Tool用来记录Task的执行情况 ==========
    Task Status分为三种Pending、In_Progress、Completed;
    当3轮没有更新TaskList时,会在Tool_Result中注入Reminder,去提醒LLM该及时更新Task_List,以防被遗忘

    +----------+      +-------+      +--------------+
    |   User   | ---> |  LLM  | ---> | Tools        |
    |  prompt  |      |       |      | + todo_write |
    +----------+      +---^---+      +------+-------+
                          |                 | update
                          |          +------v----------+
                          |          | TodoManager     |
                          |          | [ ] pending     |
                          |          | [>] in progress |
                          |          | [x] completed   |
                          |          +------+----------+
                          | tool_result     |
                          +-----------------+

              rounds_since_todo >= 3 -> add <reminder>
"""

import ast
import json
import os
import re
import subprocess
from pathlib import Path

try:
    import readline
    readline.parse_and_bind('set bind-tty-special-chars off')
except ImportError:
    pass

from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv(override=True)
if os.getenv("ANTHROPIC_BASE_URL"):
    os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

WORKDIR = Path.cwd()
client = Anthropic(base_url=os.getenv("ANTHROPIC_BASE_URL"))
MODEL = os.environ["MODEL_ID"]

# s05 change: SYSTEM prompt adds planning guidance
ENVIRONMENT_PROMPT = (
    "Windows: the bash tool runs through cmd.exe; use cmd.exe syntax, not Unix "
    "Bash or PowerShell syntax, and prefer dedicated file tools for file operations"
    if os.name == "nt"
    else "Unix-like: the bash tool runs the system shell"
)
# System Prompt强调在执行多步骤Task时,先使用todo_write这个Tool进行Plan,
#              要求LLM先列出执行计划,再随着执行进度更新状态,避免随Contex增加,模型遗忘Task
SYSTEM = (
    f"You are a coding agent at {WORKDIR}. Environment: {ENVIRONMENT_PROMPT}. "
    "Before starting any multi-step task, use todo_write to plan your steps. "
    "Update status as you go."
)


# -- Tool implementations from s02-s04 --
def run_bash(command: str) -> str:
    try:
        r = subprocess.run(command, shell=True, cwd=WORKDIR,
                           capture_output=True, text=True, errors="replace", timeout=120)
        out = (r.stdout + r.stderr).strip()
        return out[:50000] if out else "(no output)"
    except subprocess.TimeoutExpired:
        return "Error: Timeout (120s)"

def run_read(path: str, limit: int | None = None) -> str:
    try:
        lines = (WORKDIR / path).resolve().read_text(encoding="utf-8").splitlines()
        if limit and limit < len(lines):
            lines = lines[:limit] + [f"... ({len(lines) - limit} more lines)"]
        return "\n".join(lines)
    except Exception as e:
        return f"Error: {e}"

def run_write(path: str, content: str) -> str:
    try:
        file_path = (WORKDIR / path).resolve()
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} bytes to {path}"
    except Exception as e:
        return f"Error: {e}"

def run_edit(path: str, old_text: str, new_text: str) -> str:
    try:
        file_path = (WORKDIR / path).resolve()
        text = file_path.read_text(encoding="utf-8")
        if old_text not in text:
            return f"Error: text not found in {path}"
        file_path.write_text(text.replace(old_text, new_text, 1), encoding="utf-8")
        return f"Edited {path}"
    except Exception as e:
        return f"Error: {e}"

def run_glob(pattern: str) -> str:
    import glob as g
    try:
        matches = sorted({
            match for match in g.glob(
                pattern, root_dir=WORKDIR, recursive=True)
            if (WORKDIR / match).resolve().is_relative_to(WORKDIR)
        })
        shown = matches[:200]
        if len(matches) > 200:
            shown.append("... (more matches omitted; narrow the pattern)")
        return "\n".join(shown) if shown else "(no matches)"
    except Exception as e:
        return f"Error: {e}"


# -- New in s05: structured state the model updates --
# TodoManager类为模型的TaskList,由LLM决定Task怎么拆,哪步Task已完成
#            其负责Check LLM提交的TaskLis,保存在内存中,并显示当前进度
class TodoManager:
    def __init__(self):
        self.items: list[dict] = []     # TaskList类型为list,元素为dict
    
    # 校验并全量替换TaskList，返回更新后的任务列表文本；校验失败时保留原列表。
    def update(self, todos: list | str) -> str:
        # 输入格式兼容,若todos传入为字符串,尝试解析为Python对象
        # 最终目的是为todos类型转换为list,元素是dict 如[ {"content":“阅读代码,"status":"pending"}...]
        if isinstance(todos, str):
            try:
            # 尝试按JSON解析,处理字符串
                todos = json.loads(todos)   
            except json.JSONDecodeError:
            # 若JSON解析失败,按Python字面量解析
                try:
                    todos = ast.literal_eval(todos)
                except (SyntaxError, ValueError) as e:
                    raise ValueError("todos must be a list or JSON array string") from e

        # todos解析后的结果必须是list类型,元素为dict,最多包含20项todos任务
        if not isinstance(todos, list):
            raise ValueError("todos must be a list")
        if len(todos) > 20:
            raise ValueError("Max 20 todos allowed")

        # 用临时列表收集校验通过的任务，避免中途失败时修改已有状态。
        validated = []  # 通过校验的Task  
        in_progress_count = 0   # 正在处理的Task数量
        for index, todo in enumerate(todos):
        # 如[ {"content":“阅读代码,"status":"pending"}, ...]
        # todo是dict,包含Task的content、status字段
            if not isinstance(todo, dict):
                raise ValueError(f"todos[{index}] must be an object")

            # Task Content转为字符串并去除首尾空白；若Status字段为空默认值为pending。
            # 如果后续进来,status字段非空,就取当前Status的值,不会再重新设置为pending   
            content = str(todo.get("content", "")).strip()
            status = str(todo.get("status", "pending")).lower()

            # TaskList在初始化后检查,Content须非空,Status须是规定的3种状态中的1种
            if not content:
                raise ValueError(f"todos[{index}] requires content")
            if status not in ("pending", "in_progress", "completed"):
                raise ValueError(f"todos[{index}] has invalid status '{status}'")
            # 记录In_Progress的任务数量
            if status == "in_progress":
                in_progress_count += 1
            # 将通过规范性验证的TaskList进行更新
            validated.append({"content": content, "status": status})

        # 同一时刻最多允许一个任务处于进行中，保持当前执行目标明确。
        if in_progress_count > 1:
            raise ValueError("Only one todo can be in_progress at a time")

        # 全部校验通过后再进行TaskList整体替换,将Validated列表赋值给self.items
        self.items = validated
        return self.render()

    def render(self) -> str:
        # 将内存中的TaskList格式化渲染为可读文本,不涉及TaskList修改。
        if not self.items:
            return "No todos."

        lines = []
        for todo in self.items:
            # 将三种任务状态映射为直观的标记,每个任务单独占一行,可视化文本展示。
            marker = {
                "pending": "[ ]",
                "in_progress": "[>]",
                "completed": "[x]",
            }[todo["status"]]
            # TaskStatus标记 + TaskContent
            lines.append(f"{marker} {todo['content']}")

        # 布尔值参与求和时True计为1、False计为0，据此统计已完成的任务数。
        done = sum(todo["status"] == "completed" for todo in self.items)
        # 在任务列表后空一行显示“已完成数/总数”，最后拼接为一个字符串返回。
        lines.append(f"\n({done}/{len(self.items)} completed)")
        return "\n".join(lines)


TODO = TodoManager()    # TaskList实例

def run_todo_write(todos: list | str) -> str:
    # todo_write工具对应的fuc(),尝试更新TaskList,并可视化Task执行情况
    try:
        output = TODO.update(todos)
    except ValueError as e:
        return f"Error: {e}"
    print(f"\n\033[33m## Current Tasks\033[0m\n{output}")
    return output

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
    # s05: new tool: todo_write工具,更新TaskList并获取当前Task进展
    #      todo_write实际运行的func()是run_todo_write,接受参数todos,规定为List类型并且required;
    #      并且todos内item最多含20个,每个item中必须包含content、status两个字段,前置String,后者String三选一
    {"name": "todo_write", "description": "Create and manage a task list for your current coding session.",
     "input_schema": {"type": "object", "properties": {"todos": {"type": "array", "maxItems": 20, "items": {"type": "object", "properties": {"content": {"type": "string", "minLength": 1}, "status": {"type": "string", "enum": ["pending", "in_progress", "completed"]}}, "required": ["content", "status"]}}}, "required": ["todos"]}},
]

# 工具分发,Tool_Name映射到具体Tool_Func()
TOOL_HANDLERS = {
    "bash": run_bash, "read_file": run_read, "write_file": run_write,
    "edit_file": run_edit, "glob": run_glob, "todo_write": run_todo_write,
}


# -- Hook system from s04 --

HOOKS = {"UserPromptSubmit": [], "PreToolUse": [], "PostToolUse": [], "Stop": []}

def register_hook(event: str, callback):
    HOOKS[event].append(callback)

def trigger_hooks(event: str, *args):
    for callback in HOOKS[event]:
        result = callback(*args)
        if result is not None:
            return result
    return None

DENY_LIST = ["rm -rf /", "sudo", "shutdown", "reboot", "mkfs", "dd if="]
DESTRUCTIVE_COMMAND_WORD = re.compile(
    r"(?i)(?:^|[;&|()\n])\s*(?:rm|del)(?=\s|$|[;&|()])"
)
DESTRUCTIVE = ["rm ", "> /etc/", "chmod 777"]


def contains_destructive_command(command: str) -> bool:
    return bool(DESTRUCTIVE_COMMAND_WORD.search(command))


def permission_hook(block):
    """PreToolUse: s03 permission logic, registered as an s04 hook."""
    if block.name == "bash":
        command = block.input.get("command", "")
        for pattern in DENY_LIST:
            if pattern in command:
                print(f"\n\033[31m[blocked] '{pattern}'\033[0m")
                return "Permission denied by deny list"
        if contains_destructive_command(command) or any(
            keyword in command for keyword in DESTRUCTIVE
        ):
            print(f"\n\033[33m[permission] Potentially destructive command\033[0m")
            print(f"   Tool: {block.name}({block.input})")
            choice = input("   Allow? [y/N] ").strip().lower()
            if choice not in ("y", "yes"):
                return "Permission denied by user"
    if block.name in ("read_file", "write_file", "edit_file"):
        path = block.input.get("path", "")
        if not (WORKDIR / path).resolve().is_relative_to(WORKDIR):
            print(f"\n\033[33m[permission] Access outside workspace\033[0m")
            print(f"   Tool: {block.name}({block.input})")
            choice = input("   Allow? [y/N] ").strip().lower()
            if choice not in ("y", "yes"):
                return "Permission denied by user"
    return None

def log_hook(block):
    """PreToolUse: log every tool call."""
    args_preview = str(list(block.input.values())[:2])[:60]
    print(f"\033[90m[HOOK] {block.name}({args_preview})\033[0m")
    return None

def large_output_hook(block, output):
    """PostToolUse: warn on large output."""
    if len(str(output)) > 100000:
        print(f"\033[33m[HOOK] Large output from {block.name}: {len(str(output))} chars\033[0m")
    return None

def context_inject_hook(query: str):
    """UserPromptSubmit: log working directory."""
    print(f"\033[90m[HOOK] UserPromptSubmit: working in {WORKDIR}\033[0m")
    return None

def summary_hook(messages: list):
    """Stop: print tool call count."""
    tool_count = sum(1 for m in messages
                     for b in (m.get("content") if isinstance(m.get("content"), list) else [])
                     if isinstance(b, dict) and b.get("type") == "tool_result")
    print(f"\033[90m[HOOK] Stop: session used {tool_count} tool calls\033[0m")
    return None

# 为Event绑定对应Hook
register_hook("UserPromptSubmit", context_inject_hook)
register_hook("PreToolUse", permission_hook)
register_hook("PreToolUse", log_hook)
register_hook("PostToolUse", large_output_hook)
register_hook("Stop", summary_hook)


# -- Agent loop with the reminder counter --

def agent_loop(messages: list):
    rounds_since_todo = 0
    while True:
        response = client.messages.create(
            model=MODEL, system=SYSTEM, messages=messages,
            tools=TOOLS, max_tokens=8000,
        )
        messages.append({"role": "assistant", "content": response.content})

        tool_calls = [
            block for block in response.content if block.type == "tool_use"
        ]
        if not tool_calls:
            # 没有Tool_Use请求,就结束Agent Loop
            force = trigger_hooks("Stop", messages)
            if force:
                messages.append({"role": "user", "content": force})
                continue
            return

        results = []
        used_todo = False
        for block in tool_calls:
        # 依次遍历LLM请求的Tool(可能包含TodoWrite工具,其直接执行无需PermissionHook):
        #   LLM决定何时请求调用todo_write、传什么任务列表；Agent程序负责执行本地Tool调用
        #   不过3轮提醒不会自动调用Tool,只会在下次Request附加上"<reminder>Update your todos.</reminder>"
            blocked = trigger_hooks("PreToolUse", block)
            if blocked:
                results.append({"type": "tool_result", "tool_use_id": block.id,
                                "content": str(blocked)})
                continue

            handler = TOOL_HANDLERS.get(block.name)
            try:
                # 同一调用Tool,这行代码可能会调用TodoWrite,进行TaskList更新与获取
                output = handler(**block.input) if handler else f"Unknown: {block.name}"
            except Exception as e:
                output = f"Error: {e}"

            trigger_hooks("PostToolUse", block, output)

            # 如果Tool调用过todo_write,做标记
            if block.name == "todo_write":
                used_todo = True

            results.append({"type": "tool_result", "tool_use_id": block.id,
                            "content": str(output)})
        # 本轮调用过todo_write,计数器清零,没有则累加。3轮对话没有调用todo_write,提醒LLM更新todos以防遗忘
        rounds_since_todo = 0 if used_todo else rounds_since_todo + 1
        if rounds_since_todo >= 3:
            # reminder作为text类型而不是tool_result类型返回
            results.append({"type": "text",
                            "text": "<reminder>Update your todos.</reminder>"})
            rounds_since_todo = 0

        messages.append({"role": "user", "content": results})


if __name__ == "__main__":
    print("s05: TodoWrite - plan before execution")
    print("Enter a question, press Enter to send. Type q to quit.\n")

    history = []
    while True:
        try:
            # \001/\002 tell Readline the ANSI escapes have zero display width.
            query = input("\001\033[36m\002s05 >> \001\033[0m\002")
        except (EOFError, KeyboardInterrupt):
            break
        if not query.strip():
            continue
        if query.strip().lower() in ("q", "exit"):
            break
        trigger_hooks("UserPromptSubmit", query)
        history.append({"role": "user", "content": query})
        agent_loop(history)
        for block in history[-1]["content"]:
            if getattr(block, "type", None) == "text":
                print(block.text)
        print()
