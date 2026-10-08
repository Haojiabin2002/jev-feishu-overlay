# Derived UI-only extract of jev-chat/jev-chat-jarvis-mac, MIT; see LICENSE.
# No upstream config, credentials, OCR, model loading or input-writing modules are imported.
from __future__ import annotations
import objc
import AppKit
from types import SimpleNamespace
from AppKit import (
    NSAppearance,
    NSAttributedString,
    NSBackingStoreBuffered,
    NSBackgroundColorAttributeName,
    NSBezierPath,
    NSBezelStyleRounded,
    NSButton,
    NSColor,
    NSFont,
    NSFontAttributeName,
    NSForegroundColorAttributeName,
    NSPanel,
    NSPasteboard,
    NSPasteboardTypeString,
    NSPopUpButton,
    NSScreen,
    NSTextField,
    NSView,
    NSWindowMiniaturizeButton,
    NSWindowStyleMaskBorderless,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskNonactivatingPanel,
    NSWindowStyleMaskTitled,
    NSWindowZoomButton,
    NSWindowCloseButton,
)

from Foundation import NSMakeRect, NSMakeSize, NSObject, NSTimer

PANEL_W, PANEL_H = 440, 680

COLLAPSED_H = 108              # height when the panel is rolled up

FAST_TICK = 0.25         # re-check cadence while the chat pane is quiet

BURST_TICK = 0.45        # short cadence right after a change: catch the burst's next message

BURST_READS = 3          # how many reads stay on BURST_TICK before falling back to SLOW_TICK

SLOW_TICK = 1.0          # re-check cadence while the chat pane keeps moving

SETTLE_S = 1.2           # upper bound on the settle wait (anti-flood; unchanged by design)

EARLY_SETTLE_S = 0.70    # the gate may open this early …

STABLE_READS = 3         # … but only after this many consecutive unchanged reads

MIN_GAP_S = 2.0          # never restart analysis faster than this

CONTEXT_TURNS = 4        # recent turns the generation half sees

JUDGE_TURNS = 2          # recent turns the judge half sees: shorter prompt, faster forward

def _rgb(hex_code: int, alpha: float = 1.0) -> NSColor:
    return NSColor.colorWithCalibratedRed_green_blue_alpha_(
        ((hex_code >> 16) & 0xFF) / 255.0,
        ((hex_code >> 8) & 0xFF) / 255.0,
        (hex_code & 0xFF) / 255.0,
        alpha,
    )

PALETTE = {
    "bg": _rgb(0xF7F7F7),     # panel surface
    "text": _rgb(0x191919),   # judged message, intent, candidates, action advice
    "muted": _rgb(0x888888),  # status, sender/context, confidence, percentages, headers
    "green": _rgb(0x07C160),  # WeChat brand green — risk 安全, success feedback
    "amber": _rgb(0xFA9D3B),  # risk 留神
    "red": _rgb(0xFA5151),    # risk 危险, failures
    # The 话术 dropdown is drawn as a WeChat-style field: a flat light surface with a
    # hairline, because the stock popup bezel brings the system accent colour (a blue
    # chevron) into a panel that has no other system-accent pixel in it.
    # A green-tinted variant of this field was tried to advertise clickability and
    # rejected: next to the all-grey panel it read as a selection state and was jarring.
    # Discoverability is handled by the popup's tooltip instead — zero visual footprint.
    "field": _rgb(0xF2F2F2),
    "edge": _rgb(0xE3E3E3),
}

CAND_BTN_W, CAND_BTN_H, CAND_BTN_GAP = 56, 24, 4

CAND_BTN_X = PANEL_W - 14 - (2 * CAND_BTN_W + CAND_BTN_GAP)   # 230

CAND_PROB_X, CAND_PROB_W = 14, 72                              # 14 .. 86

CAND_TEXT_X = CAND_PROB_X + CAND_PROB_W + 8                    # 94

CAND_TEXT_W = CAND_BTN_X - CAND_TEXT_X - 8                     # 128

