"""Local, best-effort credential redaction; never executes or sends input.

Rules identify credential shapes and explicit sensitive fields, never arbitrary
high-entropy text. Unrecognized formats and secrets without context can survive;
review exports before sharing. A truncated private PEM block consumes the rest
of the input rather than retaining possible private key material.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import TypeAlias


JSONValue: TypeAlias = (
    str | int | float | bool | None | list["JSONValue"] | dict[str, "JSONValue"]
)


_MARKER = re.compile(r"\[REDACTED:[a-z_]+\]")
_AUTHORIZATION_LINE = re.compile(
    r"^[ \t]*(?:proxy-)?authorization[ \t]*:[ \t]*(?P<value>[^\r\n]+)",
    re.IGNORECASE | re.MULTILINE,
)
_AUTHORIZATION_QUOTED = re.compile(
    r"\b(?:proxy-)?authorization[\"']?[ \t]*:[ \t]*"
    r"(?:\"(?P<double>(?:\\.|[^\"\\])*)\"|'(?P<single>(?:\\.|[^'\\])*)')",
    re.IGNORECASE,
)
_AUTHORIZATION = re.compile(
    r"\b(?:proxy-)?authorization[\"']?[ \t]*:[ \t]*[\"']?"
    r"(?P<value>(?:Bearer|Basic|Token)[ \t]+[^\s\"',;}]+)",
    re.IGNORECASE,
)


_URL_USERINFO = re.compile(
    r"\b[A-Za-z][A-Za-z0-9+.-]*://(?P<value>[^\s/\"'<>?#]+)@"
    r"(?=[^\s/\"'<>?#]+)"
)


_SECRET_NAME = (
    r"(?:[a-z0-9]+[_-])*"
    r"(?:password|passwd|pwd|token|api[ _-]*key|secret|client[ _-]*secret|"
    r"access[ _-]*token|refresh[ _-]*token|secret[ _-]*access[ _-]*key|"
    r"[a-z0-9]*private[ _-]*key)"
)
_SECRET_LINE = re.compile(
    r"^[ \t]*" + _SECRET_NAME
    + r"[ \t]*:[ \t]*(?![ \t'\"{\[\r\n])(?P<value>[^\r\n#]+?)"
    r"(?=[ \t]*(?:#|$))",
    re.IGNORECASE | re.MULTILINE,
)
_ASSIGNMENT = re.compile(
    r"(?<![\w])(?P<key_quote>['\"])?" + _SECRET_NAME
    + r"(?(key_quote)(?P=key_quote)\s*:\s*|(?![\w])[ \t]*[:=][ \t]*)"
    r"(?:\"(?P<double>(?:\\.|[^\"\\])*)\"|"
    r"'(?P<single>(?:\\.|[^'\\])*)'|"
    r"(?P<bare>(?:\[REDACTED:[a-z_]+\]|[^\s,;#&}\]\"'])+))",
    re.IGNORECASE,
)


_PATTERNS = (
    ("private_key", re.compile(
        r"-----BEGIN (?P<pem_label>(?:[A-Z0-9]+ )*PRIVATE KEY)-----"
        r"[\s\S]*?(?:-----END (?P=pem_label)-----|\Z)"
    )),
    ("github", re.compile(
        r"(?<![\w])(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})(?![\w])"
    )),
    ("openai", re.compile(
        r"(?<![\w])sk-(?:(?:proj|svcacct)-)?[A-Za-z0-9_-]{20,}(?![\w-])"
    )),
    ("google", re.compile(r"(?<![\w])AIza[A-Za-z0-9_-]{35}(?![\w-])")),
    ("aws_access_key", re.compile(r"(?<![\w])(?:AKIA|ASIA)[A-Z0-9]{16}(?![\w])")),
    ("jwt", re.compile(
        r"(?<![\w-])eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+(?![\w-])"
    )),
    ("telegram", re.compile(
        r"(?:(?<![\w])|(?<=/bot))\d{6,12}:[A-Za-z0-9_-]{30,}(?![\w-])"
    )),
)


@dataclass
class RedactionResult:
    text: str
    counts: dict[str, int]

    @property
    def total(self) -> int:
        return sum(self.counts.values())


def redact(text: str) -> RedactionResult:
    """Return sanitized text and counts containing only credential kinds."""
    if not isinstance(text, str):
        raise TypeError("redact expects a string")
    spans: list[tuple[int, int, str]] = []
    markers = [match.span() for match in _MARKER.finditer(text)]
    for kind, pattern in (
        ("authorization", _AUTHORIZATION_LINE),
        ("authorization", _AUTHORIZATION), ("url_credentials", _URL_USERINFO),
        ("secret", _SECRET_LINE),
    ):
        spans.extend((*match.span("value"), kind) for match in pattern.finditer(text))
    for match in _AUTHORIZATION_QUOTED.finditer(text):
        group = "double" if match["double"] is not None else "single"
        if match[group]:
            spans.append((*match.span(group), "authorization"))
    for match in _ASSIGNMENT.finditer(text):
        group = next(name for name in ("double", "single", "bare") if match[name] is not None)
        start, end = match.span(group)
        if start != end:
            spans.append((start, end, "secret"))
    for kind, pattern in _PATTERNS:
        spans.extend((*match.span(), kind) for match in pattern.finditer(text))

    # Count the union of overlapping matches once. The enclosing/earlier rule
    # supplies its safe kind label; no input content becomes report metadata.
    spans.sort(key=lambda span: (span[0], -span[1]))
    accepted: list[tuple[int, int, str]] = []
    for start, end, kind in spans:
        if any(left <= start and end <= right for left, right in markers):
            continue
        if accepted and start < accepted[-1][1]:
            left, right, previous_kind = accepted[-1]
            accepted[-1] = (left, max(right, end), previous_kind)
            continue
        accepted.append((start, end, kind))

    parts: list[str] = []
    counts: dict[str, int] = {}
    position = 0
    for start, end, kind in accepted:
        parts.extend((text[position:start], f"[REDACTED:{kind}]"))
        counts[kind] = counts.get(kind, 0) + 1
        position = end
    parts.append(text[position:])
    return RedactionResult("".join(parts), counts)


def redact_value(value: JSONValue) -> tuple[JSONValue, dict[str, int]]:
    """Return a recursively sanitized JSON copy with kind-only redaction counts.

    A sensitive field marks every nonempty scalar beneath it as a credential;
    empty strings and null stay empty. Keys are sanitized too; numeric suffixes
    resolve collisions without discarding values or recording original keys.
    """
    counts: dict[str, int] = {}

    def visit(item, sensitive=False):
        if not isinstance(item, (dict, list, str, int, float, bool, type(None))):
            raise TypeError("redact_value expects JSON-compatible values")
        if isinstance(item, dict):
            if any(not isinstance(key, str) for key in item):
                raise TypeError("redact_value expects string dictionary keys")
            sanitized = {}
            reserved = set(item)
            for key, child in item.items():
                safe_key = visit(key)
                if safe_key != key:
                    base = safe_key
                    suffix = 2
                    while safe_key in sanitized or safe_key in reserved:
                        safe_key = f"{base} ({suffix})"
                        suffix += 1
                sanitized[safe_key] = visit(child, sensitive or bool(re.fullmatch(
                    _SECRET_NAME + r"|(?:proxy[ _-]*)?authorization", key, re.IGNORECASE
                )))
            return sanitized
        if isinstance(item, list):
            return [visit(child, sensitive) for child in item]
        if sensitive and item is not None and item != "":
            if isinstance(item, str) and _MARKER.fullmatch(item):
                return item
            counts["secret"] = counts.get("secret", 0) + 1
            return "[REDACTED:secret]"
        if isinstance(item, str):
            result = redact(item)
            for kind, count in result.counts.items():
                counts[kind] = counts.get(kind, 0) + count
            return result.text
        return item

    return visit(value), counts
