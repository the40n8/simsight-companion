"""Key tables: virtual-key codes, the bare-key bindings, the Ctrl+Alt
chords and which messages auto-repeat."""

from __future__ import annotations

VK_SHIFT = 0x10
VK_MENU = 0x12  # Alt
VK_CONTROL = 0x11
VK_LMENU = 0xA4  # left Alt
VK_RMENU = 0xA5  # right Alt; AltGr on many non-US layouts

# Tab direction is resolved from Shift at press time, with one latch per key.
BINDINGS = {
    'A11yTabNext': ('next control (Shift reverses)', [(0x09, False)]),
    'A11yActivate': ('activate or confirm choice', [(0x0D, False)]),
    'A11yToggle': ('toggle or select', [(0x20, False)]),
    'A11yReturnRow': ('return to last picker row', [(0x08, False)]),
    'A11yEscape': ('back or close', [(0x1B, False)]),
    'A11yArrowUp': ('previous row', [(0x26, False)]),
    'A11yArrowDown': ('next row', [(0x28, False)]),
    'A11yArrowLeft': ('left or decrease', [(0x25, False)]),
    'A11yArrowRight': ('right or increase', [(0x27, False)]),
}
NUMPAD_BINDINGS = {
    0x64: 'A11yArrowUp',
    0x66: 'A11yArrowDown',
    0x65: 'A11yActivate',
    0x68: 'A11yRepeat',
    0x6E: 'A11yDismiss',
}

# Scanner chords (Ctrl+Alt; the layout started as glaroc's a11yscan and now
# drives the scanner tree: Up/Down siblings, Right enters, Left goes back):
# vk -> (scanner intent, repeats while held). Polled keys cannot be
# swallowed, so a chord layer keeps bare keys free for the game, and nothing
# here collides with the numpad set above. Two known collisions outside the
# game: Ctrl+Alt+arrows is Intel's screen-rotation hotkey on some laptops
# (turn it off in the Intel graphics settings), and AltGr sends Ctrl+Alt on
# many non-US layouts, so a right-Alt chord on such a layout is treated as
# typing and ignored (see _altgr_pressed).
CHORDS = {
    0x26: ('prev', True),  # Up: previous thing
    0x28: ('next', True),  # Down: next thing
    0x25: ('prevcat', True),  # Left: previous main category
    0x27: ('nextcat', True),  # Right: next main category
    0x21: ('prevsub', True),  # Page Up: previous subcategory
    0x22: ('nextsub', True),  # Page Down: next subcategory
    0x0D: ('open', False),  # Enter: open the menu / activate
    0x20: ('scannermode', False),  # Space: scanner mode on or off
    0x08: ('back', False),  # Backspace: close the menu
    0x53: ('rescan', False),  # S
    0x57: ('where', False),  # W: where am I
    0x52: ('repeat', False),  # R
    0x48: ('help', False),  # H
    0x43: ('clock', False),  # C: the day and time
    0x50: ('phone', False),  # P: waiting household call/text
    0x45: ('mood', False),  # E: emotion and moodlets
    0x47: ('wants', False),  # G: wants/fears
    0x51: ('queue', False),  # Q: the active Sim's action queue
    0x4C: ('logging', False),  # L: diagnostic logging on or off
    0x56: ('events', False),  # V: running events (end them, send guests home)
    0x4D: ('milestones', False),  # M: the active Sim's milestones (Growing Together)
    0x4B: ('values', False),  # K: the active Sim's character values (Parenthood)
    0x4F: ('catalog', False),  # O: the Buy catalog tree (Build mode)
    0x49: ('catalogfind', False),  # I: search the Buy catalog (Build mode); not N (NVDA's own key)
    # DaRainbowGirlXX's Build cursor and scanner keys (merged 2026-11-12):
    0x55: ('layerup', False),  # U: Build cursor target layer up (wall heights, ceiling)
    0x44: ('layerdown', False),  # D: Build cursor target layer down
    0x4A: ('roomlabel', False),  # J: label the room at the Build cursor (L is logging)
    0xBC: ('scopeprev', False),  # comma: smaller scanner range
    0xBE: ('scopenext', False),  # period: larger scanner range
    0x42: ('buildplace', False),  # B: Build placement on the lot grid
    0x54: ('gridtoggle', False),  # T: read-only lot grid
    0xDB: ('rotateleft', False),  # [ : native Build rotation
    0xDD: ('rotateright', False),  # ]
    0x5A: ('undo', False),  # Z: native Build undo
    0x59: ('redo', False),  # Y: native Build redo
    0x46: ('buildgrid', False),  # F: aim at sampled grid point, never place
    0x58: ('sell', False),  # X: sell the object on the grid point (twice)
    # glaroc's Ctrl+Alt+1..8: switch to household Sim N (selectable order).
    0x31: ('sim1', False),
    0x32: ('sim2', False),
    0x33: ('sim3', False),
    0x34: ('sim4', False),
    0x35: ('sim5', False),
    0x36: ('sim6', False),
    0x37: ('sim7', False),
    0x38: ('sim8', False),
}
CHORD_NAMES = {
    0x26: 'Up',
    0x28: 'Down',
    0x25: 'Left',
    0x27: 'Right',
    0x20: 'Space',
    0x21: 'PageUp',
    0x22: 'PageDown',
    0x0D: 'Enter',
    0x08: 'Backspace',
    0x53: 'S',
    0x57: 'W',
    0x52: 'R',
    0x48: 'H',
    0x43: 'C',
    0x50: 'P',
    0x45: 'E',
    0x47: 'G',
    0x51: 'Q',
    0x4C: 'L',
    0x56: 'V',
    0x4D: 'M',
    0x4B: 'K',
    0x4F: 'O',
    0x49: 'I',
    0x55: 'U',
    0x44: 'D',
    0x4A: 'J',
    0xBC: 'comma',
    0xBE: 'period',
    0x42: 'B',
    0x54: 'T',
    0xDB: '[',
    0xDD: ']',
    0x5A: 'Z',
    0x59: 'Y',
    0x46: 'F',
    0x58: 'X',
    0x31: '1',
    0x32: '2',
    0x33: '3',
    0x34: '4',
    0x35: '5',
    0x36: '6',
    0x37: '7',
    0x38: '8',
}


