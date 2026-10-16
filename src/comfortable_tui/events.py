"""Incremental VT input decoding; handles partial sequences and bracketed paste."""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Key:
    name: str
    text: str = ""
    phase: str = "press"
    modifiers: frozenset[str] = frozenset()


@dataclass(frozen=True)
class Paste:
    text: str


@dataclass(frozen=True)
class Mouse:
    x: int
    y: int
    button: int = 0
    phase: str = "press"


@dataclass(frozen=True)
class Capability:
    name: str
    supported: bool


Event = Key | Paste | Mouse | Capability

KEYS = {"A": "up", "B": "down", "C": "right", "D": "left", "H": "home", "F": "end",
        "Z": "shift+tab", "1~": "home", "2~": "insert", "3~": "delete", "4~": "end",
        "5~": "pageup", "6~": "pagedown", "7~": "home", "8~": "end",
        "P": "f1", "Q": "f2", "R": "f3", "S": "f4",
        "11~": "f1", "12~": "f2", "13~": "f3", "14~": "f4", "15~": "f5",
        "17~": "f6", "18~": "f7", "19~": "f8", "20~": "f9", "21~": "f10",
        "23~": "f11", "24~": "f12"}
KITTY_KEYS = {13: "enter", 27: "escape", 9: "tab", 127: "backspace", 57352: "up",
              57353: "down", 57350: "left", 57351: "right", 57356: "home", 57357: "end",
              57354: "pageup", 57355: "pagedown", 57349: "delete", 57348: "insert",
              **{57364 + i: f"f{i + 1}" for i in range(35)}}


class InputParser:
    def __init__(self, max_paste: int = 1_000_000):
        self.buffer = ""
        self.pasting = False
        self.max_paste = max_paste
        self.paste = ""

    def feed(self, text: str, *, flush: bool = False) -> list[Event]:
        self.buffer += text
        events: list[Event] = []
        while self.buffer:
            if self.pasting:
                end = self.buffer.find("\x1b[201~")
                if end < 0:
                    # Keep a possible partial delimiter, bound stored payload.
                    if len(self.buffer) > 6:
                        self.paste = (self.paste + self.buffer[:-6])[:self.max_paste]
                        self.buffer = self.buffer[-6:]
                    break
                events.append(Paste((self.paste + self.buffer[:end])[:self.max_paste]))
                self.pasting, self.paste = False, ""
                self.buffer = self.buffer[end + 6:]
                continue
            if self.buffer.startswith(("\x1b_G", "\x1b]", "\x1bP")):
                end = self.buffer.find("\x1b\\")
                if end < 0:
                    if len(self.buffer) > 8192:
                        self.buffer = ""
                    break
                response = self.buffer[:end]
                self.buffer = self.buffer[end + 2:]
                if response.startswith("\x1b_G") and "i=31" in response:
                    events.append(Capability("kitty_graphics", response.endswith(";OK")))
                continue
            if self.buffer.startswith("\x1b[200~"):
                self.pasting = True
                self.buffer = self.buffer[6:]
                continue
            if self.buffer.startswith("\x1b["):
                match = re.match(r"\x1b\[([0-?]*)([ -/]*)([@-~])", self.buffer)
                if not match:
                    if flush:
                        self.buffer = self.buffer[1:]
                        events.append(Key("escape"))
                        continue
                    break
                params, intermediate, final = match.groups()
                self.buffer = self.buffer[match.end():]
                if final == "y" and intermediate == "$" and params.startswith("?2026;"):
                    events.append(Capability("synchronized", params.split(";")[-1] in ("1", "2")))
                elif params.startswith("?") and final == "u":
                    events.append(Capability("kitty_keyboard", True))
                elif params.startswith("<") and final in "Mm":
                    parts = params[1:].split(";")
                    if len(parts) == 3 and all(p.isdigit() for p in parts):
                        b, x, y = map(int, parts)
                        phase = "scroll" if b & 64 else "move" if b & 32 else "release" if final == "m" else "press"
                        events.append(Mouse(x - 1, y - 1, b, phase))
                elif final == "u":
                    event = self._kitty(params)
                    if event:
                        events.append(event)
                else:
                    code = params.split(";")[0] + final if final == "~" else final
                    name = KEYS.get(code)
                    if name:
                        # Kitty uses legacy functional-key finals with extended
                        # modifier:event-type fields (not only CSI ... u).
                        modifier = params.split(";")[1] if ";" in params else "1"
                        parsed = self._kitty("0;" + modifier)
                        if parsed:
                            if name == "tab" and "shift" in parsed.modifiers:
                                name = "shift+tab"
                            events.append(Key(name, phase=parsed.phase, modifiers=parsed.modifiers))
                continue
            if self.buffer.startswith("\x1bO"):
                if len(self.buffer) < 3:
                    break
                name = KEYS.get(self.buffer[2])
                self.buffer = self.buffer[3:]
                if name:
                    events.append(Key(name))
                continue
            char = self.buffer[0]
            if char == "\x1b" and len(self.buffer) == 1 and not flush:
                break
            if char == "\x1b" and len(self.buffer) >= 2 and self.buffer[1] != "\x1b":
                alt = self.buffer[1]
                self.buffer = self.buffer[2:]
                events.append(Key("alt+" + alt, modifiers=frozenset({"alt"})))
                continue
            self.buffer = self.buffer[1:]
            name = {"\x1b": "escape", "\r": "enter", "\n": "enter", "\t": "tab",
                    "\x7f": "backspace", "\x08": "backspace"}.get(char)
            if name:
                events.append(Key(name))
            elif 0 < ord(char) < 27:
                events.append(Key("ctrl+" + chr(ord(char) + 96)))
            elif ord(char) >= 32:
                events.append(Key(char, char))
        return events

    def _kitty(self, params: str) -> Key | None:
        try:
            parts = params.split(";")
            codes = parts[0].split(":")
            code = int(codes[0])
            mod = parts[1].split(":") if len(parts) > 1 else ["1"]
            bits = int(mod[0] or "1") - 1
            if bits < 0:
                return None
            phase = {1: "press", 2: "repeat", 3: "release"}.get(int(mod[1]) if len(mod) > 1 else 1)
            if phase is None:
                return None
            modifiers = frozenset(name for bit, name in [(1, "shift"), (2, "alt"), (4, "ctrl"), (8, "super")]
                                  if bits & bit)
            char = chr(code) if 32 <= code <= 0x10ffff and code < 57344 else ""
            if len(parts) > 2 and parts[2]:
                char = "".join(chr(int(point)) for point in parts[2].split(":")
                               if 32 <= int(point) <= 0x10ffff and not 0x7f <= int(point) < 0xa0)
            elif "shift" in modifiers and len(codes) > 1 and codes[1]:
                shifted = int(codes[1])
                char = chr(shifted) if 32 <= shifted < 57344 else char
            name = KITTY_KEYS.get(code, char or f"key-{code}")
            if "ctrl" in modifiers:
                name = "ctrl+" + name.lower()
                char = ""
            elif name == "tab" and "shift" in modifiers:
                name = "shift+tab"
            elif "alt" in modifiers or "super" in modifiers:
                name = "+".join(sorted(modifiers)) + "+" + name
                char = ""
            return Key(name, char, phase, modifiers)
        except (ValueError, IndexError):
            return None
