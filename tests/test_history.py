from dataclasses import replace,asdict
import json
from pathlib import Path
import tempfile
import unittest

from jev_feishu.config import Config,Source
from jev_feishu.overlay.engine import demo_result
from jev_feishu.overlay.history import HistoryStore,anchor_hash


class HistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.cfg=Config(base=Path(self.tmp.name),owner_open_id='ou_owner',sources=(
            Source('群A',chat_id='oc_a_test',enabled=True),Source('群B',chat_id='oc_b_test',enabled=True)))
        self.result=replace(demo_result(self.cfg.base),demo=False)

    def save(self,store):
        return store.save('群A','snapshot',self.result,message_id='om_local_anchor',anchor_text='原消息在这里，别覆盖它。')

    def test_restart_retains_original_message_replies_and_eight_decisions(self):
        with HistoryStore(self.cfg) as store:record_id=self.save(store)
        with HistoryStore(self.cfg) as store:
            record=store.get('群A',record_id)
            # JSON criterion-map keys are strings; compare the full JSON data model.
            self.assertEqual(asdict(record.result),json.loads(json.dumps(asdict(self.result))))
            self.assertEqual(len(record.result.judgement)+len(record.result.ranking),8)
            self.assertEqual(record.states['capture'],'done')
            self.assertEqual(record.states['ranking'],'done')

    def test_record_cannot_be_loaded_from_another_source(self):
        with HistoryStore(self.cfg) as store:
            record_id=self.save(store)
            self.assertIsNone(store.get('群B',record_id))
            self.assertEqual(store.list('群B'),[])

    def test_reanalysis_appends_instead_of_replacing_previous_record(self):
        with HistoryStore(self.cfg) as store:
            first=self.save(store);second=self.save(store)
            self.assertNotEqual(first,second)
            self.assertEqual(len(store.list('群A')),2)

    def test_saved_result_is_independent_of_later_live_mutation(self):
        original=self.result.candidates[0]
        with HistoryStore(self.cfg) as store:
            record_id=self.save(store);self.result.candidates[0]='后续新稿'
            self.assertEqual(store.get('群A',record_id).result.candidates[0],original)

    def test_file_private_and_only_anchor_hash_is_stored(self):
        with HistoryStore(self.cfg) as store:self.save(store)
        p=self.cfg.state_dir/'analysis-history.sqlite3'
        self.assertEqual(p.stat().st_mode & 0o777,0o600)
        self.assertNotIn('原消息在这里，别覆盖它。'.encode(),p.read_bytes())
        self.assertNotIn(b'om_local_anchor',p.read_bytes())

    def test_anchor_normalization_preserves_text_and_ignores_mention_labels(self):
        self.assertEqual(anchor_hash('@_user_1  请核对这几条记录。'),anchor_hash('@李同事 请核对这几条记录。'))
        self.assertNotEqual(anchor_hash('请核对这几条记录。'),anchor_hash('请删除这几条记录。'))
