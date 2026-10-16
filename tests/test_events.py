from comfortable_tui import Capability, InputParser, Key, Mouse, Paste


def test_partial_keys_escape_and_utf8_text():
    parser = InputParser()
    assert parser.feed("\x1b[") == []
    assert parser.feed("A中") == [Key("up"), Key("中", "中")]
    assert parser.feed("\x1b") == []
    assert parser.feed("", flush=True) == [Key("escape")]
    assert parser.feed("\x03") == [Key("ctrl+c")]


def test_bracketed_paste_is_not_interpreted_as_keys():
    p = InputParser()
    assert p.feed("\x1b[200~hi\n\x03\x1b[20") == []
    assert p.feed("1~") == [Paste("hi\n\x03")]


def test_paste_limit_and_delimiter_split_at_every_offset():
    raw = "\x1b[200~abc\ndef\x1b[201~"
    for offset in range(len(raw) + 1):
        parser = InputParser(max_paste=4)
        events = parser.feed(raw[:offset]) + parser.feed(raw[offset:])
        assert events == [Paste("abc\n")]


def test_kitty_phases_modifiers_and_capabilities():
    p = InputParser()
    assert p.feed("\x1b[13;1u\x1b[13;1:2u\x1b[13;1:3u") == [
        Key("enter"), Key("enter", phase="repeat"), Key("enter", phase="release")]
    event = p.feed("\x1b[113;5u")[0]
    assert event.name == "ctrl+q" and event.modifiers == frozenset({"ctrl"})
    assert p.feed("\x1b[?2026;2$y\x1b[?0u\x1b_Gi=31;OK\x1b\\") == [
        Capability("synchronized", True), Capability("kitty_keyboard", True),
        Capability("kitty_graphics", True)]
    assert p.feed("\x1b[<0;3;8M\x1b[<0;3;8m") == [Mouse(2, 7), Mouse(2, 7, phase="release")]


def test_kitty_legacy_functional_keys_preserve_release_phase():
    p = InputParser()
    assert p.feed("\x1b[1;1:3A\x1b[5;1:2~") == [Key("up", phase="release"), Key("pageup", phase="repeat")]
    assert p.feed("\x1b[97:65;2u")[0].text == "A"
    assert p.feed("\x1b[0;;20013:25991u")[0].text == "中文"
    assert p.feed("\x1bx") == [Key("alt+x", modifiers=frozenset({"alt"}))]
