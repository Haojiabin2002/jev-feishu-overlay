"""User-requested local analysis history; separate from the metadata-only quota DB."""
from __future__ import annotations
from dataclasses import asdict,dataclass
from datetime import datetime
import hashlib
import json
import os
import re
import sqlite3
import time
import uuid

from ..models import AnalysisResult


def anchor_normal(text: str) -> str:
    text=re.sub(r'@[^\s，。,：:；;！？!?]+\s*','',text)
    return ''.join(c for c in text if not c.isspace() and c not in '\u200b\u200c\u200d\ufeff')


def anchor_hash(text: str) -> str:
    return hashlib.sha256(anchor_normal(text).encode()).hexdigest()


def execution_states(result: AnalysisResult) -> dict:
    states={'capture':'done'}
    for stage in ('judgement','candidates','ranking'):
        states[stage]='failed' if stage in result.errors else 'done' if getattr(result,stage) else 'cancelled' if 'cancelled' in result.errors else 'skipped'
    return states


@dataclass(frozen=True)
class HistoryRecord:
    id: str
    alias: str
    created: float
    message_key: str
    anchor_hash: str
    preview: str
    result: AnalysisResult
    states: dict

    @property
    def time_label(self):
        return datetime.fromtimestamp(self.created).strftime('%m-%d %H:%M:%S')


class HistoryStore:
    def __init__(self,cfg):
        self.cfg=cfg
        cfg.state_dir.mkdir(parents=True,exist_ok=True,mode=0o700)
        os.chmod(cfg.state_dir,0o700)
        self.path=cfg.state_dir/'analysis-history.sqlite3'
        self.db=sqlite3.connect(self.path,timeout=3)
        os.chmod(self.path,0o600)
        self.db.execute('''CREATE TABLE IF NOT EXISTS analyses (
            id TEXT PRIMARY KEY, source_key TEXT NOT NULL, alias TEXT NOT NULL,
            created REAL NOT NULL, signature TEXT NOT NULL, message_key TEXT NOT NULL,
            anchor_hash TEXT NOT NULL, preview TEXT NOT NULL, result TEXT NOT NULL, states TEXT NOT NULL)''')
        self.db.execute('CREATE INDEX IF NOT EXISTS analysis_source_time ON analyses(source_key,created DESC)')
        self.db.commit()

    def __enter__(self):return self
    def __exit__(self,*_):self.db.close()

    def _key(self,alias):
        source=self.cfg.source(alias)
        return hashlib.sha256((self.cfg.owner_open_id+'\0'+source.chat_id+'\0'+source.user_id).encode()).hexdigest()

    def save(self,alias,signature,result,*,message_id='',anchor_text=''):
        if result.demo:raise ValueError('Demo results must not enter real conversation history')
        source_key=self._key(alias);record_id=uuid.uuid4().hex
        chat=result.state.get('chat',{})
        focus=next((m for m in chat.get('messages',[]) if m['id']==chat.get('focus_message_id')),None)
        preview=' '.join((focus or {}).get('text','').split())[:60]
        message_key=hashlib.sha256((source_key+'\0'+message_id).encode()).hexdigest() if message_id else ''
        digest=anchor_hash(anchor_text) if len(anchor_normal(anchor_text))>=6 else ''
        with self.db:
            self.db.execute('INSERT INTO analyses VALUES (?,?,?,?,?,?,?,?,?,?)',
                (record_id,source_key,alias,time.time(),signature,message_key,digest,preview,
                 json.dumps(asdict(result),ensure_ascii=False),json.dumps(execution_states(result))))
        return record_id

    def list(self,alias,limit=30,offset=0):
        rows=self.db.execute('SELECT id,created,message_key,anchor_hash,preview FROM analyses WHERE source_key=? ORDER BY created DESC LIMIT ? OFFSET ?',
                             (self._key(alias),max(1,min(100,int(limit))),max(0,int(offset)))).fetchall()
        return [dict(zip(('id','created','message_key','anchor_hash','preview'),row)) for row in rows]

    def get(self,alias,record_id):
        row=self.db.execute('SELECT id,alias,created,message_key,anchor_hash,preview,result,states FROM analyses WHERE source_key=? AND id=?',
                            (self._key(alias),record_id)).fetchone()
        if row is None:return None
        result=AnalysisResult(**json.loads(row[6]))
        return HistoryRecord(*row[:6],result,json.loads(row[7]))
