from larkmd_sync.core.doc_id import DocIdType, parse_doc_id


def test_document_url_preserves_arbitrary_tenant() -> None:
    url = "https://tenant.larksuite.com/wiki/Abcdefgh123"
    result = parse_doc_id(url)

    assert result.type is DocIdType.WIKI
    assert result.token == "Abcdefgh123"
    assert result.url == url


def test_token_uses_configured_tenant(monkeypatch) -> None:
    monkeypatch.setenv("LARK_BASE_URL", "https://tenant.feishu.cn/")

    assert parse_doc_id("Abcdefgh123").url == (
        "https://tenant.feishu.cn/docx/Abcdefgh123"
    )
