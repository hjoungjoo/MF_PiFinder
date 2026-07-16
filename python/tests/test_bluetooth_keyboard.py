import pytest

try:
    from PiFinder.ui import bluetooth_keyboard as bk

    def _clean(raw: str) -> str:
        text = bk.ANSI_ESCAPE_RE.sub("", raw)
        text = bk.CONTROL_CHARS_RE.sub("", text)
        return text.replace("\r", "\n")

    @pytest.mark.unit
    def test_passkey_parsed_through_readline_control_markers():
        # bluetoothctl wraps its colored agent prompt in readline non-printing
        # markers \x01/\x02, which land between "Passkey:" and the digits. This
        # is the exact shape captured from a Logitech Keys-To-Go 2 pairing.
        raw = (
            "\x1b[0;94m[Keys-To-Go 2]\x1b[0m# \r"
            "\x01\x1b[0;91m\x02[agent]\x01\x1b[0m\x02 Passkey: "
            "\x01\x1b[1;30m\x02\x01\x1b[1;37m\x02189795"
        )
        cleaned = _clean(raw)
        assert cleaned.splitlines()[-1] == "[agent] Passkey: 189795"
        match = bk.PASSKEY_RE.search(cleaned)
        assert match is not None
        assert match.group(1) == "189795"

    @pytest.mark.unit
    def test_control_chars_stripped_but_tab_and_newline_kept():
        # \x01/\x02 removed; \t kept; \r normalized to \n
        cleaned = _clean("a\x01\x02b\tc\r\nd")
        assert cleaned == "ab\tc\n\nd"

except ImportError:
    pass
