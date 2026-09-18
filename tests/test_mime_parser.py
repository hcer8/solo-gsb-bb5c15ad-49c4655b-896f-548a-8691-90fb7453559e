"""Tests for the RFC 2046 multipart parser and encoder."""

from app import mime_parser as mp


def parse(ct, body, boundary="", mode="body"):
    return mp.parse_message(ct, boundary, body, mode)


SIMPLE = (
    "--abc\r\n"
    "Content-Type: text/plain\r\n"
    "\r\n"
    "hello\r\n"
    "--abc\r\n"
    "Content-Type: text/html\r\n"
    "\r\n"
    "<b>hi</b>\r\n"
    "--abc--\r\n"
)

NESTED = (
    "preamble text\n"
    "--out\n"
    "Content-Type: text/plain\n"
    "\n"
    "first\n"
    "--out\n"
    "Content-Type: multipart/alternative; boundary=inn\n"
    "\n"
    "--inn\n"
    "Content-Type: text/plain\n"
    "\n"
    "plain version\n"
    "--inn\n"
    "Content-Type: text/html\n"
    "\n"
    "<b>html version</b>\n"
    "--inn--\n"
    "--out--\n"
    "epilogue text"
)


def errors(result):
    return [i for i in result["issues"] if i["level"] == "error"]


def test_simple_two_parts():
    r = parse("multipart/mixed; boundary=abc", SIMPLE)
    assert r["ok"]
    parts = r["tree"]["parts"]
    assert len(parts) == 2
    assert parts[0]["body"] == "hello"
    assert parts[0]["contentType"] == "text/plain"
    assert parts[1]["contentType"] == "text/html"
    assert parts[0]["path"] == "1" and parts[1]["path"] == "2"
    assert not errors(r)


def test_nested_multipart():
    r = parse("multipart/mixed; boundary=out", NESTED)
    assert r["ok"]
    tree = r["tree"]
    assert tree["preamble"] == "preamble text"
    assert tree["epilogue"] == "epilogue text"
    assert len(tree["parts"]) == 2
    nested = tree["parts"][1]
    assert nested["isMultipart"]
    assert nested["multipart"]["boundary"] == "inn"
    inner = nested["multipart"]["parts"]
    assert [p["path"] for p in inner] == ["2.1", "2.2"]
    assert inner[0]["body"] == "plain version"
    assert inner[1]["contentType"] == "text/html"
    assert not errors(r)


def test_quoted_boundary_with_semicolon():
    body = "--a;b\nContent-Type: text/plain\n\nhi\n--a;b--\n"
    r = parse('multipart/mixed; boundary="a;b"', body)
    assert r["ok"]
    assert r["boundary"] == "a;b"
    assert r["tree"]["parts"][0]["body"] == "hi"


def test_quoted_boundary_with_escape():
    body = '--a"b\n\nx\n--a"b--'
    r = parse(r'multipart/mixed; boundary="a\"b"', body)
    assert r["ok"]
    assert r["boundary"] == 'a"b'
    assert r["tree"]["parts"][0]["body"] == "x"


def test_lf_line_endings_warn():
    r = parse("multipart/mixed; boundary=abc", SIMPLE.replace("\r\n", "\n"))
    assert r["ok"]
    assert r["lineEnding"] == "lf"
    assert any(i["level"] == "warning" and "LF" in i["message"] for i in r["issues"])


def test_mixed_line_endings_warn():
    body = "--abc\r\n\nx\n--abc--\n"
    r = parse("multipart/mixed; boundary=abc", body)
    assert r["lineEnding"] == "mixed"
    assert any(i["level"] == "warning" and "混用" in i["message"] for i in r["issues"])


def test_missing_close_delimiter():
    r = parse("multipart/mixed; boundary=abc", "--abc\nContent-Type: text/plain\n\nhi\n")
    assert r["ok"]  # tree is still returned
    assert any("结束边界" in i["message"] for i in errors(r))


def test_boundary_not_found():
    r = parse("multipart/mixed; boundary=abc", "no delimiters here")
    assert r["ok"]
    assert any("未找到边界" in i["message"] for i in errors(r))


def test_malformed_header_line():
    r = parse("multipart/mixed; boundary=abc",
              "--abc\nContent-Type text/plain\n\nhi\n--abc--")
    assert any("缺少 ':'" in i["message"] for i in errors(r))
    part = r["tree"]["parts"][0]
    assert part["headers"][0]["valid"] is False


def test_header_folding():
    body = ("--abc\n"
            "X-Long: first\n"
            "\tsecond\n"
            "\n"
            "x\n"
            "--abc--")
    r = parse("multipart/mixed; boundary=abc", body)
    header = r["tree"]["parts"][0]["headers"][0]
    assert header["name"] == "X-Long"
    assert header["value"] == "first second"


def test_transport_padding_on_delimiters():
    r = parse("multipart/mixed; boundary=abc", "--abc  \t\n\nx\n--abc-- \t\n")
    assert r["ok"]
    assert len(r["tree"]["parts"]) == 1
    assert not errors(r)


