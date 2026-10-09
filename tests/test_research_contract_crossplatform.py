from pathlib import Path

from display_project.research_contract import sha256_canonical_text


def test_canonical_text_hash_is_newline_stable(tmp_path: Path) -> None:
    lf_path = tmp_path / "lf.txt"
    crlf_path = tmp_path / "crlf.txt"
    lf_path.write_bytes(b"alpha\nbeta\n")
    crlf_path.write_bytes(b"alpha\r\nbeta\r\n")

    assert sha256_canonical_text(lf_path) == sha256_canonical_text(crlf_path)
