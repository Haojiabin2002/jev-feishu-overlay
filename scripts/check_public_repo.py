#!/usr/bin/env python3
"""Check tracked files for private runtime artifacts; never print matched values.

This is a publication boundary check, not a replacement for a full secret scan.
"""
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
PRIVATE_DIRS={'.state','.venv','node_modules','dist','__pycache__'}
PRIVATE_FILES={'.env','config.toml','overlay.toml','.DS_Store'}
IMAGE_ALLOWLIST={f'assets/offscreen-{name}.png' for name in ('demo','edit','details')}
RULES={
    'private-home-path':re.compile(rb'/(?:Users|home)/[A-Za-z0-9_.-]+/'),
    'mac-temp-path':re.compile(rb'/var/folders/[A-Za-z0-9]+/'),
    'real-shaped-feishu-id':re.compile(rb'\b(?:ou|oc|om)_[a-f0-9]{24,}\b'),
    'credential-token':re.compile(rb'\b(?:ghp_|github_pat_|apikey_)[A-Za-z0-9_]{20,}'),
    'private-key':re.compile(rb'-----BEGIN (?:OPENSSH |RSA |EC |DSA )?PRIVATE KEY-----'),
}

def main():
    proc=subprocess.run(['git','ls-files','-z'],cwd=ROOT,capture_output=True,check=True)
    names=[n.decode() for n in proc.stdout.split(b'\0') if n]
    if not names:raise SystemExit('No tracked files to audit; stage the intended public files first.')
    findings=[]
    for name in names:
        rel=PurePosixPath(name);path=ROOT/name
        private=(bool(PRIVATE_DIRS.intersection(rel.parts)) or rel.name in PRIVATE_FILES
                 or (rel.name.startswith('.env.') and rel.name!='.env.example')
                 or '.local.' in rel.name or rel.suffix in {'.log','.jsonl','.db','.sqlite','.sqlite3','.zip','.backup'}
                 or name.startswith(('docs/tests/','docs/legacy/'))
                 or (rel.suffix.lower() in {'.png','.jpg','.jpeg','.webp','.gif'} and name not in IMAGE_ALLOWLIST))
        if private:findings.append((name,0,'private-artifact'))
        if path.is_symlink():
            findings.append((name,0,'symlink'));continue
        data=path.read_bytes()
        for label,pattern in RULES.items():
            for match in pattern.finditer(data):
                findings.append((name,data[:match.start()].count(b'\n')+1,label))
    if findings:
        for name,line,label in findings:print(f'{name}:{line}: {label}')
        return 1
    print(f'Publication boundary check passed: {len(names)} tracked files; no matched values printed.')
    return 0

if __name__=='__main__':sys.exit(main())
