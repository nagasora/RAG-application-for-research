import pytest

from app.source_parsers import SourceParseLimitError, SourceParseLimits, parse_source


@pytest.mark.parametrize(("kind", "content", "expected_kind"), [
    ("latex", r"$x^2$\n\\begin{equation}y=mx\\end{equation}", "latex"),
    ("python", "def loss(x):\n    return x * x\n", "python"),
    ("notebook", '{"nbformat":4,"cells":[{"id":"a","cell_type":"code","execution_count":3,"source":["x=1\\n"]}]}', "notebook"),
    ("csv", "metric,value\nloss,0.2\n", "csv"),
    ("chat", '[{"role":"user","content":"test hypothesis"},{"role":"assistant","content":"try an experiment"}]', "chat"),
    ("markdown", "# Constraint\n\nThe loss must be bounded.", "markdown"),
])
def test_parsers_create_atomic_spans(kind, content, expected_kind):
    result = parse_source(kind, content)
    assert result.source_kind == expected_kind
    assert result.spans
    assert all(span.text for span in result.spans)


def test_python_spans_have_stable_ast_hash_and_lines():
    result = parse_source("python", "class Model:\n    pass\n")
    span = result.spans[0]
    assert len(span.metadata["ast_hash"]) == 64
    assert span.metadata["ast_hash"] == parse_source("python", "class Model:\n    pass\n").spans[0].metadata["ast_hash"]
    assert span.line_start == 1 and span.line_end == 2


def test_parser_rejects_excessive_input():
    with pytest.raises(SourceParseLimitError):
        parse_source("csv", "a\n1\n", limits=SourceParseLimits(max_input_bytes=2))


def test_chat_json_accepts_openai_text_content_blocks_and_legacy_strings():
    structured = parse_source("chat", '''{
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": "first "},
                {"type": "input_text", "text": "second"}
            ]
        }]
    }''')
    legacy = parse_source("chat", '[{"role":"assistant","content":["first ","second"]}]')

    assert structured.spans[0].text == "first second"
    assert structured.spans[0].metadata["role"] == "user"
    assert legacy.spans[0].text == "first second"


@pytest.mark.parametrize("content", [
    '[{"role":"user","content":[{"type":"image_url","image_url":"https://example.test/x"}]}]',
    '[{"role":"user","content":[{"type":"text","text":42}]}]',
    '[{"role":"user","content":[42]}]',
])
def test_chat_json_rejects_unknown_or_malformed_content_blocks(content):
    with pytest.raises(ValueError):
        parse_source("chat", content)