CAND_TEXT_H = 64

CAND_ROW_H = 76

TONE_DD_X, TONE_DD_W, TONE_DD_H, TONE_DD_GAP = 14, PANEL_W - 28, 24, 6

TONE_DD_INSET = 6         # the popup sits this far inside its field, like text in an input box

TONE_DD_FONT = 13         # bigger than the 11 pt labels: it is a control, and it is the one

GROUP_GAP = 12            # between one group's rows and the next group's dropdown

BOTTOM_PAD = 18           # below the last group

styles = SimpleNamespace(MAX_SLOTS=1, PER_TONE=3, NONE_LABEL='不用', labels=lambda:['候选回复'])

class HudController(NSObject):
    @objc.python_method
    def _build_panel(self):
        # Closable/Miniaturizable are what actually CREATE the standard window buttons;
        # NonactivatingPanel alone gives a title bar with no controls at all.
        style = (NSWindowStyleMaskTitled | NSWindowStyleMaskClosable
                 | NSWindowStyleMaskMiniaturizable | NSWindowStyleMaskNonactivatingPanel)
        self.panel = NSPanel.alloc().initWithContentRect_styleMask_backing_defer_(
            NSMakeRect(0, 0, PANEL_W, PANEL_H), style, NSBackingStoreBuffered, False)
        self.panel.setLevel_(AppKit.NSFloatingWindowLevel)
        self.panel.setOpaque_(False)
        self.panel.setAlphaValue_(1.0)   # light surfaces go grey/washed out below 1.0
        # The title bar and button bezels are drawn from the appearance, not from the
        # background colour, so pin Aqua: a dark-mode system would otherwise give a dark
        # title bar above a white panel.
        self.panel.setAppearance_(NSAppearance.appearanceNamed_(AppKit.NSAppearanceNameAqua))
        self.panel.setBackgroundColor_(PALETTE["bg"])
        self.panel.setTitle_("jev-jarvis")
        self.panel.setHidesOnDeactivate_(False)
        self.panel.setBecomesKeyOnlyIfNeeded_(True)

        view = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, PANEL_W, PANEL_H))
        # Paint the panel colour on the view itself rather than leaning on the window's
        # background colour: _relayout() grows and shrinks this view, and a region that
        # appears after a resize is not reliably covered by the window behind it. It also
        # makes offscreen renders (cacheDisplayInRect_) show what the screen shows — a
        # transparent view renders black there and hides real layout problems.
        view.setWantsLayer_(True)
        view.layer().setBackgroundColor_(PALETTE["bg"].CGColor())
        self.rows: dict[str, NSTextField] = {}

        # Layout order matters: the message being judged is the anchor of the panel,
        # so it sits right under the title in the brightest, largest type.
        # The full-width rows (PANEL_W - 28 = 332 px) cannot clip their widest string:
        # "意图识别率 100%" measures 103 px at 12 pt.
        # Every control is created once and then placed by _relayout(), which is what lets
        # the panel change height when the tone selection changes.
        dy = 30
        for key, size, color, bold, height in (
            ("chat", 12, PALETTE["green"], True, 18),      # 群名 / 联系人
            ("status", 10, PALETTE["muted"], False, 14),
            ("message", 15, PALETTE["text"], False, 50),      # the message under analysis
            ("sender", 10, PALETTE["muted"], False, 14),
            ("intent", 21, PALETTE["text"], True, 28),
            ("confidence", 12, PALETTE["muted"], False, 18),
            ("risk", 14, PALETTE["green"], True, 20),
            ("actions", 13, PALETTE["text"], False, 18),
        ):
            tf = self._make_label(14, 0, PANEL_W - 28, height,
                                  size=size, color=color, bold=bold)
            if key == "message":
                tf.cell().setWraps_(True)
            view.addSubview_(tf)
            self.rows[key] = tf
            self._fixed.append((tf, 14, dy, PANEL_W - 28, height))
            dy += height + 8

        # ---- candidates section
        dy += 6
        header = self._make_label(14, 0, PANEL_W - 28, 16,
                                  size=11, color=PALETTE["muted"])
        header.setStringValue_("候选回复（按合适度排序）")
        view.addSubview_(header)
        self.rows["cand_header"] = header
        self._fixed.append((header, 14, dy, PANEL_W - 28, 16))
        dy += 22
        self._group_top = dy

        # ---- 话术 groups: each dropdown heads a group and its candidates sit underneath,
        # so the tone is labelled by the thing that selects it. Every group's controls exist
        # from the start; _relayout() decides which are on screen. The button tags are slot
        # arithmetic (slot * PER_TONE + row) so they never shift when a group's results are
        # still in flight.
        tone_items = styles.labels() + [styles.NONE_LABEL]
        for slot in range(styles.MAX_SLOTS):
            # the field the popup sits in: a flat surface with a hairline, drawn by us so
            # the control carries no system-accent chrome
            box = NSView.alloc().initWithFrame_(NSMakeRect(0, 0, TONE_DD_W, TONE_DD_H))
            box.setWantsLayer_(True)
            box.layer().setBackgroundColor_(PALETTE["field"].CGColor())
            box.layer().setBorderColor_(PALETTE["edge"].CGColor())
            box.layer().setBorderWidth_(1.0)
            box.layer().setCornerRadius_(5.0)
            view.addSubview_(box)
            self._dd_boxes.append(box)

            pop = NSPopUpButton.alloc().initWithFrame_pullsDown_(
                NSMakeRect(0, 0, TONE_DD_W - 2 * TONE_DD_INSET, TONE_DD_H), False)
            pop.setBordered_(False)          # <- no bezel, no accent-coloured chevron
            # the one discoverability aid the flat field gets: grey-on-grey reads as text,
            # a tooltip costs nothing visually and answers "can I click this?"
            pop.setToolTip_("点这里换话术（每种一组，各出 2 条）")
            pop.setFont_(NSFont.systemFontOfSize_(TONE_DD_FONT))
            pop.addItemsWithTitles_(tone_items)
            pop.selectItemWithTitle_(self.slot_tones[slot])
            pop.setTarget_(self)
            pop.setAction_("toneChanged:")
            view.addSubview_(pop)
            self._dds.append(pop)

            slot_rows = []
            for row in range(styles.PER_TONE):
                tag = slot * styles.PER_TONE + row
                prob = self._make_label(CAND_PROB_X, 0, CAND_PROB_W, 14,
                                        size=11, color=PALETTE["muted"])
                text = self._make_label(CAND_TEXT_X, 0, CAND_TEXT_W, CAND_TEXT_H,
                                        size=12, color=PALETTE["text"])
                text.cell().setWraps_(True)
                copy_btn = self._make_button(CAND_BTN_X, 0, CAND_BTN_W, CAND_BTN_H,
                                             "复制", "copyCandidate:", tag)
                fill_btn = self._make_button(CAND_BTN_X + CAND_BTN_W + CAND_BTN_GAP, 0,
                                             CAND_BTN_W, CAND_BTN_H, "填入", "fillCandidate:", tag)
                for c in (prob, text, copy_btn, fill_btn):
                    view.addSubview_(c)
                slot_rows.append({"prob": prob, "text": text,
                                  "btn": copy_btn, "fill_btn": fill_btn})
            self._rows.append(slot_rows)

        self.panel.setContentView_(view)
        self._title_h = self.panel.frame().size.height - PANEL_H   # measured, not assumed
        self._relayout()
        self.rows["status"].setStringValue_("等待微信消息…")
        self._wire_window_controls()
        self._install_status_item()


    @objc.python_method
    def _slot_active(self, slot: int) -> bool:
        return self.slot_tones[slot] in styles.PRESETS


    @objc.python_method
    def _relayout(self):
        """Place every control for the current tone selection and size the panel to fit.

        Two things are computed here rather than at build time. Positions are measured from
        the TOP, so when the panel grows or shrinks nothing above the change moves — only the
        bottom edge does. And the height follows the groups in use: a slot on 不用 reserves
        neither a dropdown's worth of rows nor its candidates, which is what removes the dead
        space a fixed-height panel left in the middle.
        """
        dy = self._group_top
        placements = []          # (control, x, dy_from_top, w, h)
        for slot in range(styles.MAX_SLOTS):
            placements.append((self._dd_boxes[slot], TONE_DD_X, dy, TONE_DD_W, TONE_DD_H))
            placements.append((self._dds[slot], TONE_DD_X + TONE_DD_INSET, dy,
                               TONE_DD_W - 2 * TONE_DD_INSET, TONE_DD_H))
            dy += TONE_DD_H + TONE_DD_GAP
            active = self._slot_active(slot)
            for row in range(styles.PER_TONE):
                r = self._rows[slot][row]
                controls = (r["prob"], r["text"], r["btn"], r["fill_btn"])
                if active:
                    # row height is reserved whether or not the candidates have arrived, so
                    # nothing jumps when results land mid-generation
                    placements += [
                        (r["text"], CAND_TEXT_X, dy, CAND_TEXT_W, CAND_TEXT_H),
                        (r["prob"], CAND_PROB_X, dy + 34, CAND_PROB_W, 14),
                        (r["btn"], CAND_BTN_X, dy + 24, CAND_BTN_W, CAND_BTN_H),
                        (r["fill_btn"], CAND_BTN_X + CAND_BTN_W + CAND_BTN_GAP, dy + 24,
                         CAND_BTN_W, CAND_BTN_H),
                    ]
                    dy += CAND_ROW_H
                else:
                    for c in controls:
                        c.setHidden_(True)
            if slot < styles.MAX_SLOTS - 1:
                dy += GROUP_GAP

        content_h = dy + BOTTOM_PAD
        view = getattr(self, "_document", None) or self.panel.contentView()
        view.setFrameSize_(NSMakeSize(PANEL_W, content_h))
        for ctrl, x, top, w, h in placements + self._fixed:
            ctrl.setFrame_(NSMakeRect(x, content_h - top - h, w, h))

        # resize the window with its TOP edge pinned: growing downwards is what the eye
        # expects here, and _position_near() anchors the panel to WeChat's top anyway
        f = self.panel.frame()
        top = f.origin.y + f.size.height
        frame_h = min(content_h + self._title_h, getattr(self, '_panel_height_limit', float('inf')))
        frame = NSMakeRect(f.origin.x, top - frame_h, PANEL_W, frame_h)
        if f != frame:
            self.panel.setFrame_display_(frame, True)
        self._expanded_h = frame_h


    @objc.python_method
    def _wire_window_controls(self):
        """Native traffic lights, mapped to this app's actions.

        red    -> quit. A hidden panel would otherwise be unreachable: LSUIElement apps
                  have no Dock icon, so a plain order-out looks like a crash.
        yellow -> roll the panel up instead of miniaturizing, for the same reason.
        green  -> hidden: the HUD has a fixed size and nothing to zoom.
        """
        close = self.panel.standardWindowButton_(NSWindowCloseButton)
        mini = self.panel.standardWindowButton_(NSWindowMiniaturizeButton)
        zoom = self.panel.standardWindowButton_(NSWindowZoomButton)
        if close:
            close.setTarget_(self)
            close.setAction_("quitApp:")
            close.setToolTip_("退出 jev-jarvis")
        if mini:
            mini.setTarget_(self)
            mini.setAction_("collapsePanel:")
            mini.setToolTip_("收起 / 展开面板")
        if zoom:
            zoom.setHidden_(True)


    @objc.python_method
    def _make_label(self, x, y, w, h, size=13, color=None, bold=False):
        tf = NSTextField.alloc().initWithFrame_(NSMakeRect(x, y, w, h))
        tf.setStringValue_("")
        tf.setBezeled_(False)
        tf.setDrawsBackground_(False)
        tf.setEditable_(False)
        tf.setSelectable_(True)
        tf.setTextColor_(PALETTE["text"] if color is None else color)
        tf.setFont_(NSFont.boldSystemFontOfSize_(size) if bold else NSFont.systemFontOfSize_(size))
        return tf


    @objc.python_method
    def _make_button(self, x, y, w, h, title, action, tag):
        """A native rounded bezel with a WeChat-green label — reads correctly on #F7F7F7."""
        btn = NSButton.alloc().initWithFrame_(NSMakeRect(x, y, w, h))
        btn.setTitle_(title)
        btn.setBezelStyle_(NSBezelStyleRounded)
        btn.setFont_(NSFont.systemFontOfSize_(11))
        btn.setContentTintColor_(PALETTE["green"])
        btn.setTarget_(self)
        btn.setAction_(action)
        btn.setTag_(tag)
        btn.setHidden_(True)
        return btn


    @objc.python_method
    def _show(self):
        if not self.panel.isVisible():
            self.panel.orderFrontRegardless()


    @objc.python_method
    def _context_line(self, sender, prev: str) -> str:
        parts = []
        if sender:
            parts.append(f"来自 {sender}")
        if prev:
            parts.append(f"上文：{prev[:26]}")
        return " · ".join(parts)


    @objc.python_method
    def _render(self, key: str, text: str, color: NSColor | None = None):
        tf = self.rows[key]
        tf.setStringValue_(text)
        if color is not None:
            tf.setTextColor_(color)


    @objc.python_method
    def _row_controls(self, slot: int, row: int):
        r = self._rows[slot][row]
        return (r["prob"], r["text"], r["btn"], r["fill_btn"])


    @objc.python_method
    def _render_groups(self, payload: list):
        """payload: [(slot, tone, [{"text","prob"}, ...]), ...] — one entry per active tone.

        Rows the model did not fill are emptied and their buttons hidden, but the row keeps
        its space: the panel's height is decided by the tone selection, not by how many lines
        came back, so a late result cannot resize the panel under the cursor.
        """
        wanted = set()
        for slot, _tone, items in payload:
            for row in range(styles.PER_TONE):
                if row < len(items):
                    it = items[row]
                    wanted.add((slot, row))
                    r = self._rows[slot][row]
                    prob = "排序中" if it["prob"] is None else f"{it['prob'] * 100:.0f}%"
                    r["prob"].setStringValue_(f"#{row + 1} · {prob}")
                    r["text"].setStringValue_(it["text"])
                    for c in self._row_controls(slot, row):
                        c.setHidden_(not self._slot_active(slot))
                    self.cand_texts[slot * styles.PER_TONE + row] = it["text"]
        for slot in range(styles.MAX_SLOTS):
            for row in range(styles.PER_TONE):
                if (slot, row) not in wanted and self._slot_active(slot):
                    r = self._rows[slot][row]
                    r["prob"].setStringValue_("")
                    r["text"].setStringValue_("")
                    r["btn"].setHidden_(True)
                    r["fill_btn"].setHidden_(True)
                    self.cand_texts[slot * styles.PER_TONE + row] = None
        self._relayout()


    @objc.python_method
    def _clear_candidates(self):
        for slot in range(styles.MAX_SLOTS):
            for row in range(styles.PER_TONE):
                r = self._rows[slot][row]
                r["prob"].setStringValue_("")
                r["text"].setStringValue_("")
                r["btn"].setHidden_(True)
                r["fill_btn"].setHidden_(True)
                self.cand_texts[slot * styles.PER_TONE + row] = None


    @objc.python_method
    def _display_height(self) -> float:
        """Height of the display whose origin is (0,0) — the Quartz<->Cocoa flip constant.

        Taking this from the *target* screen is wrong on multi-display setups: a screen
        placed above the main one has origin.y > 0 and the flip must still use the
        primary display's height.
        """
        for scr in NSScreen.screens():
            f = scr.frame()
            if f.origin.x == 0 and f.origin.y == 0:
                return f.size.height
        return NSScreen.mainScreen().frame().size.height


    @objc.python_method
    def _position_near(self, win: dict | None):
        """Dock the panel beside WeChat, on the screen WeChat is actually on.

        Uses global Cocoa coordinates throughout. NSScreen.mainScreen() must NOT be used:
        it follows whichever display holds the key window, so relying on it made the panel
        hop ~1369 px between displays a few times a minute.
        """
        flip = self._display_height()
        panel_h = self.panel.frame().size.height or PANEL_H
        panel_w = self.panel.frame().size.width or PANEL_W
        screens = list(NSScreen.screens())
        primary = next((s for s in screens
                        if s.frame().origin.x == 0 and s.frame().origin.y == 0), screens[0])

        if win:
            # CGWindow bounds are top-left origin global pixels -> Cocoa bottom-left
            wx, wy = win["x"], win["y"]
            ww, wh = win["w"], win["h"]
            cx_win = wx + ww / 2.0
            cyan = flip - (wy + wh / 2.0)
            host = next((s for s in screens
                         if s.frame().origin.x <= cx_win <= s.frame().origin.x + s.frame().size.width
                         and s.frame().origin.y <= cyan <= s.frame().origin.y + s.frame().size.height),
                        primary)
            sf = host.frame()
            # dock right of WeChat if it fits on that screen, else left, else its right edge
            x = wx + ww + 8
            if x + panel_w > sf.origin.x + sf.size.width:
                x = wx - panel_w - 8
            if x < sf.origin.x:
                x = sf.origin.x + sf.size.width - panel_w - 12
            y = flip - wy - panel_h
            y = max(sf.origin.y + 40, min(y, sf.origin.y + sf.size.height - panel_h - 40))
        else:
            sf = primary.frame()
            x = sf.size.width - panel_w - 12
            y = sf.size.height - panel_h - 60

        # dead-band: ignore sub-2pt corrections and one-off blips, so WeChat's own window
        # animations (and our own numeric noise) stop nudging the panel around
        target = (round(x), round(y))
        last = self._last_origin
        if last is None:                    # first placement: apply without debounce
            self._last_origin = target
            self._pending_origin = target
            self.panel.setFrameOrigin_(target)
            return
        if abs(target[0] - last[0]) <= 2 and abs(target[1] - last[1]) <= 2:
            return
        if target != self._pending_origin:
            self._pending_origin = target
            return  # require the same target on two consecutive ticks before moving
        self._last_origin = target
        self.panel.setFrameOrigin_(target)


    @objc.python_method
    def _set_collapsed(self, collapsed: bool):
        """Roll the panel up to a title+status strip, or back to full height."""
        self._collapsed = collapsed
        controlled = ["message", "sender", "intent", "confidence", "risk", "actions",
                      "cand_header"]   # "chat" and "status" survive collapsing
        for key in controlled:
            self.rows[key].setHidden_(collapsed)
        for slot in range(styles.MAX_SLOTS):
            self._dds[slot].setHidden_(collapsed)
            self._dd_boxes[slot].setHidden_(collapsed)
            for row in range(styles.PER_TONE):
                has = self.cand_texts[slot * styles.PER_TONE + row] is not None
                for c in self._row_controls(slot, row):
                    c.setHidden_(collapsed or not has)
        if not collapsed:
            # re-expanding puts every control back where _relayout() wants it, and re-hides
            # the slots that are switched off — the collapse above cannot know that
            self._relayout()
            self._last_origin = None      # let the next tick re-dock cleanly
            return

        rect = self.panel.frame()
        # _expanded_h is maintained by _relayout() (it changes with the tone selection), so
        # expanding reads the current full height rather than a value captured at startup
        new_h = COLLAPSED_H if collapsed else (self._expanded_h or PANEL_H)
        self.panel.setFrame_display_(
            NSMakeRect(rect.origin.x, rect.origin.y + (rect.size.height - new_h),
                       rect.size.width, new_h), True)
        self._last_origin = None      # let the next tick re-dock cleanly
