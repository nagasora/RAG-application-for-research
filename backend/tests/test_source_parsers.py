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
