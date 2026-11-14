"""polars の最小版。scripts/shrink_polars.py が生成した。手で直さない。

入口: dragowing/overlap.py:load_overlap, pythdragoras/propositions.py:_cond
使う名前: DataFrame, Expr, col, lit, read_ndjson
AST では見えないので式と表にまとめて入れてある: DataFrame.height, DataFrame.columns, DataFrame.__getitem__, DataFrame.select, DataFrame.explode, DataFrame.filter, DataFrame.join, DataFrame.iter_rows, Series.to_list, Expr.__lt__, Expr.__le__, Expr.__gt__, Expr.__ge__, Expr.__eq__, Expr.__ne__, Expr.__and__, Expr.is_not_null
"""
from __future__ import annotations

import json
from pathlib import Path


def _cmp(a, b, op):
    if a is None or b is None:
        return None
    # polars は bool と文字列を "true" / "false" にして比べる。数値と文字列はエラー。
    if type(a) is bool and type(b) is str:
        a = "true" if a else "false"
    elif type(b) is bool and type(a) is str:
        b = "true" if b else "false"
    if isinstance(a, str) or isinstance(b, str):
        if isinstance(a, str) and isinstance(b, str):
            return op(a, b)
        raise TypeError(f"polars_min は文字列と数値を比べない: {a!r} と {b!r}")
    if isinstance(a, (bool, int, float)) and isinstance(b, (bool, int, float)):
        return op(a, b)
    raise TypeError(f"polars_min が比べない型: {type(a).__name__} と {type(b).__name__}")


def _kleene_and(a, b):
    """polars の bool の &。偽が片方なら偽、それ以外で null なら null。"""
    if a is False or b is False:
        return False
    if a is None or b is None:
        return None
    if a is True and b is True:
        return True
    raise TypeError(f"polars_min の & は bool か null だけ: {a!r} & {b!r}")


class Expr:
    """列か定数か、その比較・積。行の辞書を受け取って Python の値を返す。"""

    __slots__ = ("_fn", "_scalar", "_marker")

    def __init__(self, fn, scalar, marker=None):
        self._fn = fn
        self._scalar = scalar
        self._marker = marker

    def is_not_null(self):
        return Expr(lambda row, fn=self._fn: fn(row) is not None, False, "is_not_null")

    def _bin(self, other, op):
        other = other if isinstance(other, Expr) else lit(other)
        return Expr(
            lambda row, a=self._fn, b=other._fn, op=op: _cmp(a(row), b(row), op),
            self._scalar and other._scalar,
        )

    def __lt__(self, other):
        return self._bin(other, lambda x, y: x < y)

    def __le__(self, other):
        return self._bin(other, lambda x, y: x <= y)

    def __gt__(self, other):
        return self._bin(other, lambda x, y: x > y)

    def __ge__(self, other):
        return self._bin(other, lambda x, y: x >= y)

    def __eq__(self, other):
        return self._bin(other, lambda x, y: x == y)

    def __ne__(self, other):
        return self._bin(other, lambda x, y: x != y)

    def __and__(self, other):
        other = other if isinstance(other, Expr) else lit(other)
        return Expr(
            lambda row, a=self._fn, b=other._fn: _kleene_and(a(row), b(row)),
            self._scalar and other._scalar,
        )

    def __or__(self, other):
        raise NotImplementedError("polars_min に | はない（load_overlap は使わない）")

    def __invert__(self):
        raise NotImplementedError("polars_min に ~ はない（load_overlap は使わない）")

    def __bool__(self):
        raise TypeError("polars_min の式は真理値にしない")

    def __getattr__(self, name):
        raise AttributeError("Expr." + name)

    def __repr__(self):
        return "polars_min.Expr"


class Series:
    __slots__ = ("_values", "name")

    def __init__(self, values, name):
        self._values = list(values)
        self.name = name

    def to_list(self):
        return list(self._values)

    def __repr__(self):
        return f"polars_min.Series({self.name!r}, n={len(self._values)})"


def _cell_kind(value):
    """collect のコンストラクタが受けるスカラー。bool を int へは寄せない。"""
    if value is None:
        return None
    if type(value) is bool:
        return "bool"
    if type(value) is int:
        return "int"
    if type(value) is str:
        return "str"
    raise TypeError("polars_min が受けない値: " + type(value).__name__)


def _column_kind(column, values, as_list):
    kinds = set()
    for value in values:
        if as_list:
            if value is None:
                continue
            if not isinstance(value, list):
                raise TypeError(f"polars_min の列 {column} はリストとスカラーを混ぜない")
            for element in value:
                kind = _cell_kind(element)
                if kind:
                    kinds.add(kind)
        else:
            if isinstance(value, list):
                raise TypeError(f"polars_min の列 {column} はリストとスカラーを混ぜない")
            kind = _cell_kind(value)
            if kind:
                kinds.add(kind)
    if len(kinds) > 1:
        raise TypeError(f"polars_min の列 {column} は型を混ぜない: {', '.join(sorted(kinds))}")
    inner = next(iter(kinds)) if kinds else "null"
    return ("list:" + inner) if as_list else inner


