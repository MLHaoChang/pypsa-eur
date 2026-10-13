"""
A small Excel formula evaluator for the pro forma workbook tests (plan S5,
review v1 S9). The environment has no Excel and no formula engine, so the
XLSX's formulas are checked by evaluating them here.

It implements exactly what `services/study/proforma_xlsx.py` writes: numbers,
cell references (``B1``, ``$B$1``, ``'Cash flows'!B30``, ``KPIs!B2``), ranges
inside functions, ``+ - * / ^``, unary minus, parentheses, ``SUM`` and
``NPV``. Anything else raises, so a formula the writer starts emitting that
this cannot read fails the test rather than evaluating to something wrong.

Excel semantics kept on purpose:

* ``NPV(rate, value1, ...)`` discounts its FIRST value by one period:
  ``Σ_{i=1..n} v_i / (1 + rate)^i``. It is not a textbook NPV; the year-0
  cash flow must be added outside it (``=CF0 + NPV(rate, CF1:CFn)``). Empty
  cells inside a range are skipped (Excel ignores empty cells, text and
  logical values in references).
* Unary minus binds tighter than ``^`` (``-2^2 = 4`` in Excel), and ``^`` is
  left-associative.
"""
from __future__ import annotations

import re

_TOKEN = re.compile(r"""
    \s*(?:
      (?P<num>\d+(?:\.\d*)?(?:[eE][-+]?\d+)?|\.\d+(?:[eE][-+]?\d+)?)
    | (?P<ref>(?:'(?P<qsheet>[^']+)'!|(?P<sheet>[A-Za-z_][A-Za-z0-9_]*)!)?
              \$?(?P<col>[A-Z]{1,3})\$?(?P<row>\d+)
              (?::\$?(?P<col2>[A-Z]{1,3})\$?(?P<row2>\d+))?)
    | (?P<func>[A-Z][A-Z0-9.]*)\(
    | (?P<op>[-+*/^(),])
    )""", re.VERBOSE)


def excel_npv(rate: float, values: list[float]) -> float:
    """Excel's NPV: the first value is discounted one period."""
    return sum(v / (1.0 + rate) ** (i + 1) for i, v in enumerate(values))


def _col_index(col: str) -> int:
    n = 0
    for ch in col:
        n = n * 26 + (ord(ch) - 64)
    return n


def _col_letters(n: int) -> str:
    out = ""
    while n:
        n, r = divmod(n - 1, 26)
        out = chr(65 + r) + out
    return out


