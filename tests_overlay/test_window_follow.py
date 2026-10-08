import unittest
from dataclasses import replace
from pathlib import Path
from jev_feishu.overlay.window_follow import anchor_position, valid_bounds, WindowFollower
from jev_feishu.overlay.settings import OverlaySettings
from jev_feishu.errors import ConfigError


class WindowFollowTests(unittest.TestCase):
    def test_moving_chat_moves_panel_by_same_delta(self):
        b={'x':200,'y':100,'width':1000,'height':750}
        p=anchor_position(b,(440,740),(1800,1000))
        q=anchor_position({**b,'x':250,'y':130},(440,740),(1800,1000))
        self.assertEqual((q[0]-p[0],q[1]-p[1]),(50,30))

    def test_manual_offset_is_preserved_on_move(self):
        self.assertEqual(anchor_position({'x':200,'y':100,'width':1000,'height':750},(440,740),(1800,1000),(25,30)),(225,130))

    def test_panel_stays_on_screen_at_edges(self):
        p=anchor_position({'x':1400,'y':850,'width':700,'height':750},(440,740),(1470,956))
        self.assertLessEqual(p[0]+440,1470)
        self.assertLessEqual(p[1]+740,956)

    def test_invalid_geometry_is_rejected(self):
        for bad in ({}, {'x':False,'y':0,'width':700,'height':750}, {'x':float('nan'),'y':0,'width':700,'height':750}):
            self.assertFalse(valid_bounds(bad))

    def test_follow_setting_is_boolean_and_bundle_explicit(self):
        with self.assertRaises(ConfigError):replace(OverlaySettings(),follow_window='yes').validate()
        with self.assertRaises(ConfigError):replace(OverlaySettings(),follow_window=True,follow_bundle_id='*').validate()

    def test_unavailable_window_never_keeps_old_coordinates(self):
        f=WindowFollower(Path('/tmp'),OverlaySettings())
        f.events.put({'status':'window','x':100,'y':100,'width':800,'height':700})
        self.assertIsNotNone(f.poll())
        f.events.put({'status':'unavailable','reason':'accessibility'})
        self.assertIsNone(f.poll());self.assertIn('辅助功能权限',f.status)
