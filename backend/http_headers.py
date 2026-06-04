import re
from urllib.parse import quote


_FILENAME_FALLBACK_RE = re.compile(r"[^A-Za-z0-9._-]+")


def attachment_content_disposition(
    filename: str, fallback_filename: str = "download"
) -> str:
    fallback = _FILENAME_FALLBACK_RE.sub("_", filename).strip("._")
    if not fallback or fallback != filename:
        fallback = fallback_filename

    fallback = fallback.replace("\\", "_").replace('"', "_")
    encoded = quote(filename, safe="")
    return f'attachment; filename="{fallback}"; filename*=UTF-8\'\'{encoded}'
