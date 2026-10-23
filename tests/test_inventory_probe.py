from scripts.inventory_probe import LinkParser


def test_link_parser_resolves_text_and_preserves_href() -> None:
    parser = LinkParser()
    parser.feed('<nav><a href="/reports"> Annual <strong>reports</strong> </a></nav>')

    assert parser.links == [("Annual reports", "/reports")]
    assert parser.tag_counts == {"nav": 1, "a": 1, "strong": 1}
