from fastapi.responses import Response

from backend.http_headers import attachment_content_disposition


def test_attachment_content_disposition_supports_utf8_filename() -> None:
    header = attachment_content_disposition("пир-тест.conf", "peer-56.conf")

    assert header == (
        'attachment; filename="peer-56.conf"; '
        "filename*=UTF-8''%D0%BF%D0%B8%D1%80-%D1%82%D0%B5%D1%81%D1%82.conf"
    )
    assert header.encode("latin-1")
    assert Response("ok", headers={"Content-Disposition": header}).raw_headers[0] == (
        b"content-disposition",
        header.encode("latin-1"),
    )
