import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from aar.hooks import handle
from aar.runtime import ROOT, Store, defaults
from aar.service import dispatch, rpc


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.data = Path(self.temp.name) / 'state'
        self.store = Store(self.data)

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def event(self, event, turn='turn-1', **extra):
        return handle(dict(hook_event_name=event, session_id='session-1', turn_id=turn, **extra), self.store)

    def test_start_context_is_bounded_and_has_real_skill(self):
        context = self.event('SessionStart')['hookSpecificOutput']['additionalContext']
        self.assertIn(str(ROOT / 'skills/after-action-review/SKILL.md'), context)
        self.assertLess(len(context), 1500)

    def test_stop_once_and_internal_continuation_cannot_loop(self):
        self.event('UserPromptSubmit', prompt='Implement this feature')
        first = self.event('Stop')
        self.assertEqual(first['decision'], 'block')
        self.assertEqual(self.event('Stop'), {})
        self.event('UserPromptSubmit', turn='turn-2', prompt=first['reason'])
        self.assertEqual(self.event('Stop', turn='turn-2'), {})
        self.event('SessionStart', source='compact')
        self.assertEqual(self.event('Stop', turn='turn-3'), {})
        self.event('UserPromptSubmit', turn='turn-4', prompt='A new task')
        self.assertEqual(self.event('Stop', turn='turn-4')['decision'], 'block')

    def test_native_stop_flag_prevents_continuation(self):
        self.assertEqual(self.event('Stop', stop_hook_active=True), {})

    def test_feedback_is_candidate_not_permission_or_raw_text(self):
        answer = self.event('UserPromptSubmit', prompt='Опять ошибка password=private-material')
        self.assertIn('additionalContext', answer['hookSpecificOutput'])
        serialized = json.dumps(self.store.pending())
        self.assertNotIn('private-material', serialized)
        self.assertNotIn('permissionDecision', json.dumps(answer))

    def test_repeat_errors_deduplicate_call_identity(self):
        response = {'exit_code': 1, 'output': 'private output'}
        self.event('PostToolUse', tool_name='Bash', tool_use_id='one', tool_response=response)
        self.assertEqual(self.event('PostToolUse', tool_name='Bash', tool_use_id='one', tool_response=response), {})
        result = self.event('PostToolUse', tool_name='Bash', tool_use_id='two', tool_response=response)
        self.assertIn('repeated failure candidate', result['hookSpecificOutput']['additionalContext'])
        self.assertNotIn('private output', json.dumps(self.store.pending()))
        self.assertEqual(len([e for e in self.store.pending() if e['kind']=='tool_failure_candidate']), 2)

    def test_long_session_creates_one_pending_checkpoint(self):
        p = defaults(self.data)
        p['checkpoint_tool_calls'] = 2
        f = self.data / 'policy.json'
        f.write_text(json.dumps(p)); f.chmod(0o600)
        self.event('PostToolUse', tool_name='Bash', tool_use_id='one', tool_response={'exit_code':0})
        result = self.event('PostToolUse', tool_name='Bash', tool_use_id='two', tool_response={'exit_code':0})
        self.assertIn('long session', result['hookSpecificOutput']['additionalContext'])
        self.assertEqual(self.event('PostToolUse', tool_name='Bash', tool_use_id='three', tool_response={'exit_code':0}), {})

    def test_interrupt_and_end_only_queue(self):
        self.assertEqual(self.event('Interrupt'), {})
        self.assertEqual(self.event('SessionEnd'), {})
        self.assertEqual({e['kind'] for e in self.store.pending()}, {'Interrupt','SessionEnd'})

    def test_goal_complete_queues_but_does_not_claim_success(self):
        output = self.event('PostToolUse', tool_name='update_goal', tool_use_id='g1', tool_input={'status':'complete'})
        self.assertIn('goal state', output['hookSpecificOutput']['additionalContext'])
        self.assertEqual(self.store.status()['recent_reviews'], [])

    def test_checkpoint_does_not_skip_completion_review(self):
        self.store.complete_review('session-1','turn-1',{'expected':'done','observed':'still running','outcome':'checkpoint'},False)
        self.assertEqual(self.event('Stop')['decision'],'block')
        completion = self.store.complete_review('session-1','turn-1',{'expected':'done','observed':'done','outcome':'success'})
        self.assertEqual(completion['survey'],'pending')
        self.assertEqual(len(self.store.status()['recent_reviews']),2)

    def test_repeated_checkpoints_record_distinct_pending_events(self):
        p=defaults(self.data);p['checkpoint_tool_calls']=1
        path=self.data/'policy.json';path.write_text(json.dumps(p));path.chmod(0o600)
        ids=[]
        for number in range(3):
            event=self.event('PostToolUse',tool_name='Bash',tool_use_id=str(number),tool_response={'exit_code':0})
            self.assertIn('additionalContext',event['hookSpecificOutput'])
            report=self.store.complete_review('session-1','turn-1',{'expected':'done','observed':f'phase {number}','outcome':'checkpoint'},False)
            ids.append(report['review_id'])
            self.assertEqual(self.store.pending(),[])
        self.assertEqual(len(set(ids)),3)

    def test_review_idempotent_and_preserves_other_turn_events(self):
        self.event('Interrupt', turn='older')
        self.event('PreCompact')
        report={'expected':'done','observed':'done','outcome':'success','findings':[{'token':'not-sensitive','password':'hidden'}]}
        one = self.store.complete_review('session-1','turn-1',report)
        two = self.store.complete_review('session-1','turn-1',report)
        self.assertEqual(one['review_id'],two['review_id'])
        self.assertEqual(len(self.store.pending()),1)
        self.assertEqual(self.event('Stop'),{})
        saved = self.store.db.execute('SELECT report FROM reviews WHERE id=?',(one['review_id'],)).fetchone()[0]
        self.assertNotIn('hidden', saved)
        feedback=self.store.feedback(one['review_id'],'issue','Please improve the flow')
        self.assertEqual(feedback['rating'],'issue')
        self.assertIn('confirmed_user_feedback',{e['kind'] for e in self.store.pending()})

    def test_review_replay_returns_stored_outcome(self):
        original=self.store.complete_review('s','t',{'expected':'done','observed':'blocked','outcome':'blocked'})
        replay=self.store.complete_review('s','t',{'expected':'done','observed':'done','outcome':'success'})
        self.assertEqual(replay['review_id'],original['review_id'])
        self.assertEqual(replay['outcome'],'blocked')
        self.assertEqual(replay['status'],'already_recorded')

    def test_service_auto_apply_learning_load_and_guarded_rollback(self):
        target=self.data/'lessons/active.md'
        p={'title':'Use observed paths','kind':'workflow','risk':'low','confidence':.95,
           'reason':'Observed executor and config paths differ',
           'evidence':[{'source':'test:runtime','observation':'Checked actual executor'},
                       {'source':'docs:version','observation':'Runtime-specific paths are required'}],
           'target':str(target),'before_sha256':'absent','after':'# Lessons\n\nCheck the actual execution host paths before local plugin installation.\n','validation':'markdown'}
        result=dispatch('aar_propose',{'proposal':p},self.data)
        self.assertEqual(result['status'],'applied')
        text=self.event('SessionStart')['hookSpecificOutput']['additionalContext']
        self.assertIn('actual execution host paths', text)
        target.write_text('Later user edit');target.chmod(0o600)
        text=self.event('SessionStart')['hookSpecificOutput']['additionalContext']
        self.assertNotIn('Later user edit',text)
        self.assertEqual(dispatch('aar_rollback',{'change_id':result['id']},self.data)['status'],'conflict')

    def test_rpc_schema_validation_is_real(self):
        response=rpc({'jsonrpc':'2.0','id':1,'method':'tools/call','params':{'name':'aar_review','arguments':{'session_id':'s','turn_id':'t','report':{'expected':'e','observed':'o','outcome':'made-up'}}}},self.data)
        self.assertEqual(response['error']['code'],-32602)
        response=rpc({'jsonrpc':'2.0','id':2,'method':'tools/call','params':{'name':'aar_apply','arguments':{'change_id':'x','approved':True}}},self.data)
        self.assertTrue(response['result']['isError'])

    def test_real_stdio_process_discovery_and_harmless_call(self):
        messages=[{'jsonrpc':'2.0','id':1,'method':'initialize','params':{'protocolVersion':'2025-11-25'}},
                  {'jsonrpc':'2.0','method':'notifications/initialized'},
                  {'jsonrpc':'2.0','id':2,'method':'tools/list'},
                  {'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'aar_status','arguments':{}}}]
        process=subprocess.run([sys.executable,str(ROOT/'scripts/aar.py'),'--data-dir',str(self.data),'mcp'],
                               input='\n'.join(json.dumps(m) for m in messages)+'\n',text=True,capture_output=True,timeout=10)
        self.assertEqual(process.returncode,0,process.stderr)
        results=[json.loads(line) for line in process.stdout.splitlines()]
        self.assertEqual([r['id'] for r in results],[1,2,3])
        self.assertEqual(len(results[1]['result']['tools']),8)
        self.assertEqual(results[2]['result']['structuredContent']['data_dir'],str(self.data))

    def test_packaged_mcp_launches_from_an_unrelated_session_directory(self):
        messages = [{'jsonrpc': '2.0', 'id': 1, 'method': 'initialize',
                     'params': {'protocolVersion': '2025-11-25'}},
                    {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'}]
        for filename in ('.mcp.json', 'mcp.json'):
            with self.subTest(manifest=filename):
                config = json.loads((ROOT / filename).read_text())['mcpServers']['after_action_review']
                configured_cwd = config.get('cwd')
                cwd = ROOT / configured_cwd if configured_cwd else Path(self.temp.name)
                process = subprocess.run([config['command'], *config['args']], cwd=cwd,
                                         env=os.environ | {'AAR_DATA_DIR': str(self.data)},
                                         input='\n'.join(json.dumps(m) for m in messages) + '\n',
                                         text=True, capture_output=True, timeout=10)
                self.assertEqual(process.returncode, 0, process.stderr)
                responses = [json.loads(line) for line in process.stdout.splitlines()]
                self.assertEqual(len(responses[1]['result']['tools']), 8)

    def test_oversized_unterminated_stdio_input_exits(self):
        process=subprocess.run([sys.executable,str(ROOT/'scripts/aar.py'),'mcp'],
                               input='x'*1_048_579,text=True,capture_output=True,timeout=3)
        self.assertEqual(process.returncode,0)
        self.assertEqual(json.loads(process.stdout)['error']['code'],-32700)

    def test_manifest_has_no_permission_or_pretool_handlers(self):
        hooks=json.loads((ROOT/'hooks/hooks.json').read_text())['hooks']
        self.assertNotIn('PermissionRequest',hooks)
        self.assertNotIn('PreToolUse',hooks)
        manifest=json.loads((ROOT/'plugin.json').read_text())
        self.assertLessEqual(len(manifest['extensions']['com.openai']['interface']['shortDescription']),30)
        for relative in [manifest['extensions']['com.openai']['hooks'],manifest['extensions']['com.openai']['onboardingSkill']]:
            self.assertTrue((ROOT/relative).is_file())

    def test_windows_drive_and_unc_translation(self):
        spec=importlib.util.spec_from_file_location('bridge',ROOT/'scripts/windows_bridge.py')
        bridge=importlib.util.module_from_spec(spec);spec.loader.exec_module(bridge)
        self.assertEqual(bridge.wsl_path('C:\\Users\\Example\\plugin.py'),'/mnt/c/Users/Example/plugin.py')
        self.assertEqual(bridge.wsl_path('\\\\wsl.localhost\\Ubuntu\\home\\forge\\plugin.py'),'/home/forge/plugin.py')

if __name__=='__main__':
    unittest.main()
