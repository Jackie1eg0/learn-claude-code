"""Read-only lesson metadata. Never imports or executes lesson modules."""
import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LESSONS = {
    's01': ('Agent 循环', '把模型的决定变成行动', 'LLM 只返回内容和工具调用。Agent 执行工具，把结果加入 messages，再次请求 LLM。没有 tool_use 时，这个循环才结束。'),
    's02': ('工具分发', '同一个循环，多种能力', 'tools 发送的是名称、描述与 JSON Schema，不是 Python 函数。Agent 根据 name 将 input 分发给本地函数。'),
    's03': ('权限控制', '先判断，再执行', '模型提出工具调用之后，Agent 先检查拒绝列表、规则和用户许可。获准后才执行；拒绝也应作为结果反馈。'),
    's04': ('Hooks 钩子', '在执行边界插入逻辑', 'UserPromptSubmit、PreToolUse、PostToolUse、Stop 在程序的不同边界运行。钩子不是模型内部的思考。'),
    's05': ('任务清单', '把计划变成显式状态', 'todo_write 更新结构化清单。清单状态通过工具结果回到模型，帮助后续轮次跟踪进度。'),
    's06': ('子 Agent', '独立上下文，返回摘要', '主 Agent 用 task 委派任务。子 Agent 有自己的 messages 和工具循环；最终文本作为父级的 tool_result 返回。'),
    's07': ('技能加载', '按需读取详细指令', '初始提示只提供技能摘要；模型通过工具读取完整技能。技能内容随后成为上下文，而不是新的模型能力。'),
    's08': ('上下文压缩', '保留工作状态，缩短历史', '压缩器在上下文过长时生成摘要并整理消息。tool_use 和 tool_result 的配对关系必须保留。'),
    's09': ('长期记忆', '跨会话保存有用的信息', '相关记忆可以进入 system；会话后的提取与整理也可能单独调用模型。记忆不等于模型权重。'),
    's10': ('任务依赖', '先完成前置，再领取任务', '任务存储维护 ID、状态与 blockedBy。工具返回的任务 ID 用于后续更新、领取和完成操作。'),
    's11': ('后台任务', '启动和完成是两个时刻', '启动返回任务标识，后台线程继续执行。完成通知之后才进入消息历史，模型可以据此继续工作。'),
    's12': ('定时调度', '时间触发新的输入', '调度器把到期任务送入运行时队列。定时器决定何时触发，Agent 循环负责处理实际任务。'),
    's13': ('Agent 团队', '多个上下文通过协议协作', '队友拥有独立循环，通过邮箱传递消息。任务领取、计划审批和关闭请求是显式协议，不是共享同一份思考。'),
    's14': ('MCP 插件', '发现外部工具，再调用', 'MCP 连接后发现工具 Schema，加入工具池。模型看到工具定义，宿主负责把调用转发到对应服务。'),
    's15': ('完整 Harness', '把运行机制接到一个循环', '权限、上下文、工具池、后台通知与恢复逻辑共同围绕模型调用工作。每一轮都可能携带更新后的上下文和工具。'),
    's16': ('Workflow 运行时', '一个工具承载一段固定流程', 'Workflow 接受 name、args 和可选 run_id。内部执行并行或流水线步骤，记录 journal，最终返回结构化结果。'),
    's17': ('Goal 循环', '本轮结束后，再检查目标', '主模型没有 tool_use 时，独立判断器读取完成条件和对话证据。未完成就追加继续理由；完成才放行。判断器没有工具。'),
}


def chapters():
    return [{'id': k, 'title': v[0], 'subtitle': v[1], 'insight': v[2]} for k, v in LESSONS.items()]


def lesson_path(chapter):
    if chapter not in LESSONS:
        raise ValueError('unknown chapter')
    return next(ROOT.glob(chapter + '_*/code.py'))


def source_info(chapter):
    path = lesson_path(chapter)
    source = path.read_text(encoding='utf-8')
    tree = ast.parse(source)
    functions = [{'name': n.name, 'line': n.lineno, 'end': n.end_lineno}
                 for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]
    # Literal schemas only: no eval, imports, environment loading, or side effects.
    schemas = {}
    for n in ast.walk(tree):
        if isinstance(n, ast.Dict):
            try:
                value = ast.literal_eval(n)
            except (ValueError, TypeError, SyntaxError):
                continue
            if isinstance(value, dict) and {'name', 'input_schema'} <= value.keys():
                schemas[value['name']] = value
    if chapter == 's16':
        schemas = {**source_info('s15')['schemas'], **schemas}
    return {'path': str(path.relative_to(ROOT)), 'source': source,
            'functions': sorted(functions, key=lambda n: n['line']), 'schemas': schemas}


def lesson(chapter):
    scenario_path = ROOT / 'web/src/data/scenarios' / (chapter + '.json')
    scenario = json.loads(scenario_path.read_text(encoding='utf-8'))
    return {**next(c for c in chapters() if c['id'] == chapter),
            **source_info(chapter), 'scenario': scenario}
