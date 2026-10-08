import unittest
from types import SimpleNamespace
from unittest.mock import patch
import AppKit
from jev_feishu.overlay.message_annotations import MessageAnnotations, validated_anchors

class AnnotationGuards(unittest.TestCase):
    def payload(self,**changes):
        return dict({'status':'matched','chat_verified':True,'foreground':True,'timed_out':False,'occlusion_checked':True,
          'anchors':[{'key':'record','x':100,'y':300,'width':300,'height':22,'unoccluded':True}]},**changes)

    def test_only_verified_uncovered_chat_can_show_annotations(self):
        self.assertEqual(len(validated_anchors(self.payload(),{'record'})),1)
        for field in ('chat_verified','occlusion_checked'):
            self.assertEqual(validated_anchors(self.payload(**{field:False}),{'record'}),[])

    def test_side_by_side_chat_is_allowed_but_covered_annotation_is_not(self):
        self.assertEqual(len(validated_anchors(self.payload(foreground=False),{'record'})),1)
        p=self.payload();p['anchors'][0]['unoccluded']=False
        self.assertEqual(validated_anchors(p,{'record'}),[])

    def test_late_incomplete_snapshot_is_not_rendered(self):
        self.assertEqual(validated_anchors(self.payload(timed_out=True),{'record'}),[])

    def test_unknown_record_and_invalid_geometry_are_rejected(self):
        self.assertEqual(validated_anchors(self.payload(),{'another-source-record'}),[])
        for value in (float('nan'),float('inf'),True,'300'):
            p=self.payload();p['anchors'][0]['x']=value
            self.assertEqual(validated_anchors(p,{'record'}),[])


class InputIsolationTests(unittest.TestCase):
    def setUp(self):
        self.registration=[]
        def register(mask,handler):
            self.registration.append((mask,handler));return object()
        self.monitor=patch.object(AppKit,'NSEvent',SimpleNamespace(addGlobalMonitorForEventsMatchingMask_handler_=register))
        self.monitor.start();self.addCleanup(self.monitor.stop)
        self.c=SimpleNamespace(alias='group',_paused=False,cfg=SimpleNamespace())
        self.manager=MessageAnnotations(self.c)
        self.manager.alias='group';self.manager.front_pid=42
        workspace=SimpleNamespace(frontmostApplication=lambda:SimpleNamespace(processIdentifier=lambda:42))
        self.workspace=patch.object(AppKit,'NSWorkspace',SimpleNamespace(sharedWorkspace=lambda:workspace))
        self.workspace.start();self.addCleanup(self.workspace.stop)

    def dispatch(self,event_mask):
        for mask,handler in self.registration:
            if mask & event_mask:handler(object())

    def test_other_app_keyboard_events_never_reach_annotation_queue(self):
        self.dispatch(AppKit.NSEventMaskKeyDown)
        self.dispatch(AppKit.NSEventMaskKeyUp)
        self.dispatch(AppKit.NSEventMaskFlagsChanged)
        self.assertTrue(self.manager.events.empty())
        self.dispatch(AppKit.NSEventMaskScrollWheel)
        self.assertFalse(self.manager.events.empty())

    def test_focused_editor_suspends_scanning_until_pointer_navigation(self):
        self.manager.busy=True
        self.manager.events.put(('result',(0,'group',[],{'reason':'editing'})))
        with patch('jev_feishu.overlay.message_annotations.HistoryStore') as history, \
             patch.object(self.manager,'_show',return_value=0),patch('jev_feishu.overlay.message_annotations.time.monotonic',return_value=100):
            self.manager.tick();self.manager.tick()
            history.assert_not_called()
            self.assertEqual(self.manager.last_reason,'editing')
            self.dispatch(AppKit.NSEventMaskLeftMouseDown)
            self.manager.tick()
            history.assert_not_called()  # navigation is debounced
        with patch('jev_feishu.overlay.message_annotations.HistoryStore') as history, \
             patch('jev_feishu.overlay.message_annotations.time.monotonic',return_value=101):
            history.return_value.__enter__.return_value.list.return_value=[]
            self.manager.tick()
            history.assert_called_once()
