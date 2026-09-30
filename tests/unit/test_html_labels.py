from fidelity.html_labels import MAX_SECTIONS, SUB_SUP_SCALE, format_expression, html_sections


def test_sub_and_mismatched_close_as_qt():
    """Szerkezeti változás területtel: '<sub>..</small>' — the closing tag
    ends the innermost size change, like Qt."""
    assert html_sections("1ő→1ő\n<sub>4.35 ha</small>") == [
        ("1ő→1ő\n", 1.0), ("4.35 ha", round(SUB_SUP_SCALE, 4))]


def test_breaks_entities_and_unknown_tags():
    assert html_sections("a<br>b &amp; <b>c</b>") == [("a\nb & c", 1.0)]
    assert html_sections(None) == [] and html_sections("x") == [("x", 1.0)]


def test_sections_are_capped_and_format_lists_them_all():
    text = "".join(f"n{i}<sub>s{i}</sub>" for i in range(10))
    sections = html_sections(text)
    assert len(sections) == MAX_SECTIONS
    assert "".join(t for t, _ in sections) == "".join(f"n{i}s{i}" for i in range(10))
    expression = format_expression()
    assert expression[0] == "format" and len(expression) == 1 + 2 * MAX_SECTIONS
