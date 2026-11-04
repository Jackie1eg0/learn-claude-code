"""Offline tests: no provider calls, no tool execution outside temporary dirs."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
import os
from pathlib import Path
import sys
import subprocess
import tempfile
import unittest
from unittest.mock import patch

try:
    import httpx2 as httpx  # Anthropic SDK 2.x
except ImportError:
    import httpx  # Anthropic SDK 0.x / 1.x
from anthropic import Anthropic, AsyncAnthropic
from anthropic.resources.messages import Messages, AsyncMessages

from catalog import chapters, lesson, lesson_path
from record import Recorder
import server


def reply(text='hello'):
    return {'id':'msg_test','type':'message','role':'assistant','model':'test-model',
            'content':[{'type':'text','text':text}],'stop_reason':'end_turn',
            'stop_sequence':None,'usage':{'input_tokens':12,'output_tokens':4}}


class VisualizerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.rec=Recorder('s01',Path(self.tmp.name))
        self.addCleanup(self.rec.file.close)

    def rows(self):
        return [json.loads(line) for line in self.rec.path.read_text().splitlines()]

    def instrument(self):
        patches=self.rec.patch_sdk()
        for cls,original in patches:
            self.addCleanup(setattr,cls,'create',original)

    def test_all_chapters_have_current_source_and_schemas(self):
        self.assertEqual(len(chapters()),17)
        for chapter in chapters():
            data=lesson(chapter['id'])
            self.assertEqual(data['source'],lesson_path(chapter['id']).read_text())
            self.assertTrue(data['scenario']['steps'])
            self.assertIn('bash',data['schemas'])
            for func in data['functions']:
                self.assertIn(func['name'],data['source'].splitlines()[func['line']-1])
        self.assertIn('Workflow',lesson('s16')['schemas'])

    def test_unknown_chapter_rejected(self):
        for value in ('../.env','s00','s18','s01/../../.env'):
            with self.assertRaises(ValueError): lesson_path(value)

    def test_sync_sdk_snapshot_and_pairing(self):
        self.instrument()
        transport=httpx.MockTransport(lambda req:httpx.Response(200,json=reply()))
        with Anthropic(api_key='sk-fake',http_client=httpx.Client(transport=transport)) as client:
            history=[{'role':'user','content':'hello'}]
            response=client.messages.create(model='test-model',max_tokens=50,messages=history)
            history.append({'role':'assistant','content':'mutated later'})
        a,b=self.rows()
        self.assertEqual(a['call_id'],b['call_id'])
        self.assertEqual(a['kind'],'llm_request')
        self.assertEqual(len(a['payload']['messages']),1)
        self.assertEqual(b['payload']['usage']['input_tokens'],12)
        self.assertEqual(response.content[0].text,'hello')

    def test_async_sdk(self):
        self.instrument()
        async def run():
            transport=httpx.MockTransport(lambda req:httpx.Response(200,json=reply()))
            async with AsyncAnthropic(api_key='sk-fake',http_client=httpx.AsyncClient(transport=transport)) as client:
                return await client.messages.create(model='test-model',max_tokens=20,messages=[{'role':'user','content':'test'}])
        asyncio.run(run())
        self.assertEqual([r['kind'] for r in self.rows()],['llm_request','llm_response'])

    def test_error_is_preserved(self):
        self.instrument()
        transport=httpx.MockTransport(lambda req:httpx.Response(400,json={'error':{'message':'invalid model','type':'invalid_request_error'}}))
        with Anthropic(api_key='fake',http_client=httpx.Client(transport=transport)) as client:
            with self.assertRaises(Exception):
                client.messages.create(model='wrong',max_tokens=20,messages=[{'role':'user','content':'test'}])
        self.assertEqual(self.rows()[-1]['kind'],'error')
        self.assertIn('invalid model',self.rows()[-1]['payload'])

    def test_secrets_redacted_and_no_object_repr(self):
        secret='sk-test-secret-do-not-record'
        with patch.dict(os.environ,{'ANTHROPIC_API_KEY':secret}):
            self.rec.emit('test','agent','llm','test',{'nested':{'x-api-key':secret},'content':'value='+secret,'Authorization':'Bearer hidden','input_tokens':19,'client':object()})
        raw=self.rec.path.read_text()
        self.assertNotIn(secret,raw)
        self.assertNotIn('Bearer hidden',raw)
        self.assertEqual(self.rows()[0]['payload']['input_tokens'],19)
        self.assertIn('<object>',raw)

    def test_threads_have_ordered_unique_events(self):
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda i:self.rec.emit('test','tool','agent','result',i),range(80)))
        rows=self.rows()
        self.assertEqual([r['id'] for r in rows],list(range(1,81)))
        self.assertEqual(len({r['payload'] for r in rows}),80)

    def test_partial_jsonl_line_does_not_hide_completed_events(self):
        self.rec.emit('test','agent','llm','first',{})
        self.rec.file.write('{"incomplete":')
        self.rec.file.flush()
        with patch.object(server,'TRACES',Path(self.tmp.name)):
            self.assertEqual(len(server.read_trace('s01')['events']),1)
            self.assertIsNone(server.read_trace('s02')['name'])

    def test_all_17_recording_entrypoints_can_start_and_quit_offline(self):
        # Each process gets fresh thread/profile state and a temporary working dir.
        driver = """