def test_boundary_prefix_line_is_body_not_delimiter():
    r = parse("multipart/mixed; boundary=abc", "--abc\n\n--abcXYZ still body\n--abc--")
    assert r["tree"]["parts"][0]["body"] == "--abcXYZ still body"


def test_close_delimiter_not_confused_with_open():
    # boundary "ab": "--ab--" must be the close delimiter, not an open one
    r = parse("multipart/mixed; boundary=ab", "--ab\n\nx\n--ab--")
    assert len(r["tree"]["parts"]) == 1
    assert not errors(r)


def test_boundary_too_long_warns():
    b = "x" * 71
    r = parse(f"multipart/mixed; boundary={b}", f"--{b}\n\nx\n--{b}--")
    assert any(i["level"] == "warning" and "70" in i["message"] for i in r["issues"])


def test_boundary_invalid_chars_warn():
    r = parse('multipart/mixed; boundary="a(b)c)d"', "--a(b)c)d\n\nx\n--a(b)c)d--")
    assert r["ok"]
    # ')' is a valid bchar, but let's check a clearly invalid one
    r2 = parse('multipart/mixed; boundary="a@b"', "--a@b\n\nx\n--a@b--")
    assert any(i["level"] == "warning" and "未定义" in i["message"] for i in r2["issues"])


def test_multipart_part_missing_boundary_param():
    body = "--abc\nContent-Type: multipart/related\n\njust text\n--abc--"
    r = parse("multipart/mixed; boundary=abc", body)
    part = r["tree"]["parts"][0]
    assert not part["isMultipart"]
    assert part["body"] == "just text"
    assert any("缺少 boundary 参数" in i["message"] for i in errors(r))


def test_preamble_epilogue_info():
    r = parse("multipart/mixed; boundary=out", NESTED)
    levels = [i["level"] for i in r["issues"]]
    assert "info" in levels
    assert r["tree"]["preamble"] == "preamble text"
    assert r["tree"]["epilogue"] == "epilogue text"


def test_empty_body_part():
    r = parse("multipart/mixed; boundary=abc", "--abc\nContent-Type: text/plain\n\n--abc--")
    assert r["tree"]["parts"][0]["body"] == ""


def test_missing_content_type_fails():
    assert not parse("", "--abc\n\nx\n--abc--")["ok"]


def test_non_multipart_top_level_fails():
    r = parse("text/plain", "hello")
    assert not r["ok"]
    assert any("multipart" in i["message"] for i in errors(r))


def test_missing_boundary_fails():
    assert not parse("multipart/mixed", "--abc\n\nx\n--abc--")["ok"]


def test_boundary_override_wins():
    r = parse("multipart/mixed; boundary=wrong", "--right\n\nx\n--right--",
              boundary="right")
    assert r["ok"]
    assert r["boundary"] == "right"


def test_boundary_override_accepts_quoted_form():
    r = parse("multipart/mixed", "--abc\n\nx\n--abc--", boundary='boundary="abc"')
    assert r["ok"]
    assert r["boundary"] == "abc"


def test_message_mode_extracts_headers_and_ct():
    msg = (
        "From: alice@example.com\r\n"
        "Subject: demo\r\n"
        "Content-Type: multipart/mixed; boundary=xyz\r\n"
        "\r\n"
        "--xyz\r\n"
        "\r\n"
        "body\r\n"
        "--xyz--\r\n"
    )
    r = parse("", msg, mode="message")
    assert r["ok"]
    assert r["contentType"] == "multipart/mixed"
    assert r["boundary"] == "xyz"
    assert "From: alice@example.com" in r["messageHeaders"]
    encoded = mp.encode_message(r["tree"], r["messageHeaders"])
    assert encoded.startswith("From: alice@example.com\r\n")
    assert "Content-Type: multipart/mixed; boundary=xyz\r\n\r\n--xyz\r\n" in encoded


def test_roundtrip_is_stable():
    r = parse('multipart/mixed; boundary="out"', NESTED)
    assert r["ok"]
    encoded = mp.encode_message(r["tree"])
    assert "\r\n" in encoded
    r2 = parse('multipart/mixed; boundary="out"', encoded)
    assert r2["ok"]
    # second encode must be byte-identical
    assert mp.encode_message(r2["tree"]) == encoded
    # structure preserved
    p = r2["tree"]["parts"][1]["multipart"]["parts"][0]
    assert p["body"] == "plain version"


def test_encode_after_edit():
    r = parse("multipart/mixed; boundary=abc", SIMPLE)
    tree = r["tree"]
    tree["parts"][0]["body"] = "EDITED BODY"
    tree["parts"][1]["rawHeaders"] = "Content-Type: text/html; charset=utf-8"
    out = mp.encode_message(tree)
    assert "EDITED BODY" in out
    assert "Content-Type: text/html; charset=utf-8\r\n" in out
    assert out.count("--abc") == 3  # two opens + close prefix
    assert out.endswith("--abc--\r\n")


def test_encode_normalizes_to_crlf():
    r = parse("multipart/mixed; boundary=abc", SIMPLE.replace("\r\n", "\n"))
    out = mp.encode_message(r["tree"])
    assert "\n" not in out.replace("\r\n", "")