def _ingest(data):
    """DataFrame(dict) か DataFrame(list of dict)。collect が使う形だけ。"""
    if isinstance(data, dict):
        columns = list(data)
        seqs = []
        width = None
        for column in columns:
            value = data[column]
            if isinstance(value, str) or not isinstance(value, (list, tuple)):
                raise TypeError(f"polars_min の列 {column} はリスト")
            seq = list(value)
            if width is None:
                width = len(seq)
            elif len(seq) != width:
                raise ValueError(f"polars_min の列の長さが違う: {column} は {len(seq)}、期待は {width}")
            seqs.append(seq)
        rows = [{column: seq[index] for column, seq in zip(columns, seqs)} for index in range(width or 0)]
        kinds = {column: _column_kind(column, [row[column] for row in rows], False) for column in columns}
        return columns, rows, kinds
    if isinstance(data, list):
        if not data:
            raise ValueError("polars_min は空のリストから型を推論しない")
        if not all(isinstance(row, dict) for row in data):
            raise TypeError("polars_min のリストは辞書の行だけ")
        columns, seen = [], set()
        for row in data:
            for key in row:
                if key not in seen:
                    seen.add(key)
                    columns.append(key)
        rows = [{column: row[column] if column in row else None for column in columns} for row in data]
        kinds = {}
        for column in columns:
            as_list = any(isinstance(row[column], list) for row in rows)
            kinds[column] = _column_kind(column, [row[column] for row in rows], as_list)
        return columns, rows, kinds
    raise TypeError("polars_min の DataFrame は列の辞書か行のリスト")


class DataFrame:
    """行のリスト。列順はファイルで最初に見た順（大きな NDJSON では polars の順と違うことがある）。"""

    __slots__ = ("_columns", "_rows", "_kinds")

    def __init__(self, data=None, schema=None, *, schema_overrides=None, strict=True,
                 orient=None, infer_schema_length=100, nan_to_null=False, height=None):
        if (schema is not None or schema_overrides is not None or strict is not True
                or orient is not None or infer_schema_length != 100 or nan_to_null is not False
                or height is not None):
            raise NotImplementedError("polars_min の DataFrame は data だけ")
        columns, rows, kinds = _ingest(data)
        self._columns = columns
        self._rows = rows
        self._kinds = kinds

    @classmethod
    def _frame(cls, columns, rows, kinds=None):
        self = object.__new__(cls)
        self._columns = list(columns)
        self._rows = rows
        self._kinds = dict(kinds) if kinds is not None else {}
        return self

    @property
    def height(self):
        return len(self._rows)

    @property
    def columns(self):
        return list(self._columns)

    def __getitem__(self, key):
        if not isinstance(key, str):
            raise NotImplementedError("polars_min の DataFrame[] は列名だけ")
        if key not in self._columns:
            raise KeyError(key)
        return Series((row[key] for row in self._rows), key)

    def select(self, *exprs, **named):
        if exprs or not named:
            raise NotImplementedError("polars_min の select はキーワード引数の式だけ")
        items = []
        for name, expr in named.items():
            if not isinstance(expr, Expr):
                raise TypeError(f"polars_min の select は式だけ: {name}")
            items.append((name, expr))
        scalar = all(expr._scalar for _, expr in items)
        # 定数だけの select は polars と同じく 1 行。列を含む式は行数に放送する。
        n = 1 if scalar else self.height
        columns = [name for name, _ in items]
        rows = []
        for i in range(n):
            src = {} if scalar else self._rows[i]
            rows.append({name: expr._fn(src) for name, expr in items})
        return DataFrame._frame(columns, rows)

    def explode(self, columns, *more_columns, empty_as_null=False, keep_nulls=True):
        if (more_columns or type(columns) is not str or empty_as_null is not False or keep_nulls is not True):
            raise NotImplementedError("polars_min の explode は列名ひとつ")
        if columns not in self._columns:
            raise KeyError(columns)
        kind = self._kinds[columns]
        if not str(kind).startswith("list:"):
            raise TypeError(f"polars_min の explode はリスト列だけ: {columns}")
        # 空のリストは消え、null のリストは null のスカラーになる。
        out = []
        for row in self._rows:
            value = row[columns]
            if value is None:
                out.append(dict(row))
            elif isinstance(value, list):
                for element in value:
                    copied = dict(row)
                    copied[columns] = element
                    out.append(copied)
            else:
                raise TypeError(f"polars_min の explode はリスト列だけ: {columns}")
        kinds = dict(self._kinds)
        kinds[columns] = kind.split(":", 1)[1]
        return DataFrame._frame(self._columns, out, kinds)

    def filter(self, *predicates, **constraints):
        if constraints or len(predicates) != 1:
            raise NotImplementedError("polars_min の filter は式ひとつ")
        pred = predicates[0]
        if not isinstance(pred, Expr) or pred._marker != "is_not_null":
            raise NotImplementedError("polars_min の filter は col().is_not_null() だけ")
        return DataFrame._frame(self._columns, [row for row in self._rows if pred._fn(row) is True], self._kinds)

    def join(self, other, on=None, how="inner", *, left_on=None, right_on=None, suffix="_right",
             validate="m:m", nulls_equal=False, coalesce=None, maintain_order=None, build_side="auto"):
        if (how != "left" or type(on) is not str or left_on is not None or right_on is not None
                or suffix != "_right" or validate != "m:m" or nulls_equal is not False
                or coalesce is not None or maintain_order is not None or build_side != "auto"):
            raise NotImplementedError("polars_min の join は on=列、how='left' だけ")
        if not isinstance(other, DataFrame):
            raise TypeError("polars_min の join は polars_min の DataFrame")
        if on not in self._columns or on not in other._columns:
            raise KeyError(on)
        if self._kinds[on] != other._kinds[on]:
            raise TypeError(f"polars_min の join の鍵の型が違う: {on} {self._kinds[on]} と {other._kinds[on]}")
        overlap = [column for column in other._columns if column != on and column in self._columns]
        if overlap:
            raise NotImplementedError("polars_min の join は重なる列に接尾辞を付けない: " + ", ".join(overlap))
        # null の鍵は null 同士でも一致しない。左の行順は保つ。
        buckets = {}
        for row in other._rows:
            key = row[on]
            if key is None:
                continue
            buckets.setdefault(key, []).append(row)
        right_cols = [column for column in other._columns if column != on]
        out = []
        for row in self._rows:
            matches = buckets.get(row[on]) if row[on] is not None else None
            if not matches:
                copied = dict(row)
                for column in right_cols:
                    copied[column] = None
                out.append(copied)
            else:
                for match in matches:
                    copied = dict(row)
                    for column in right_cols:
                        copied[column] = match[column]
                    out.append(copied)
        kinds = dict(self._kinds)
        for column in right_cols:
            kinds[column] = other._kinds[column]
        return DataFrame._frame(list(self._columns) + right_cols, out, kinds)

    def iter_rows(self, *, named=False, buffer_size=512):
        if named is not True or buffer_size != 512:
            raise NotImplementedError("polars_min の iter_rows は named=True だけ")
        for row in self._rows:
            yield {column: row[column] for column in self._columns}

    def __getattr__(self, name):
        raise AttributeError("DataFrame." + name)

    def __repr__(self):
        return f"polars_min.DataFrame(height={self.height}, columns={self._columns})"


