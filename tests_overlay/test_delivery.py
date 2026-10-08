from __future__ import annotations
import importlib.util
from pathlib import Path
import plistlib
import subprocess
import tempfile
import unittest
ROOT=Path(__file__).resolve().parent.parent

def mod(name):
    spec=importlib.util.spec_from_file_location(name,ROOT/'scripts'/f'{name}.py')
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

class DeliveryTests(unittest.TestCase):
    def test_overlay_doctor_requires_no_bot_send_command(self):
        from unittest.mock import patch
        from jev_feishu.__main__ import doctor
        from jev_feishu.config import Config
        from subprocess import CompletedProcess
        with patch('jev_feishu.__main__.LarkCLI') as cli, patch('jev_feishu.__main__.subprocess.run') as run:
            cli.return_value.executable.return_value='lark-cli'
            run.return_value=CompletedProcess([],0,'--as --params --data','')
            doctor(Config(base=ROOT),False,overlay=True)
            self.assertEqual(run.call_count,1)
            self.assertEqual(run.call_args.args[0][1], 'api')
    def test_launchd_never_restarts_on_quit(self):
        p=mod('launchd_overlay').make_plist(Path('/tmp/测试 dir'),Path('/tmp/home'))
        self.assertFalse(p['KeepAlive']);self.assertTrue(p['RunAtLoad'])
        self.assertEqual(p['LimitLoadToSessionType'],'Aqua')
    def test_launchd_only_overlay_not_legacy_bot(self):
        p=mod('launchd_overlay').make_plist(Path('/tmp/测试 dir'),Path('/tmp/home'))
        self.assertEqual(p['ProgramArguments'][-1],'overlay')
        self.assertNotIn('run',p['ProgramArguments']);self.assertNotIn('bind',p['ProgramArguments'])
    def test_launchd_no_secrets_and_valid_plist(self):
        p=mod('launchd_overlay').make_plist(ROOT,Path.home())
        b=plistlib.dumps(p);self.assertEqual(plistlib.loads(b),p)
        self.assertNotIn(b'JEV_API_KEY',b);self.assertNotIn(b'LLM_API_KEY',b)
    def test_app_launcher_quotes_paths_and_valid_shell(self):
        with tempfile.TemporaryDirectory(prefix="Jev 空格 ' ") as tmp:
            root=Path(tmp);app=mod('make_app').build(root)
            cp=subprocess.run(['zsh','-n',str(app/'Contents/Resources/launcher.zsh')],capture_output=True)
            self.assertEqual(cp.returncode,0)
            self.assertIn(b'Mach-O',subprocess.check_output(['file',str(app/'Contents/MacOS/launch')]))
            info=plistlib.loads((app/'Contents/Info.plist').read_bytes())
            from jev_feishu import __version__
            self.assertTrue(info['LSUIElement']);self.assertEqual(info['CFBundleVersion'],__version__)
    def test_app_launcher_no_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);m=mod('make_app');m.build(root)
            with self.assertRaises(FileExistsError):m.build(root)
    def test_bootstrap_offline_no_package_or_account_mutation(self):
        text=(ROOT/'scripts/bootstrap_overlay.sh').read_text()
        for term in ['pip install','auth login','config init','bind --send','launchctl','sudo ']:self.assertNotIn(term,text)
        self.assertIn('[ -e "$dest" ] || cp',text)
    def test_launchers_are_shell_valid(self):
        for name in ['启动悬浮窗.command','离线演示.command','scripts/bootstrap_overlay.sh','scripts/build_input_helper.sh']:
            with self.subTest(name=name):
                cp=subprocess.run(['bash','-n',str(ROOT/name)],capture_output=True)
                self.assertEqual(cp.returncode,0)
    def test_sources_locked_overlay_version(self):
        import json
        data=json.loads((ROOT/'sources.lock.json').read_text())
        from jev_feishu import __version__
        self.assertEqual(data['version'],__version__);self.assertEqual(data['project'],'jev-feishu-overlay')

if __name__=='__main__':unittest.main()
