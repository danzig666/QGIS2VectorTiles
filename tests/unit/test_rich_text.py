"""Simple HTML of long published texts (rich_text.py): only headings,
paragraphs, lists, emphasis and plain tables survive, without attributes."""

from publishing.rich_text import clean_html


def test_scripts_attributes_and_links_go():
    html = ('<h1 onclick="x()">12. §</h1><p style="color:red">Az <b>épület</b> &lt;max&gt; '
            '<a href="javascript:alert(1)">link</a><script>alert(1)</script><img src=x onerror=y></p>'
            '<iframe src="https://evil.example"><p>inside</p></iframe><style>p{}</style>')
    assert clean_html(html) == "<h3>12. §</h3><p>Az <b>épület</b> &lt;max&gt; link</p>"


def test_implicit_ends_spans_and_empty_input():
    assert clean_html("<ul><li>a) egy<li>b) kettő</ul><p>x<p>y") == \
        "<ul><li>a) egy</li><li>b) kettő</li></ul><p>x</p><p>y</p>"
    assert clean_html('<table><tr><th colspan="2" class="x">A</th><td rowspan="x">b<td rowspan="3">c'
                      '</table><br/>') == \
        '<table><tr><th colspan="2">A</th><td>b</td><td rowspan="3">c</td></tr></table><br>'
    assert clean_html(None) == "" and clean_html("   ") == ""
    assert clean_html("<p>unclosed <strong>bold") == "<p>unclosed <strong>bold</strong></p>"