import sys, pathlib
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
import record
from anthropic.resources.messages import Messages, AsyncMessages
OriginalRecorder = record.Recorder
chapter = sys.argv[2]
sys.argv = ['record.py', chapter]
with patch('dotenv.load_dotenv'), patch.dict('os.environ', {'MODEL_ID':'test-model','ANTHROPIC_API_KEY':'fake'}, clear=True), patch('builtins.input', return_value='q'), patch.object(Messages, 'create', side_effect=AssertionError('unexpected network call')), patch.object(AsyncMessages, 'create', side_effect=AssertionError('unexpected network call')), patch.object(record, 'Recorder', side_effect=lambda chapter: OriginalRecorder(chapter, pathlib.Path.cwd() / 'traces')):
    record.main()
"""
        for chapter in chapters():
            with self.subTest(chapter=chapter['id']), tempfile.TemporaryDirectory() as work:
                result = subprocess.run([sys.executable, '-c', driver, str(Path(__file__).parent.resolve()), chapter['id']], cwd=work, capture_output=True, text=True, timeout=20)
                self.assertEqual(result.returncode, 0, result.stderr[-3000:])
                traces = list((Path(work) / 'traces').glob('*.jsonl'))
                self.assertEqual(len(traces), 1)
                rows = [json.loads(line) for line in traces[0].read_text().splitlines()]
                self.assertEqual(rows[0]['kind'], 'session_start')
                self.assertEqual(rows[-1]['kind'], 'session_end')

    def test_actual_s01_tool_loop_with_mock_llm(self):
        spec=importlib.util.spec_from_file_location('atlas_s01_test',lesson_path('s01'))
        mod=importlib.util.module_from_spec(spec)
        with patch('anthropic.Anthropic', return_value=None), patch('dotenv.load_dotenv'), patch.dict(os.environ, {'MODEL_ID':'test-model'}):
            spec.loader.exec_module(mod)
        responses=[{**reply(),'content':[{'type':'tool_use','id':'tool_1','name':'bash','input':{'command':'printf atlas'}}],'stop_reason':'tool_use'},reply('done')]
        transport=httpx.MockTransport(lambda req:httpx.Response(200,json=responses.pop(0)))
        self.instrument()
        with Anthropic(api_key='fake',http_client=httpx.Client(transport=transport)) as client:
            mod.client=client
            history=[{'role':'user','content':'print atlas'}]
            previous=sys.getprofile()
            sys.setprofile(self.rec.profile)
            try: mod.agent_loop(history)
            finally: sys.setprofile(previous)
        rows=self.rows()
        calls=[r for r in rows if r['kind']=='llm_request']
        self.assertEqual(len(calls),2)
        result=calls[1]['payload']['messages'][-1]['content'][0]
        self.assertEqual(result['tool_use_id'],'tool_1')
        self.assertEqual(result['content'],'atlas')
        self.assertTrue(any(r['title']=='run_bash() 调用' for r in rows))
        self.assertTrue(any(r['kind']=='function_return' and r['payload']=='atlas' for r in rows))


if __name__=='__main__': unittest.main()