class FormulaEvaluator:
    """Evaluates cells of an openpyxl workbook loaded WITHOUT ``data_only``."""

    def __init__(self, workbook):
        self.wb = workbook
        self._cache: dict[tuple[str, str], float | None] = {}
        self._active: set[tuple[str, str]] = set()

    def value(self, sheet: str, coord: str) -> float | None:
        key = (sheet, coord.replace("$", ""))
        if key in self._cache:
            return self._cache[key]
        if key in self._active:
            raise ValueError(f"circular reference at {sheet}!{coord}")
        self._active.add(key)
        try:
            raw = self.wb[sheet][key[1]].value
            if raw is None:
                out = None
            elif isinstance(raw, str) and raw.startswith("="):
                out = self.evaluate(raw[1:], sheet)
            elif isinstance(raw, (int, float)) and not isinstance(raw, bool):
                out = float(raw)
            else:
                raise ValueError(f"{sheet}!{coord} holds {raw!r}, not a number")
        finally:
            self._active.discard(key)
        self._cache[key] = out
        return out

    # ── parsing ─────────────────────────────────────────────────────────
    def evaluate(self, text: str, sheet: str) -> float:
        # Re-entrant: a referenced formula cell is evaluated mid-parse.
        saved = (getattr(self, "_tokens", None), getattr(self, "_pos", 0),
                 getattr(self, "_sheet", None))
        self._tokens = self._tokenize(text)
        self._pos = 0
        self._sheet = sheet
        try:
            out = self._expr()
            if self._pos != len(self._tokens):
                raise ValueError(f"unparsed tail in {text!r}")
        finally:
            self._tokens, self._pos, self._sheet = saved
        return out

    @staticmethod
    def _tokenize(text: str) -> list[tuple[str, object]]:
        out, pos = [], 0
        text = text.strip()
        while pos < len(text):
            m = _TOKEN.match(text, pos)
            if not m or m.end() == pos:
                raise ValueError(f"cannot read {text[pos:]!r}")
            pos = m.end()
            if m.group("num"):
                out.append(("num", float(m.group("num"))))
            elif m.group("ref"):
                out.append(("ref", m.groupdict()))
            elif m.group("func"):
                out.append(("func", m.group("func")))
            else:
                out.append(("op", m.group("op")))
        return out

    def _peek(self):
        return self._tokens[self._pos] if self._pos < len(self._tokens) else (None, None)

    def _take(self, kind=None, value=None):
        tok = self._peek()
        if kind is not None and tok[0] != kind or value is not None and tok[1] != value:
            raise ValueError(f"expected {value or kind}, got {tok}")
        self._pos += 1
        return tok

    def _expr(self) -> float:
        v = self._term()
        while self._peek() in (("op", "+"), ("op", "-")):
            op = self._take()[1]
            rhs = self._term()
            v = v + rhs if op == "+" else v - rhs
        return v

    def _term(self) -> float:
        v = self._power()
        while self._peek() in (("op", "*"), ("op", "/")):
            op = self._take()[1]
            rhs = self._power()
            v = v * rhs if op == "*" else v / rhs
        return v

    def _power(self) -> float:
        v = self._unary()
        while self._peek() == ("op", "^"):
            self._take()
            v = v ** self._unary()
        return v

    def _unary(self) -> float:
        if self._peek() == ("op", "-"):
            self._take()
            return -self._unary()
        if self._peek() == ("op", "+"):
            self._take()
            return self._unary()
        return self._atom()

    def _atom(self) -> float:
        kind, val = self._peek()
        if kind == "num":
            self._take()
            return val
        if kind == "op" and val == "(":
            self._take()
            v = self._expr()
            self._take("op", ")")
            return v
        if kind == "ref":
            self._take()
            cells = self._cells(val)
            if len(cells) != 1:
                raise ValueError("a range outside a function")
            v = cells[0]
            return 0.0 if v is None else v  # Excel reads an empty cell as 0
        if kind == "func":
            self._take()
            return self._call(val)
        raise ValueError(f"unexpected token {kind} {val!r}")

    def _cells(self, ref: dict) -> list[float | None]:
        sheet = ref["qsheet"] or ref["sheet"] or self._sheet
        c1, r1 = _col_index(ref["col"]), int(ref["row"])
        c2 = _col_index(ref["col2"]) if ref["col2"] else c1
        r2 = int(ref["row2"]) if ref["row2"] else r1
        out = []
        for r in range(min(r1, r2), max(r1, r2) + 1):
            for c in range(min(c1, c2), max(c1, c2) + 1):
                out.append(self.value(sheet, f"{_col_letters(c)}{r}"))
        return out

    def _args(self) -> list[list[float | None]]:
        """Each argument as a list: a range expands, an expression is one value."""
        args = []
        while True:
            kind, val = self._peek()
            nxt = self._tokens[self._pos + 1] if self._pos + 1 < len(self._tokens) else (None, None)
            if kind == "ref" and nxt in (("op", ","), ("op", ")")):
                self._take()
                args.append(self._cells(val))
            else:
                args.append([self._expr()])
            if self._peek() == ("op", ","):
                self._take()
                continue
            self._take("op", ")")
            return args

    def _call(self, name: str) -> float:
        args = self._args()
        if name == "SUM":
            return float(sum(v for a in args for v in a if v is not None))
        if name == "NPV":
            (rate,), *rest = args
            values = [v for a in rest for v in a if v is not None]
            return excel_npv(rate, values)
        raise ValueError(f"function {name} is not implemented by this evaluator")
