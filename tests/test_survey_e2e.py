import json

from typer.testing import CliRunner

from kontext.cli import app
from kontext.survey import db as dbmod

runner = CliRunner()


def run_survey(dump, tmp_path, *extra):
    db = tmp_path / "survey.db"
    out = tmp_path / "report.json"
    result = runner.invoke(
        app, ["survey", str(dump), "--db", str(db), "--json-out", str(out), *extra],
    )
    assert result.exit_code == 0, result.output
    return db, out, result


def test_survey_end_to_end(dump, tmp_path):
    db, out, result = run_survey(dump, tmp_path)
    report = json.loads(out.read_text())

    assert report["totals"]["files"] == 10
    assert report["formats"]["pdf"]["count"] == 3
    assert report["formats"]["epub"]["count"] == 3
    assert report["formats"]["mobi"]["count"] == 2

    # scanned pdf shows up as ocr scope
    assert report["text_layer"]["scanned"]["files"] == 1
    assert report["scanned_pages_estimate"] == 3

    # the copied epub is one exact duplicate
    assert report["duplicates"]["redundant_files"] == 1

    # problems: one corrupt pdf, one drm'd azw3
    assert report["problems"]["corrupt"]["count"] == 1
    assert report["problems"]["drm"]["count"] == 1

    # archive with ebooks inside is surfaced
    assert report["archives"]["count"] == 1
    assert report["archives"]["ebooks_inside_estimate"] == 2

    # languages come from opf metadata / detection
    assert "en" in report["languages"]
    assert "de" in report["languages"]

    # downstream numbers exist and are plausible
    est = report["estimates"]
    assert est["chunks"] > 0
    assert est["words_from_ocr"] == 3 * est["assumptions"]["words_per_ocr_page"]
    assert est["embed_hours"] >= 0
    assert report["recommendations"]

    # manifest rows are queryable for phase 1
    conn = dbmod.connect(db)
    rows = conn.execute("SELECT COUNT(*) FROM files WHERE status='ok'").fetchone()[0]
    assert rows >= 6


def test_survey_resume_skips_unchanged(dump, tmp_path):
    db, _, _ = run_survey(dump, tmp_path)
    _, _, second = run_survey(dump, tmp_path)
    assert "10 already surveyed, 0 to probe" in second.output

    conn = dbmod.connect(db)
    seen, probed = conn.execute(
        "SELECT files_seen, files_probed FROM runs ORDER BY id DESC LIMIT 1"
    ).fetchone()[0:2]
    assert seen == 10
    assert probed == 0


def test_report_command_reuses_manifest(dump, tmp_path):
    db, out, _ = run_survey(dump, tmp_path)
    out2 = tmp_path / "report2.json"
    result = runner.invoke(
        app,
        ["report", "--db", str(db), "--json-out", str(out2), "--embed-rate", "1000"],
    )
    assert result.exit_code == 0, result.output
    report = json.loads(out2.read_text())
    assert report["estimates"]["assumptions"]["embed_chunks_per_sec"] == 1000