def col(name):
    if not isinstance(name, str):
        raise TypeError("polars_min の col は文字列の列名だけ")
    return Expr(lambda row, name=name: row[name], False)


def lit(value):
    return Expr(lambda row, value=value: value, True)


def _kind(value):
    if type(value) is bool:
        return "bool"
    if type(value) is int:
        return "int"
    if type(value) is float:
        return "float"
    if type(value) is str:
        return "str"
    return type(value).__name__


def read_ndjson(source):
    """オブジェクトの NDJSON。空行は飛ばす。列の型は null を除いて一種類（int と float だけは float）。"""
    text = Path(source).read_text(encoding="utf-8")
    raw = []
    for line in text.splitlines():
        if not line.strip():
            continue
        obj = json.loads(line)
        if not isinstance(obj, dict):
            raise ValueError("polars_min の read_ndjson はオブジェクトの行だけ")
        raw.append(obj)
    if not raw:
        raise ValueError("polars_min は空の NDJSON の型を推論しない")
    columns = []
    seen = set()
    for obj in raw:
        for key in obj:
            if key not in seen:
                seen.add(key)
                columns.append(key)
    values = {c: [] for c in columns}
    for obj in raw:
        for c in columns:
            value = obj[c] if c in obj else None
            if value is not None and _kind(value) not in {"bool", "int", "float", "str"}:
                raise NotImplementedError(f"polars_min が読まない値: {c} {_kind(value)}")
            values[c].append(value)
    for c, vals in values.items():
        kinds = {_kind(v) for v in vals if v is not None}
        if kinds <= {"int", "float"} and "float" in kinds:
            values[c] = [None if v is None else float(v) for v in vals]
        elif kinds <= {"bool"} or kinds <= {"int"} or kinds <= {"float"} or kinds <= {"str"} or not kinds:
            continue
        else:
            raise TypeError(f"polars_min は列 {c} の型を混ぜない: {sorted(kinds)}")
    rows = [{c: values[c][i] for c in columns} for i in range(len(raw))]
    kinds = {}
    for c, vals in values.items():
        found = {_kind(v) for v in vals if v is not None}
        kinds[c] = next(iter(found)) if len(found) == 1 else ("null" if not found else "mixed")
    return DataFrame._frame(columns, rows, kinds)

__all__ = ['DataFrame', 'Expr', 'Series', 'col', 'lit', 'read_ndjson']
__version__ = "0+min"
