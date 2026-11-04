#!/usr/bin/env python3
"""Run an unchanged chapter with SDK/function boundary recording."""
import argparse
import builtins
import contextvars
import functools
import inspect
import json
import os
from pathlib import Path
import re
import runpy
import sys
import threading
import time
import uuid

from catalog import ROOT, lesson_path

ACTIVE_CALL = contextvars.ContextVar('visualizer_call', default=None)
SECRET_KEY = re.compile(r'api.?key|authorization|auth.?token|password|secret|cookie', re.I)


class Recorder:
    def __init__(self, chapter, directory=None):
        directory = directory or Path(__file__).resolve().parent / 'traces'
        directory.mkdir(parents=True, exist_ok=True)
        self.path = directory / f'{chapter}-{time.time_ns()}.jsonl'
        self.file = self.path.open('x', encoding='utf-8')
        os.chmod(self.path, 0o600)
        self.chapter = chapter
        self.lock = threading.RLock()
        self.seq = 0
        self.started = time.monotonic()
        self.secrets = set()
        self.refresh_secrets()

    def refresh_secrets(self):
        self.secrets.update(v for k, v in os.environ.items() if SECRET_KEY.search(k) and len(v) >= 6)

    def clean(self, value, depth=0):
        if depth > 30:
            return '[nested value omitted]'
        if hasattr(value, 'model_dump'):
            value = value.model_dump(mode='json')
        if isinstance(value, dict):
            return {str(k): '[REDACTED]' if SECRET_KEY.search(str(k)) else self.clean(v, depth + 1)
                    for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self.clean(v, depth + 1) for v in value]
        if isinstance(value, str):
            for secret in sorted(self.secrets, key=len, reverse=True):
                value = value.replace(secret, '[REDACTED]')
            value = re.sub(r'sk-[A-Za-z0-9_-]{12,}', '[REDACTED]', value)
            if len(value) > 200_000:
                return value[:200_000] + '\n[truncated after 200000 characters]'
            return value
        if value is None or isinstance(value, (bool, int, float)):
            return value
        # Avoid repr(client) and other arbitrary objects carrying credentials.
        return f'<{type(value).__name__}>'

    def emit(self, kind, src, dst, title, payload=None, **extra):
        with self.lock:
            self.refresh_secrets()
            self.seq += 1
            row = self.clean({'id': self.seq, 'kind': kind, 'from': src, 'to': dst,
                              'title': title, 'payload': payload, 'chapter': self.chapter,
                              'elapsed': round(time.monotonic() - self.started, 3),
                              'thread': threading.current_thread().name, **extra})
            self.file.write(json.dumps(row, ensure_ascii=False) + '\n')
            self.file.flush()

    def location(self):
        frame = sys._getframe(1)
        while frame:
            path = Path(frame.f_code.co_filename)
            if path.name == 'code.py' and path.parent.parent == ROOT:
                return {'file': str(path.relative_to(ROOT)), 'line': frame.f_lineno,
                        'function': frame.f_code.co_name}
            frame = frame.f_back
        return None

    def patch_sdk(self):
        from anthropic.resources.messages import Messages, AsyncMessages
        restorations = []
        for cls in (Messages, AsyncMessages):
            original = cls.create
            restorations.append((cls, original))
            if cls is AsyncMessages:
                @functools.wraps(original)
                async def async_create(instance, *args, _original=original, **kwargs):
                    call_id = uuid.uuid4().hex[:12]
                    parent = ACTIVE_CALL.get()
                    token = ACTIVE_CALL.set(call_id)
                    self.emit('llm_request', 'agent', 'llm', '发送 LLM 请求', kwargs,
                              call_id=call_id, parent_id=parent, source=self.location())
                    try:
                        response = await _original(instance, *args, **kwargs)
                        self.emit('llm_response', 'llm', 'agent', '收到 LLM 响应', response,
                                  call_id=call_id, source=self.location())
                        return response
                    except Exception as e:
                        self.emit('error', 'llm', 'agent', 'LLM 请求失败', str(e), call_id=call_id)
                        raise
                    finally:
                        ACTIVE_CALL.reset(token)
                cls.create = async_create
            else:
                @functools.wraps(original)
                def create(instance, *args, _original=original, **kwargs):
                    call_id = uuid.uuid4().hex[:12]
                    parent = ACTIVE_CALL.get()
                    token = ACTIVE_CALL.set(call_id)
                    self.emit('llm_request', 'agent', 'llm', '发送 LLM 请求', kwargs,
                              call_id=call_id, parent_id=parent, source=self.location())
                    try:
                        response = _original(instance, *args, **kwargs)
                        self.emit('llm_response', 'llm', 'agent', '收到 LLM 响应', response,
                                  call_id=call_id, source=self.location())
                        return response
                    except Exception as e:
                        self.emit('error', 'llm', 'agent', 'LLM 请求失败', str(e), call_id=call_id)
                        raise
                    finally:
                        ACTIVE_CALL.reset(token)
                cls.create = create
        return restorations

    def profile(self, frame, event, arg):
        if event not in ('call', 'return'):
            return
        path = Path(frame.f_code.co_filename)
        if path.name != 'code.py' or path.parent.parent != ROOT:
            return
        name = frame.f_code.co_name
        # SDK capture is authoritative; these are additional function boundaries.
        if not (name.startswith('run_') or name in {
            'execute_tool', '_run_tool', 'trigger_hooks', 'check_permission',
            'evaluate_after_turn', 'send_message', 'emit', 'prepare_context',
            'collect_background_results', 'consume_cron_queue'}):
            return
        # Profiling async functions reports yields as returns; omit these ambiguous boundaries.
        if frame.f_code.co_flags & (inspect.CO_COROUTINE | inspect.CO_GENERATOR | inspect.CO_ASYNC_GENERATOR):
            return
        loc = {'file': str(path.relative_to(ROOT)), 'line': frame.f_lineno, 'function': name}
        is_tool = name.startswith('run_') or name in {'execute_tool', '_run_tool'}
        target = 'tool' if is_tool else 'runtime'
        if event == 'call':
            count = frame.f_code.co_argcount + frame.f_code.co_kwonlyargcount
            params = {k: frame.f_locals[k] for k in frame.f_code.co_varnames[:count]
                      if k in frame.f_locals and k not in ('self', 'client', 'handlers')}
            self.emit('function_call', 'agent', target, name + '() 调用', params,
                      source=loc, frame_id=str(id(frame)))
        else:
            self.emit('function_return', target, 'agent', name + '() 返回', arg,
                      source=loc, frame_id=str(id(frame)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('chapter', help='s01 … s17')
    parser.add_argument('args', nargs=argparse.REMAINDER, help='arguments forwarded to chapter')
    args = parser.parse_args()
    try:
        script = lesson_path(args.chapter)
    except (ValueError, StopIteration):
        parser.error('chapter must be s01 … s17')
    recorder = Recorder(args.chapter)
    patches = recorder.patch_sdk()
    original_input = builtins.input
    original_argv = sys.argv[:]
    old_profile = sys.getprofile()
    old_thread_profile = threading.getprofile()

    def recorded_input(prompt=''):
        recorder.emit('input_wait', 'agent', 'user', '等待终端输入', prompt)
        text = original_input(prompt)
        recorder.emit('user_input', 'user', 'agent', '用户输入 / 确认', text)
        return text

    builtins.input = recorded_input
    sys.argv = [str(script), *args.args]
    recorder.emit('session_start', 'runtime', 'agent', '真实录制开始', {'script': str(script.relative_to(ROOT)), 'argv': args.args})
    print(f'\n[信息流录制] {recorder.path}\n网页切换到「真实记录」即可查看。\n', flush=True)
    sys.setprofile(recorder.profile)
    threading.setprofile(recorder.profile)
    try:
        runpy.run_path(str(script), run_name='__main__')
    except (Exception, KeyboardInterrupt) as e:
        recorder.emit('error', 'runtime', 'agent', '程序异常 / 中断', str(e))
        raise
    finally:
        sys.setprofile(old_profile)
        threading.setprofile(old_thread_profile)
        builtins.input = original_input
        sys.argv = original_argv
        for cls, original in patches:
            cls.create = original
        recorder.emit('session_end', 'agent', 'user', '主程序已退出', None)
        # Daemon threads may still finish a callback during interpreter teardown.
        # Leave the descriptor open until process exit rather than closing under them.


if __name__ == '__main__':
    main()