# Holding a navigation key auto-repeats it (OS-style: initial delay, then a
# steady rate). Only movement actions repeat; activate/clear/speech keys fire
# once per press. Flash preserves input order while a list realizes a row.
REPEATABLE = {
    'A11yNavNext',
    'A11yNavPrev',
    'A11yArrowUp',
    'A11yArrowDown',
    'A11yArrowLeft',
    'A11yArrowRight',
    'A11yScanner:prev',
    'A11yScanner:next',
    'A11yBuildGrid:up',
    'A11yBuildGrid:down',
    'A11yBuildGrid:left',
    'A11yBuildGrid:right',
}

# Scanner mode: bare keys browse the scanner tree while Flash reports that
# the scanner owns input; its native guards consume them, so the arrows never
# pan the camera. No system hook. Escape is deliberately not a scanner key:
# it stays the game's Options menu.
SCANNER_KEYS = {
    0x26: 'A11yScanner:prev',
    0x28: 'A11yScanner:next',
    0x27: 'A11yScanner:nextcat',
    0x25: 'A11yScanner:prevcat',
    0x0D: 'A11yScanner:open',
    0x08: 'A11yScanner:back',
    0x20: 'A11yScanner:repeat',
}
# The Build cursor (grid Build placement): plain keys while Flash reports
# it owns them (TooltipManager A11yBuildGridOwner, simsight.buildgridowner).
BUILD_GRID_KEYS = {
    0x26: 'up',
    0x28: 'down',
    0x25: 'left',
    0x27: 'right',
    0x21: 'floorup',
    0x22: 'floordown',
    0x0D: 'place',
    0x20: 'repeat',
    0x08: 'back',
    0x1B: 'back',
    0x52: 'turn',
    0x24: 'last',
    0x2E: 'sell',
    0x31: 'info1',
    0x32: 'info2',
    0x33: 'info3',
    0x34: 'info4',
    0x35: 'info5',
    0x36: 'info6',
    0x4D: 'moveobject',
}  # M: pick up an object, M again puts it down here
REPEAT_DELAY = 0.45
REPEAT_INTERVAL = 0.065
