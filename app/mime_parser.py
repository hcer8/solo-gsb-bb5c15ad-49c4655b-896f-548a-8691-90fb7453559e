"""RFC 2046 multipart message parser and re-encoder.

The parser works on ``str``. Input may use CRLF or LF line endings (bare LF
is tolerated and reported); re-encoded output always uses canonical CRLF as
required by RFC 2046 section 5.1.1.

Internally all text blocks (bodies, preambles, raw header blocks) are stored
LF-normalized; :func:`encode_message` normalizes everything back to CRLF.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

CRLF = "\r\n"
LF = "\n"

# RFC 2046 section 5.1.1:
#   boundary := 0*69<bchars> bcharsnospace
#   bchars := bcharsnospace / " "
BCHARS_NOSPACE = set(
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789"
    "'()+_,-./:=?"
)
TRANSPORT_PADDING = frozenset(" \t")
MAX_BOUNDARY_LEN = 70
MAX_DEPTH = 50

_LINE_SPLIT_RE = re.compile(r"\r\n|\n")
_BARE_LF_RE = re.compile(r"(?<!\r)\n")
# field-name = 1*ftext ; ftext = %d33-57 / %d59-126 (printable ASCII except ':')
_HEADER_NAME_RE = re.compile(r"[!-9;-~]+")


def split_lines(text):
    """Split text on CRLF or LF. Returns ``(lines, eol_kind)``."""
    crlf_count = text.count(CRLF)
    bare_lf = len(_BARE_LF_RE.findall(text))
    if crlf_count and bare_lf:
        eol = "mixed"
    elif crlf_count:
        eol = "crlf"
    elif bare_lf:
        eol = "lf"
    else:
        eol = "none"
    return _LINE_SPLIT_RE.split(text), eol


@dataclass
class Issue:
    level: str          # "error" | "warning" | "info"
    message: str
    path: str = ""      # part path like "2.1"; "" means top level


@dataclass
class Header:
    name: str
    value: str
    raw: str
    valid: bool = True


@dataclass
class Part:
    path: str
    raw_headers: str
    headers: list = field(default_factory=list)
    content_type: str = ""
    ct_params: dict = field(default_factory=dict)
    body: "str | None" = None            # leaf parts only
    multipart: "MultipartNode | None" = None
    issues: list = field(default_factory=list)


@dataclass
class MultipartNode:
    boundary: str
    preamble: str = ""
    epilogue: str = ""
    parts: list = field(default_factory=list)
    issues: list = field(default_factory=list)


# ---------------------------------------------------------------------------
# Content-Type / parameter parsing (RFC 2045 section 5.1, quoted-string)
# ---------------------------------------------------------------------------

def split_quoted(value, sep=";"):
    """Split on ``sep`` occurrences that are outside quoted-strings."""
    segments = []
    buf = []
    in_quote = False
    escaped = False
    for ch in value:
        if escaped:
            buf.append(ch)
            escaped = False
        elif ch == "\\" and in_quote:
            buf.append(ch)
            escaped = True
        elif ch == '"':
            buf.append(ch)
            in_quote = not in_quote
        elif ch == sep and not in_quote:
            segments.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    segments.append("".join(buf))
    return segments


def unquote(value):
    """Unquote a quoted-string. Returns ``(text, ok)``."""
    if not value.startswith('"'):
        return value, True
    out = []
    i = 1
    while i < len(value):
        ch = value[i]
        if ch == "\\" and i + 1 < len(value):
            out.append(value[i + 1])
            i += 2
            continue
        if ch == '"':
            rest = value[i + 1:].strip()
            return "".join(out), rest == ""
        out.append(ch)
        i += 1
    return "".join(out), False  # unterminated quote


def parse_content_type(value):
    """Parse a Content-Type header value.

    Returns ``(maintype, params, issues)``; maintype is lowercased and may be
    ``""`` when the value is empty.
    """
    issues = []
    params = {}
    segments = split_quoted(value or "")
    maintype = segments[0].strip().lower()
    if maintype and "/" not in maintype:
        issues.append(Issue("error", f"Content-Type 主类型缺少 '/': {maintype!r}"))
    for seg in segments[1:]:
        seg = seg.strip()
        if not seg:
            continue
        if "=" not in seg:
            issues.append(Issue("warning", f"忽略无效参数 {seg!r}（缺少 '='）"))
            continue
        name, _, raw_val = seg.partition("=")
        name = name.strip().lower()
        raw_val = raw_val.strip()
        if not name:
            issues.append(Issue("warning", f"忽略无名参数 {seg!r}"))
            continue
        if raw_val.startswith('"'):
            val, ok = unquote(raw_val)
            if not ok:
                issues.append(Issue("warning", f"参数 {name!r} 的引号未闭合或引号后有多余内容"))
        else:
            val = raw_val
        if name in params:
            issues.append(Issue("warning", f"参数 {name!r} 重复出现，后者覆盖前者"))
        params[name] = val
    return maintype, params, issues


def check_boundary(boundary, path=""):
    """Validate a boundary string against RFC 2046 section 5.1.1."""
    issues = []
    if not boundary:
        issues.append(Issue("error", "boundary 为空", path))
        return issues
    if len(boundary) > MAX_BOUNDARY_LEN:
        issues.append(Issue(
            "warning",
            f"boundary 长度为 {len(boundary)}，超过 RFC 2046 上限 70 字符",
            path,
        ))
    if boundary[-1] == " ":
        issues.append(Issue("error", "boundary 不能以空格结尾", path))
    bad = sorted({c for c in boundary if c not in BCHARS_NOSPACE and c != " "})
    if bad:
        issues.append(Issue(
            "warning",
            f"boundary 包含 RFC 2046 未定义的字符: {''.join(bad)!r}",
            path,
        ))
    return issues


# ---------------------------------------------------------------------------
# Header parsing
# ---------------------------------------------------------------------------

def parse_headers(header_lines, path=""):
    """Parse and unfold RFC 822 header lines. Returns ``(headers, issues)``."""
    issues = []
    unfolded = []
    for line in header_lines:
        if line[:1] in (" ", "\t"):
            if unfolded:
                unfolded[-1] += "\n" + line
            else:
                issues.append(Issue("error", f"头部块以续行（空白开头）起始: {line!r}", path))
                unfolded.append(line)
        else:
            unfolded.append(line)

    headers = []
    for raw in unfolded:
        lines_of = raw.split("\n")
        first = lines_of[0]
        if ":" not in first:
            issues.append(Issue("error", f"头部行缺少 ':'，无法解析: {first!r}", path))
            headers.append(Header(name="", value="", raw=raw, valid=False))
            continue
        name, _, first_val = first.partition(":")
        name = name.strip()
        if not name or not _HEADER_NAME_RE.fullmatch(name):
            issues.append(Issue("warning", f"头部名称包含非法字符: {name!r}", path))
        value_parts = [first_val.strip()] + [ln.strip() for ln in lines_of[1:]]
        value = " ".join(p for p in value_parts if p)
        headers.append(Header(name=name, value=value, raw=raw, valid=True))
    return headers, issues


# ---------------------------------------------------------------------------
# Multipart parsing (RFC 2046 section 5.1.1)
# ---------------------------------------------------------------------------

def match_delimiter(line, boundary):
    """Classify a line against a boundary.

    Returns ``"open"``, ``"close"`` or ``None``. Transport padding
    (trailing spaces/tabs) is allowed after the delimiter.
    """
    delim = "--" + boundary
    for kind, marker in (("close", delim + "--"), ("open", delim)):
        if line == marker:
            return kind
        if line.startswith(marker) and all(
            ch in TRANSPORT_PADDING for ch in line[len(marker):]
        ):
            return kind
    return None


def parse_multipart(lines, boundary, parent_path="", depth=0):
    """Parse body lines into a MultipartNode using ``boundary``."""
    node = MultipartNode(boundary=boundary)
    preamble_lines = []
    epilogue_lines = []
    raw_parts = []
    cur = None
    state = "preamble"  # preamble | in_part | epilogue

    for line in lines:
        kind = match_delimiter(line, boundary) if state != "epilogue" else None
        if kind == "close":
            if cur is not None:
                raw_parts.append(cur)
                cur = None
            state = "epilogue"
        elif kind == "open":
            if cur is not None:
                raw_parts.append(cur)
            cur = {"header_lines": [], "body_lines": [], "in_body": False}
            state = "in_part"
        elif state == "preamble":
            preamble_lines.append(line)
        elif state == "epilogue":
            epilogue_lines.append(line)
        elif not cur["in_body"]:
            if line == "":
                cur["in_body"] = True
            else:
                cur["header_lines"].append(line)
        else:
            cur["body_lines"].append(line)

    if cur is not None:
        raw_parts.append(cur)

    if not raw_parts and state == "preamble":
        node.issues.append(Issue(
            "error", f"正文中未找到边界分隔线 --{boundary}", parent_path))
    elif state != "epilogue":
        node.issues.append(Issue(
            "error", f"缺少结束边界线 --{boundary}--", parent_path))

    node.preamble = LF.join(preamble_lines)
    node.epilogue = LF.join(epilogue_lines)
    if node.preamble.strip():
        node.issues.append(Issue(
            "info", "存在前言（preamble），按 RFC 2046 应被接收方忽略", parent_path))
    if node.epilogue.strip():
        node.issues.append(Issue(
            "info", "存在尾声（epilogue），按 RFC 2046 应被接收方忽略", parent_path))

    for i, raw in enumerate(raw_parts, 1):
        path = f"{parent_path}.{i}" if parent_path else str(i)
        node.parts.append(_build_part(raw, path, depth))
    return node


def _build_part(raw, path, depth):
    issues = []
    header_lines = raw["header_lines"]
    body_lines = raw["body_lines"]

    headers, h_issues = parse_headers(header_lines, path)
    issues.extend(h_issues)

    ct_headers = [h for h in headers if h.name.lower() == "content-type"]
    content_type = ""
    ct_params = {}
    multipart = None
    body = None

    if ct_headers:
        if len(ct_headers) > 1:
            issues.append(Issue("warning", "部件包含多个 Content-Type 头，使用第一个", path))
        content_type, ct_params, ct_issues = parse_content_type(ct_headers[0].value)
        for issue in ct_issues:
            issue.path = path
        issues.extend(ct_issues)
    else:
        issues.append(Issue(
            "info", "部件缺少 Content-Type 头，按 RFC 2046 缺省视为 text/plain", path))
        content_type = "text/plain"

    if content_type.startswith("multipart/"):
        sub_boundary = ct_params.get("boundary", "")
        if not sub_boundary:
            issues.append(Issue(
                "error", "multipart 部件缺少 boundary 参数，内容按普通正文处理", path))
            body = LF.join(body_lines)
        elif depth >= MAX_DEPTH:
            issues.append(Issue("error", "嵌套层级过深，停止递归解析", path))
            body = LF.join(body_lines)
        else:
            issues.extend(check_boundary(sub_boundary, path))
            multipart = parse_multipart(body_lines, sub_boundary, path, depth + 1)
    else:
        body = LF.join(body_lines)

    return Part(
        path=path,
        raw_headers=LF.join(header_lines),
        headers=headers,
        content_type=content_type,
        ct_params=ct_params,
        body=body,
        multipart=multipart,
        issues=issues,
    )


# ---------------------------------------------------------------------------
# Top-level entry points
# ---------------------------------------------------------------------------

def normalize_boundary(raw):
    """Normalize a user-supplied boundary (tolerates 'boundary=' and quotes)."""
    b = (raw or "").strip()
    if b.lower().startswith("boundary="):
        b = b.split("=", 1)[1].strip()
    if b.startswith('"'):
        b, _ = unquote(b)
    return b


def parse_message(content_type_value, boundary_override, body, mode="body"):
    """Parse a multipart message. Returns a JSON-serializable dict."""
    issues = []
    message_headers = None

    lines, eol = split_lines(body or "")
    if eol == "lf":
        issues.append(Issue(
            "warning", "报文使用 LF 换行；RFC 2046 要求 CRLF（重新编码时将统一为 CRLF）"))
    elif eol == "mixed":
        issues.append(Issue(
            "warning", "报文混用 CRLF 与 LF 换行（重新编码时将统一为 CRLF）"))

    if mode == "message":
        try:
            split_at = lines.index("")
        except ValueError:
            split_at = None
        if split_at is None:
            issues.append(Issue("error", "完整报文模式下未找到头部与正文之间的空行"))
            header_lines, lines = lines, []
        else:
            header_lines, lines = lines[:split_at], lines[split_at + 1:]
        message_headers = LF.join(header_lines)
        _, h_issues = parse_headers(header_lines, "")
        issues.extend(h_issues)
        if not (content_type_value or "").strip():
            top_headers, _ = parse_headers(header_lines, "")
            ct_header = next(
                (h for h in top_headers if h.name.lower() == "content-type"), None)
            if ct_header is not None:
                content_type_value = ct_header.value

    maintype, params, ct_issues = parse_content_type(content_type_value or "")
    issues.extend(ct_issues)

    boundary = normalize_boundary(boundary_override) or params.get("boundary", "")

    fatal = False
    if not maintype:
        issues.append(Issue("error", "缺少 Content-Type，无法解析"))
        fatal = True
    elif not maintype.startswith("multipart/"):
        issues.append(Issue(
            "error", f"顶层 Content-Type 为 {maintype!r}，不是 multipart/*，无法按 multipart 解析"))
        fatal = True
    if not boundary:
        issues.append(Issue(
            "error", "未提供 boundary（既无显式输入，Content-Type 中也无 boundary 参数）"))
        fatal = True
    else:
        issues.extend(check_boundary(boundary))
        if any(i.level == "error" for i in issues if "boundary" in i.message):
            fatal = True

    result = {
        "ok": False,
        "contentType": maintype,
        "params": params,
        "boundary": boundary,
        "lineEnding": eol,
        "messageHeaders": message_headers,
        "tree": None,
        "issues": [],
    }
    if fatal:
        result["issues"] = [_issue_dict(i) for i in issues]
        return result

    tree = parse_multipart(lines, boundary)
    all_issues = issues + _collect_issues(tree)
    result["ok"] = True
    result["tree"] = _node_dict(tree)
    result["issues"] = [_issue_dict(i) for i in all_issues]
    return result


def _collect_issues(node):
    out = list(node.issues)
    for part in node.parts:
        out.extend(part.issues)
        if part.multipart is not None:
            out.extend(_collect_issues(part.multipart))
    return out


# ---------------------------------------------------------------------------
# Re-encoding
# ---------------------------------------------------------------------------

def encode_node(node):
    """Encode a multipart node dict into a list of lines (without CRLF)."""
    lines = []
    preamble = node.get("preamble") or ""
    if preamble:
        lines.extend(split_lines(preamble)[0])
    boundary = node.get("boundary") or ""
    for part in node.get("parts") or []:
        lines.append("--" + boundary)
        raw_headers = part.get("rawHeaders") or ""
        if raw_headers.strip():
            lines.extend(split_lines(raw_headers)[0])
        lines.append("")
        sub = part.get("multipart")
        if sub:
            lines.extend(encode_node(sub))
        else:
            body_text = part.get("body")
            if body_text:
                lines.extend(split_lines(body_text)[0])
    lines.append("--" + boundary + "--")
    epilogue = node.get("epilogue") or ""
    if epilogue:
        lines.extend(split_lines(epilogue)[0])
    return lines


def encode_message(tree, message_headers=None):
    """Encode a (possibly edited) tree back into a canonical CRLF message.

    A trailing CRLF is appended only when there is no epilogue; otherwise the
    final newline would be re-absorbed into the epilogue on the next parse,
    breaking round-trip stability.
    """
    lines = []
    if message_headers and message_headers.strip():
        lines.extend(split_lines(message_headers)[0])
        lines.append("")
    lines.extend(encode_node(tree or {}))
    text = CRLF.join(lines)
    if not (tree or {}).get("epilogue"):
        text += CRLF
    return text


# ---------------------------------------------------------------------------
# Serialization helpers
# ---------------------------------------------------------------------------

def _issue_dict(issue):
    return {"level": issue.level, "message": issue.message, "path": issue.path}


def _header_dict(header):
    return {
        "name": header.name,
        "value": header.value,
        "raw": header.raw,
        "valid": header.valid,
    }


def _part_dict(part):
    return {
        "path": part.path,
        "rawHeaders": part.raw_headers,
        "headers": [_header_dict(h) for h in part.headers],
        "contentType": part.content_type,
        "contentTypeParams": part.ct_params,
        "isMultipart": part.multipart is not None,
        "body": part.body,
        "multipart": _node_dict(part.multipart) if part.multipart is not None else None,
        "issues": [_issue_dict(i) for i in part.issues],
    }


def _node_dict(node):
    return {
        "boundary": node.boundary,
        "preamble": node.preamble,
        "epilogue": node.epilogue,
        "parts": [_part_dict(p) for p in node.parts],
        "issues": [_issue_dict(i) for i in node.issues],
    }
