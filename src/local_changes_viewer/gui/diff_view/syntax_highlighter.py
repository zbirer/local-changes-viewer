from PySide6.QtGui import QColor, QSyntaxHighlighter, QTextCharFormat, QTextDocument
from pygments.lexer import Lexer
from pygments.lexers import TextLexer, get_lexer_for_filename
from pygments.token import _TokenType
from pygments.util import ClassNotFound

_TOKEN_COLORS: dict[_TokenType, str] = {}


def _load_token_colors() -> dict[_TokenType, str]:
    from pygments.token import Comment, Keyword, Literal, Name, Number, Operator, String

    return {
        Keyword: "#C678DD",
        Name.Builtin: "#61AFEF",
        Name.Function: "#61AFEF",
        Name.Class: "#E5C07B",
        Name.Decorator: "#E5C07B",
        String: "#98C379",
        Number: "#D19A66",
        Literal: "#D19A66",
        Comment: "#5C6370",
        Operator: "#56B6C2",
    }


_TOKEN_COLORS = _load_token_colors()


def _color_for_token(token_type: _TokenType) -> QColor | None:
    current = token_type
    while current is not None:
        hex_color = _TOKEN_COLORS.get(current)
        if hex_color is not None:
            return QColor(hex_color)
        current = current.parent
    return None


class PygmentsHighlighter(QSyntaxHighlighter):
    """Applies Pygments-based coloring per line. `prefix_len` skips leading
    diff-marker characters (e.g. unified view's '+'/'-'/' ') that aren't code."""

    def __init__(self, document: QTextDocument, prefix_len: int = 0) -> None:
        super().__init__(document)
        self._lexer: Lexer = TextLexer(stripnl=False)
        self._prefix_len = prefix_len
        self._filename: str | None = None

    def set_filename(self, filename: str) -> None:
        # `_rebuild()` in side_by_side_view.py calls this on every fold-marker
        # expand, not just on a genuine file switch (set_diff) -- the lexer
        # can't have changed if the filename hasn't, so skip the ClassNotFound
        # probe and the full-document rehighlight() they'd otherwise cost.
        if filename == self._filename:
            return
        self._filename = filename
        try:
            self._lexer = get_lexer_for_filename(filename, stripnl=False)
        except ClassNotFound:
            self._lexer = TextLexer(stripnl=False)
        self.rehighlight()

    def highlightBlock(self, text: str) -> None:
        code = text[self._prefix_len :]
        if not code:
            return
        try:
            tokens = list(self._lexer.get_tokens(code))
        except Exception:
            return

        pos = self._prefix_len
        for token_type, value in tokens:
            length = len(value)
            color = _color_for_token(token_type)
            if color is not None and value.strip():
                fmt = QTextCharFormat()
                fmt.setForeground(color)
                self.setFormat(pos, length, fmt)
            pos += length
