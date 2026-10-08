from pathlib import Path
from dataclasses import replace
import os
import json
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from jev_feishu.config import Config
from jev_feishu.models import ModelClient
from jev_feishu.errors import ConfigError, ProviderError
from jev_feishu.overlay.ux import readiness


class CodexBackendTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cfg = Config(base=Path(self.tmp.name), allow_cloud=True,
                          llm_backend='codex_sdk', codex_binary='/existing/codex', node_binary='/existing/node')
        for name in ('codex','node'):
            binary=Path(self.tmp.name)/name
            binary.write_text('#!/bin/sh\nexit 0\n');binary.chmod(0o700)
        self.cfg=replace(self.cfg,codex_binary=str(Path(self.tmp.name)/'codex'),node_binary=str(Path(self.tmp.name)/'node'))

    def test_missing_codex_path_is_reported_before_spawning_sdk(self):
        cfg=replace(self.cfg,codex_binary=str(Path(self.tmp.name)/'removed-codex'))
        with patch('jev_feishu.codex_backend.run_process') as run:
            with self.assertRaisesRegex(ProviderError,'Codex.*不存在'):
                ModelClient(cfg).generate({})
            run.assert_not_called()

    def test_nonexecutable_node_is_reported_before_spawning_sdk(self):
        Path(self.cfg.node_binary).chmod(0o600)
        with patch('jev_feishu.codex_backend.run_process') as run:
            with self.assertRaisesRegex(ProviderError,'Node.*不可执行'):
                ModelClient(self.cfg).generate({})
            run.assert_not_called()

    def test_codex_needs_only_jev_key(self):
        with patch.dict(os.environ, {'JEV_API_KEY': 'synthetic'}, clear=True):
            self.cfg.check_models()
            self.assertFalse(any('LLM' in item for item in readiness(self.cfg)))

    def test_unknown_backend_rejected(self):
        with self.assertRaises(ConfigError):
            replace(self.cfg, llm_backend='silent_fallback').validate()

    def test_sdk_success_uses_stdin_and_does_not_inherit_jev_secret(self):
        expected=['我先核对一下进度。','可以先明确演示范围吗？','确认时间后我再同步。']
        def run(argv, **kw):
            self.assertNotIn('private source text', ' '.join(argv))
            body=json.loads(kw['input'])
            self.assertEqual(body['state']['message'], 'private source text')
            self.assertNotIn('JEV_API_KEY', kw['env'])
            self.assertNotIn('LLM_API_KEY', kw['env'])
            self.assertNotIn('UNRELATED_SECRET', kw['env'])
            self.assertFalse(kw.get('shell', False))
            return subprocess.CompletedProcess(argv,0,json.dumps({'replies':expected,'usage':{'input_tokens':4}}),'')
        with patch.dict(os.environ, {'JEV_API_KEY':'never-copy','LLM_API_KEY':'never-copy','UNRELATED_SECRET':'never-copy'}), patch('jev_feishu.codex_backend.run_process',side_effect=run):
            replies,usage=ModelClient(self.cfg).generate({'message':'private source text'})
        self.assertEqual(replies,expected)
        self.assertEqual(usage,{'input_tokens':4})

    def test_sdk_failure_is_sanitized_without_http_fallback(self):
        with patch('jev_feishu.codex_backend.run_process',return_value=subprocess.CompletedProcess([],1,'SECRET','SECRET')), patch('jev_feishu.models.JsonTransport.post') as post:
            with self.assertRaises(ProviderError) as error:ModelClient(self.cfg).generate({})
        self.assertNotIn('SECRET',str(error.exception));post.assert_not_called()

    def test_sdk_timeout_is_not_retried(self):
        with patch('jev_feishu.codex_backend.run_process',side_effect=subprocess.TimeoutExpired([],1)) as run:
            with self.assertRaises(ProviderError):ModelClient(self.cfg).generate({})
        self.assertEqual(run.call_count,1)

    def test_http_mode_still_requires_llm_key(self):
        with patch.dict(os.environ,{'JEV_API_KEY':'synthetic'},clear=True):
            with self.assertRaises(ConfigError):replace(self.cfg,llm_backend='http').check_models()
