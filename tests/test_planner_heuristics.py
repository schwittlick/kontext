from kontext.ingest.dedup import find_merges, signature_bytes
from kontext.ingest.extractors import extract_task
from kontext.ingest.planner import _dir_title, _looks_exploded

from conftest import ENGLISH, make_text_pdf


def files(*names):
    return [{"path": f"/dump/some dir/{n}"} for n in names]


def test_sequential_pages_are_exploded():
    assert _looks_exploded(files(*[f"page_{i}.html" for i in range(1, 12)]))


def test_common_prefix_is_exploded():
    assert _looks_exploded(files(*[f"treatise_split_{c}.html" for c in "abcdefgh"]))


def test_descriptive_names_are_a_collection():
    assert not _looks_exploded(files(
        "African Films - A Retrospective.htm",
        "Black Film Review - American cinema inside out.htm",
        "CONTEMPORARY AFRICAN FILM.htm",
        "Film in Africa and South Africa.htm",
        "Interview with Ben Zulu.htm",
        "The post-war cabinet.htm",
        "WorldViews - African cinema.htm",
        "Wend Kuuni and history.htm",
    ))


def test_few_files_never_exploded():
    assert not _looks_exploded(files("page_1.html", "page_2.html"))


def test_dir_title_skips_generic_names():
    assert _dir_title("/dump/01 Futurism/01 book/Other Modernism - Fiction of Power/files") \
        == "Other Modernism - Fiction of Power"
    assert _dir_title("/dump/An Exploded Treatise") == "An Exploded Treatise"
    # bare index dirs and generic names are skipped even when prefixed
    assert _dir_title("/dump/Kafka/02 texts") == "Kafka"


def test_office_lock_files_are_skipped(tmp_path):
    from kontext.ingest.planner import plan
    from kontext.survey import db as sdb
    from kontext.survey.worker import survey_one

    lock = tmp_path / "~$thesis.doc"
    lock.write_bytes(b"\xd0\xcf\x11\xe0" + b"\x00" * 64)
    conn = sdb.connect(tmp_path / "survey.db")
    st = lock.stat()
    sdb.upsert_files(conn, [survey_one(str(lock), st.st_size, st.st_mtime_ns)])
    tasks, skipped = plan(conn, set())
    assert tasks == []
    assert skipped["lock_artifact"] == 1


def test_dedup_length_ratio_guard():
    sig = signature_bytes([ENGLISH] * 8)
    books = [(1, sig, "epub", 700), (2, sig, "pdf", 9000)]
    # identical signatures, wildly different lengths -> boilerplate, not the same work
    assert find_merges(books, new_ids={2}) == []


def test_junk_embedded_titles_fall_back_to_filename(tmp_path):
    make_text_pdf(tmp_path / "The Actual Book Title.pdf", pages=2)
    task = {"kind": "pdf", "paths": [str(tmp_path / "The Actual Book Title.pdf")],
            "format": "pdf", "title": "Microsoft Word - final_v2.doc",
            "author": None, "language": None, "needs_ocr": False}
    result = extract_task(task)
    assert result["extraction"].title == "The Actual Book Title"


def test_needs_conversion_retries_when_tool_appears(tmp_path, monkeypatch):
    from kontext import catalog
    from kontext.ingest.runner import _clear_retryable_conversions
    from kontext.model import Extraction

    conn = catalog.connect(tmp_path / "kontext.db")
    catalog.insert_book(
        conn, Extraction(status="needs_conversion", source_format="doc", title="thesis"),
        files=[{"sha256": "abc", "path": "/dump/thesis.doc", "format": "doc",
                "size": 10, "role": "primary"}],
    )
    # a chm also waits as needs_conversion, but libreoffice can't ever take it
    catalog.insert_book(
        conn, Extraction(status="needs_conversion", source_format="doc", title="manual"),
        files=[{"sha256": "def", "path": "/dump/manual.CHM", "format": "doc",
                "size": 10, "role": "primary"}],
    )
    monkeypatch.setattr("kontext.ingest.runner.shutil.which",
                        lambda name: "/usr/bin/soffice" if "office" in name else None)
    assert _clear_retryable_conversions(conn) == 1
    # only the chm remains; the doc's hash is freed for re-planning
    remaining = conn.execute("SELECT title FROM books").fetchall()
    assert [r[0] for r in remaining] == ["manual"]
    assert conn.execute("SELECT COUNT(*) FROM book_files").fetchone()[0] == 1
    # a second run must not touch the chm again
    assert _clear_retryable_conversions(conn) == 0


def test_converter_env_strips_venv():
    import os
    import sys

    from kontext.ingest.extractors import _converter_env

    env = _converter_env()
    assert "VIRTUAL_ENV" not in env
    assert "PYTHONPATH" not in env
    assert not any(p.startswith(sys.prefix) for p in env["PATH"].split(os.pathsep) if p)


def test_mixed_pdf_gets_no_dedup_signature(tmp_path):
    make_text_pdf(tmp_path / "b.pdf", pages=6)
    task = {"kind": "pdf", "paths": [str(tmp_path / "b.pdf")], "format": "pdf",
            "title": None, "author": None, "language": None, "needs_ocr": True}
    result = extract_task(task)
    assert result["extraction"].status == "extracted"
    assert result["extraction"].needs_ocr is True
    assert result["minhash"] is None
