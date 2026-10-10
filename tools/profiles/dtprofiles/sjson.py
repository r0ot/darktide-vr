"""Darktide's user_settings.config: read, change and write back exactly.

The file is the engine's SJSON dialect as the game writes it: `key = value`
lines, tab indentation, LF line endings, tables in braces, arrays in brackets
with one item per line, and keys in sorted order. Values are strings, numbers
and booleans; keys are bare or, when they hold a path, quoted.

Every scalar keeps the text it was read from, so an unchanged document is
written back byte for byte. `parse` refuses anything it would not write back
exactly (`round_trips` says whether a text can be handled at all); callers
that cannot be sure leave the file alone.
"""
from __future__ import annotations

import re

_BARE_KEY = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_TOKEN = re.compile(r'\s*(?:(?P<string>"(?:[^"\\]|\\.)*")|(?P<punct>[={}\[\]])|'
                    r'(?P<word>[^\s={}\[\]"]+))')


class SjsonError(ValueError):
    pass


class Scalar:
    """A value with the exact text it was written as."""
    __slots__ = ("raw",)

    def __init__(self, raw: str):
        self.raw = raw

    @property
    def value(self):
        raw = self.raw
        if raw.startswith('"'):
            return re.sub(r"\\(.)", r"\1", raw[1:-1])
        if raw == "true":
            return True
        if raw == "false":
            return False
        try:
            return int(raw)
        except ValueError:
            pass
        try:
            return float(raw)
        except ValueError:
            return raw

    def __eq__(self, other):
        return isinstance(other, Scalar) and other.raw == self.raw

    def __hash__(self):
        return hash(self.raw)

    def __repr__(self):
        return f"Scalar({self.raw})"


def scalar(value) -> Scalar:
    """The text the game would write for a Python value."""
    if isinstance(value, Scalar):
        return value
    if isinstance(value, bool):
        return Scalar("true" if value else "false")
    if isinstance(value, int):
        return Scalar(str(value))
    if isinstance(value, float):
        text = repr(value)
        return Scalar(text[:-2] if text.endswith(".0") else text)
    if isinstance(value, str):
        return Scalar('"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"')
    raise SjsonError(f"no SJSON form for {value!r}")


class Table(dict):
    """An ordered table; remembers which keys were written quoted."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.quoted: set[str] = set()
        # What precedes the first key of a document: the game starts the file
        # with an empty line.
        self.prefix = "\n"

    def put(self, key: str, value) -> None:
        """Set `key`, keeping its place, or adding it where the game's sorted
        order puts it."""
        value = value if isinstance(value, (Table, list, Scalar)) else scalar(value)
        if key in self:
            self[key] = value
            return
        items = list(self.items())
        index = next((i for i, (name, _) in enumerate(items) if name > key), len(items))
        items.insert(index, (key, value))
        self.clear()
        self.update(items)

    def copy(self) -> "Table":
        result = Table((key, deep_copy(value)) for key, value in self.items())
        result.quoted = set(self.quoted)
        result.prefix = self.prefix
        return result


def deep_copy(value):
    if isinstance(value, Table):
        return value.copy()
    if isinstance(value, list):
        return [deep_copy(item) for item in value]
    return value


def _tokens(text: str):
    position = 0
    for line_number, line in enumerate(text.split("\n"), 1):
        position = 0
        while position < len(line):
            match = _TOKEN.match(line, position)
            if match is None or match.end() == position:
                if line[position:].strip() == "":
                    break
                raise SjsonError(f"line {line_number}: cannot read {line[position:]!r}")
            position = match.end()
            kind = match.lastgroup
            yield kind, match.group(kind), line_number


def parse(text: str) -> Table:
    """The document in `text`; raises SjsonError unless `dumps` gives it
    back unchanged."""
    tokens = list(_tokens(text))
    index = 0

    def take():
        nonlocal index
        if index >= len(tokens):
            raise SjsonError("unexpected end of file")
        token = tokens[index]
        index += 1
        return token

    def value_from(token):
        kind, text_, line = token
        if kind == "punct":
            if text_ == "{":
                return table_until("}")
            if text_ == "[":
                return array()
            raise SjsonError(f"line {line}: unexpected {text_!r}")
        return Scalar(text_)

    def array():
        items = []
        while True:
            token = take()
            if token[0] == "punct" and token[1] == "]":
                return items
            items.append(value_from(token))

    def table_until(closer):
        table = Table()
        while True:
            if closer is None and index >= len(tokens):
                return table
            kind, key, line = take()
            if kind == "punct" and key == closer:
                return table
            if kind == "string":
                name = Scalar(key).value
                table.quoted.add(name)
            elif kind == "word":
                name = key
            else:
                raise SjsonError(f"line {line}: expected a key, found {key!r}")
            equals = take()
            if equals[:2] != ("punct", "="):
                raise SjsonError(f"line {line}: expected '=' after {name!r}")
            if name in table:
                raise SjsonError(f"line {line}: {name!r} twice")
            table[name] = value_from(take())

    document = table_until(None)
    document.prefix = text[:len(text) - len(text.lstrip())]
    if dumps(document) != text:
        raise SjsonError("the file is not in the form the game writes; left alone")
    return document


def round_trips(text: str) -> bool:
    try:
        parse(text)
        return True
    except SjsonError:
        return False


def _key(table: Table, name: str) -> str:
    if name in table.quoted or not _BARE_KEY.match(name):
        return scalar(name).raw
    return name


def _emit(value, depth: int, lead: str, out: list[str]) -> None:
    indent = "\t" * depth
    if isinstance(value, Table):
        out.append(f"{indent}{lead}{{")
        for name, item in value.items():
            _emit(item, depth + 1, f"{_key(value, name)} = ", out)
        out.append(f"{indent}}}")
    elif isinstance(value, list):
        out.append(f"{indent}{lead}[")
        for item in value:
            _emit(item, depth + 1, "", out)
        out.append(f"{indent}]")
    elif isinstance(value, Scalar):
        out.append(f"{indent}{lead}{value.raw}")
    else:
        out.append(f"{indent}{lead}{scalar(value).raw}")


def dumps(document: Table) -> str:
    out: list[str] = []
    for name, value in document.items():
        _emit(value, 0, f"{_key(document, name)} = ", out)
    return document.prefix + "\n".join(out) + "\n"
