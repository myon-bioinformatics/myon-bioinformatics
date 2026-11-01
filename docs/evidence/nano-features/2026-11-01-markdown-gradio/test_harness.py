"""Stdlib/fake preparation only; no package import, server, curl or install."""
import contextlib
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import gradio_worker as worker
import measure_gradio as measure


class FakeProcess:
    pid = 12345
    returncode = None
    waited = False
    def poll(self): return self.returncode
    def wait(self, timeout):
        self.waited = True
        self.returncode = -15
        return self.returncode


class HarnessTests(unittest.TestCase):
    def test_server_close_attempted_even_if_client_close_fails(self):
        client=mock.Mock()
        client.close.side_effect=RuntimeError('injected client close failure')
        demo=mock.Mock()
        with self.assertRaisesRegex(RuntimeError,'injected'):
            worker.close_app(client,demo)
        for executor in (client.executor,client.helper_executor,client.stream_executor):
            executor.shutdown.assert_called_once_with(wait=True,cancel_futures=True)
        demo.close.assert_called_once_with()

    def test_server_closed_before_waiting_for_sse_executor(self):
        calls=[]
        client=mock.Mock();demo=mock.Mock()
        client.close.side_effect=lambda:calls.append('client')
        demo.close.side_effect=lambda:calls.append('server')
        for executor in (client.executor,client.helper_executor,client.stream_executor):
            executor.shutdown.side_effect=lambda **kw:calls.append('executor')
        worker.close_app(client,demo)
        self.assertEqual(calls,['client','server','executor','executor','executor'])

    def test_exact_source_and_tool_receipt(self):
        self.assertEqual(worker.verify()['revision'], worker.REVISION)

    def test_fixed_closure_rows(self):
        def rows(profile):
            return [line for line in (worker.ROOT / f'requirements-{profile}.txt').read_text().splitlines()
                    if line and not line.lstrip().startswith(('#', '--hash='))]
        full, thin = rows('full'), rows('gradio')
        self.assertEqual((len(full), len(thin)), (77, 56))
        self.assertTrue(set(thin) <= set(full))
        self.assertFalse(any(row.startswith(('streamlit==','playwright==','pytest==','pluggy==','iniconfig==')) for row in thin))

    def test_worker_gate_precedes_source_work(self):
        with mock.patch.object(worker, 'verify', side_effect=AssertionError('must not execute')):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                worker.main(['api'])

    def test_install_gate(self):
        with mock.patch.object(measure, 'install', side_effect=AssertionError('must not execute')):
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                measure.main(['install'])

    def test_explicit_matched_trial_required(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            measure.main(['workloads', '--exclusive-window'])

    def test_isolation_scrubs_inherited_secrets_and_proxies(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.dict(worker.os.environ, {'HTTP_PROXY':'fake','ALL_PROXY':'fake','OPENAI_API_KEY':'fake','GRADIO_SHARE':'True','PYTHONPATH':'/wrong'}):
            env = worker.isolated_env(Path(tmp) / 'one')
            for key in ('HTTP_PROXY','ALL_PROXY','OPENAI_API_KEY','PYTHONPATH'):
                self.assertNotIn(key, env)
            self.assertEqual(env['GRADIO_SHARE'],'False')
            self.assertEqual(env['GRADIO_ANALYTICS_ENABLED'],'False')
            self.assertEqual(env['HF_HUB_DISABLE_TELEMETRY'],'1')
            self.assertEqual(env['HF_HUB_OFFLINE'],'1')
            self.assertEqual(env['GRADIO_SSR_MODE'],'False')

    def test_each_worker_has_distinct_home_tmp_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            one=worker.isolated_env(Path(tmp)/'one');two=worker.isolated_env(Path(tmp)/'two')
            for name in ('HOME','TMPDIR','XDG_CACHE_HOME','XDG_CONFIG_HOME','HF_HOME','GRADIO_TEMP_DIR'):
                self.assertNotEqual(one[name],two[name])

    def test_loopback_ports_must_be_registered(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit=worker.Audit(Path(tmp)/'audit.jsonl')
            self.assertFalse(audit.decide('socket.connect',(None,('127.0.0.1',1234)))[0])
            self.assertTrue(audit.decide('socket.bind',(None,('127.0.0.1',1234)))[0])
            self.assertTrue(audit.decide('socket.connect',(None,('127.0.0.1',1234)))[0])
            self.assertFalse(audit.decide('socket.bind',(None,('0.0.0.0',1234)))[0])
            self.assertFalse(audit.decide('socket.connect',(None,('1.1.1.1',1234)))[0])

    def test_external_dns_and_child_execution_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            audit=worker.Audit(Path(tmp)/'audit.jsonl')
            self.assertFalse(audit.decide('socket.getaddrinfo',('huggingface.co',443))[0])
            self.assertFalse(audit.decide('subprocess.Popen',('/bin/sh',['sh','-c','anything'],None,None))[0])
            self.assertFalse(audit.decide('os.system',('anything',))[0])

    def test_denial_is_persisted_before_exception(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'audit.jsonl';audit=worker.Audit(path)
            with self.assertRaises(PermissionError): audit('socket.getaddrinfo',('outside.invalid',443))
            row=json.loads(path.read_text());self.assertFalse(row['allowed']);self.assertEqual(audit.denied,1)

    def test_exact_original_curl_commands_allowed(self):
        url='http://127.0.0.1:1234/gradio_api/call/analyze';ports={1234}
        for text in (worker.CASES['rich'],''):
            args=[worker.CURL,'-s','-X','POST',url,'-H','Content-Type: application/json','-d',json.dumps({'data':[text,None]})]
            self.assertTrue(worker.allowed_curl(worker.CURL,args,ports))
        self.assertTrue(worker.allowed_curl(worker.CURL,[worker.CURL,'-s','-N','--max-time','15.0',url+'/abc123'],ports))

    def test_curl_external_redirect_options_and_file_reads_denied(self):
        base=[worker.CURL,'-s','-N','--max-time','15.0','http://127.0.0.1:1234/gradio_api/call/analyze/abc']
        for args in (base+['-L'],base[:-1]+['https://outside.invalid/'],base[:-1]+['file:///tmp/a'],base[:-1]+['http://127.0.0.1:1235/gradio_api/call/analyze/abc']):
            self.assertFalse(worker.allowed_curl(worker.CURL,args,{1234}))

    def test_sse_retains_complete_payload_after_heartbeat(self):
        payload=['one','two','three','four']
        text='event: heartbeat\ndata: null\n\nevent: complete\ndata: '+json.dumps(payload)+'\n\n'
        self.assertEqual(worker.parse_sse(text),payload)

    def test_sse_rejects_error_duplicate_or_partial(self):
        for text in ('event: error\ndata: bad\n\n','event: complete\ndata: []\n\n','event: complete\ndata: [1,2,3,4]\n\nevent: complete\ndata: [1,2,3,4]\n\n'):
            with self.assertRaises(ValueError):worker.parse_sse(text)

    def test_independent_expected_payload_is_complete(self):
        expected=worker.EXPECTED['rich'];good=[*expected[:3],json.dumps(expected[3],indent=2)]
        worker.validate_result('rich',good)
        good[-1]=good[-1]+' '
        with self.assertRaises(AssertionError):worker.validate_result('rich',good)

    def test_identity_guard(self):
        with mock.patch.object(measure,'identities',return_value={'sha':'new'}):
            with self.assertRaises(ValueError):measure.verify_unchanged({'sha':'old'})

    def test_symlink_targets_not_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'big').write_bytes(b'x'*1000);(root/'env').mkdir();(root/'env/a').write_bytes(b'ab');(root/'env/link').symlink_to(root/'big')
            self.assertEqual(measure.tree_bytes(root/'env')['apparent_regular_bytes'],2)

    def test_second_launch_failure_reaps_owned_process_group(self):
        with tempfile.TemporaryDirectory() as tmp:
            proc=FakeProcess();folder=Path(tmp)/'batch'
            with mock.patch.object(measure.subprocess,'Popen',side_effect=[proc,OSError('injected launch error')]) as launch, mock.patch.object(measure.os,'killpg',side_effect=[None,ProcessLookupError()]) as kill, mock.patch.object(measure,'rss',return_value=0):
                with self.assertRaisesRegex(OSError,'injected'):measure.batch(folder,Path('/fake/python'),2)
            self.assertTrue(proc.waited);self.assertEqual(kill.call_args_list[0],mock.call(proc.pid,measure.signal.SIGTERM))
            self.assertTrue(all(call.kwargs['start_new_session'] for call in launch.call_args_list))
            self.assertEqual(json.loads((folder/'batch-receipt.json').read_text())['status'],'failed')

    def test_already_exited_leader_lingering_group_gets_term_then_kill(self):
        proc=FakeProcess();proc.returncode=1
        with mock.patch.object(measure.os,'killpg') as kill, mock.patch.object(measure.time,'monotonic',side_effect=[0,6]):
            measure.reap(proc)
        self.assertEqual(kill.call_args_list,[mock.call(proc.pid,measure.signal.SIGTERM),mock.call(proc.pid,0),mock.call(proc.pid,measure.signal.SIGKILL)])

    def test_private_installer_home_config_tmp(self):
        with tempfile.TemporaryDirectory() as tmp:
            one=measure.clean_env(Path(tmp)/'one');two=measure.clean_env(Path(tmp)/'two')
            for name in ('HOME','TMPDIR','XDG_CONFIG_HOME','XDG_CACHE_HOME'):
                self.assertNotEqual(one[name],two[name]);self.assertTrue(Path(one[name]).is_dir())

    def test_installer_launch_error_is_saved_without_network_retry(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(measure,'ROOT',Path(tmp)), mock.patch.object(measure,'identities',return_value={'fixed':True}), mock.patch.object(measure.subprocess,'check_output',return_value='uv 0.13.4'), mock.patch.object(measure.subprocess,'run',side_effect=OSError('injected spawn error')) as run:
            with self.assertRaisesRegex(RuntimeError,'venv creation failed'):measure.install()
            result=json.loads((Path(tmp)/'install-results.json').read_text())
            self.assertEqual(len(result['records']),1)
            self.assertEqual(result['records'][0]['venv_create_exit_code'],'launch_error')
            self.assertEqual(result['records'][0]['attempted_child_execution'],'known_not_started')
            self.assertEqual(run.call_count,1)

    def test_uv_launch_error_is_saved_as_nonexecuted(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(measure,'ROOT',Path(tmp)), mock.patch.object(measure,'identities',return_value={'fixed':True}), mock.patch.object(measure.subprocess,'check_output',return_value='uv 0.13.4'), mock.patch.object(measure.subprocess,'run',side_effect=[SimpleNamespace(returncode=0),OSError('injected uv spawn error')]) as run:
            with self.assertRaisesRegex(RuntimeError,'registry install failed'):measure.install()
            record=json.loads((Path(tmp)/'install-results.json').read_text())['records'][0]
            self.assertEqual(record['exit_code'],'launch_error');self.assertEqual(record['stage'],'failed')
            self.assertEqual(record['attempted_child_execution'],'known_not_started');self.assertEqual(run.call_count,2)

    def test_failed_batch_retained_in_aggregate_denominator(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);installed={'records':[{'trial':3,'profile':p,'exit_code':0,'installed_packages':[['gradio','6.30.0']]} for p in ('full','gradio')]}
            (root/'install-results.json').write_text(json.dumps(installed))
            def fail(folder,*args,**kwargs):
                folder.mkdir();(folder/'batch-receipt.json').write_text(json.dumps({'status':'failed','error':'injected'}));raise RuntimeError('injected batch')
            with mock.patch.object(measure,'ROOT',root),mock.patch.object(measure,'identities',return_value={'fixed':True}),mock.patch.object(measure,'batch',side_effect=fail):
                with self.assertRaisesRegex(RuntimeError,'injected'):measure.workloads(3)
            results=json.loads((root/'workload-results.json').read_text())
            self.assertEqual(len(results['batches']),1);self.assertEqual(results['batches'][0]['status'],'failed')
            self.assertEqual(results['batches'][0]['profile'],'full');self.assertFalse(results['postrun_identity_verified'])


if __name__=='__main__':unittest.main()
