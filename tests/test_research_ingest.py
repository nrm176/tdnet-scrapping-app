import json
import logging
from datetime import date, datetime, timezone
from io import StringIO
from unittest.mock import patch

import pytest
from bs4 import BeautifulSoup

import tdnet.research_ingest as ingest
from tdnet.parsing import extract_structured_data_from_page


OBSERVED = datetime(2026, 9, 6, 3, 0, tzinfo=timezone.utc)
URL = "https://www.release.tdnet.info/inbs/I_list_001_20260904.html"
PDF = "https://www.release.tdnet.info/inbs/140120260904000001.pdf"


def html(*, minute="00", title="決算", href="140120260904000001.pdf", next_page=False):
    pager = '<div class="pager-R" onclick="go(\'I_list_002_20260904.html\')">次へ</div>' if next_page else ""
    return f"""<html><table id="main-list-table"><tr>
    <td class="kjTime">09:{minute}</td><td class="kjCode">72030</td>
    <td class="kjCompany">トヨタ</td><td class="kjTitle"><a href="{href}">{title}</a></td>
    <td class="kjPlace">東</td><td class="kjHistroy"></td></tr></table>{pager}</html>"""


def capture(page_html=None, *, observed=OBSERVED.isoformat(), pages=None):
    return {
        "source": "tdnet", "target_date": "2026-09-04", "observed_at": observed,
        "raw_payload": {"pages": pages or [{"url": URL, "html": page_html or html()}]},
    }


def test_parse_capture_normalizes_literal_tdnet_row_and_stable_source_id():
    with patch.object(ingest, "_utc_now", return_value=OBSERVED):
        rows = ingest.parse_capture(capture())
    assert len(rows) == 1
    assert rows[0]["source_id"] == PDF
    assert rows[0]["source_date"] == date(2026, 9, 4)
    assert rows[0]["title"] == "決算"
    assert rows[0]["company_code"] == "72030"
    assert rows[0]["published_at"].isoformat() == "2026-09-04T09:00:00+09:00"
    assert rows[0]["payload"]["pdf_url"] == PDF
    with patch.object(ingest, "_utc_now", return_value=OBSERVED):
        changed = ingest.parse_capture(capture(html(title="訂正決算")))
    assert changed[0]["source_id"] == rows[0]["source_id"]
    assert changed[0]["title"] == "訂正決算"


@pytest.mark.parametrize("bad", [
    capture(html(minute="99")),
    capture(html(href="http://www.release.tdnet.info/inbs/a.pdf")),
    capture("<html><body>unknown</body></html>"),
    capture(html().replace("<td class=\"kjCode\">72030</td>", "<td class=\"kjCode\"></td>")),
    capture(observed="2026-09-06T03:00:00"),
])
def test_parse_capture_rejects_bad_clock_url_unknown_page_and_silent_row_omission(bad):
    with patch.object(ingest, "_utc_now", return_value=OBSERVED):
        with pytest.raises(ValueError):
            ingest.parse_capture(bad)


def test_parse_capture_validates_exact_page_chain_and_explicit_empty_marker():
    second_url = URL.replace("001", "002")
    good_pages = [{"url": URL, "html": html(next_page=True)},
                  {"url": second_url, "html": html(href="140120260904000002.pdf")}]
    with patch.object(ingest, "_utc_now", return_value=OBSERVED):
        assert len(ingest.parse_capture(capture(pages=good_pages))) == 2
        with pytest.raises(ValueError):
            ingest.parse_capture(capture(pages=[good_pages[0]]))
        empty = capture("<html>検索条件に該当するデータが見つかりません。</html>")
        assert ingest.parse_capture(empty) == []


def test_parse_capture_rejects_near_match_pager_target():
    second_url = URL.replace("001", "002")
    near_match = html(next_page=True).replace(
        "I_list_002_20260904.html", "I_list_002_20260904.html.backup"
    )
    pages = [
        {"url": URL, "html": near_match},
        {"url": second_url, "html": html(href="140120260904000002.pdf")},
    ]
    with patch.object(ingest, "_utc_now", return_value=OBSERVED), pytest.raises(ValueError):
        ingest.parse_capture(capture(pages=pages))


def test_parse_capture_accepts_marker_with_header_only_table_but_rejects_rows():
    marker = "検索条件に該当するデータが見つかりません。"
    header_only = f'<html>{marker}<table id="main-list-table"><tr><th>時刻</th></tr></table></html>'
    conflicting = html().replace("<html>", f"<html>{marker}")
    with patch.object(ingest, "_utc_now", return_value=OBSERVED):
        assert ingest.parse_capture(capture(header_only)) == []
        with pytest.raises(ValueError):
            ingest.parse_capture(capture(conflicting))


@pytest.mark.parametrize("page_html", [
    '<html><table id="main-list-table"></table></html>',
    '<html><table id="main-list-table"><tr><th>時刻</th></tr></table></html>',
])
def test_parse_capture_rejects_unmarked_empty_or_header_only_table(page_html):
    with patch.object(ingest, "_utc_now", return_value=OBSERVED), pytest.raises(ValueError):
        ingest.parse_capture(capture(page_html))


@pytest.mark.parametrize("target", [
    "I_list_002_20260903.html",
    "I_list_broken_20260904.html",
    "I_list_002_20260904.html.backup",
])
def test_parse_capture_rejects_invalid_continuation_on_final_page(target):
    pager = f'<div class="pager-R" onclick="go(\'{target}\')">次へ</div>'
    page_html = html().replace("</html>", f"{pager}</html>")
    with patch.object(ingest, "_utc_now", return_value=OBSERVED), pytest.raises(ValueError):
        ingest.parse_capture(capture(page_html))


def test_parse_capture_accepts_actual_empty_terminal_pager_target():
    second_url = URL.replace("001", "002")
    terminal = html(href="140120260904000002.pdf").replace(
        "</html>", '<div class="pager-R" onclick="pager(\'\')">次へ</div></html>',
    )
    pages = [
        {"url": URL, "html": html(next_page=True)},
        {"url": second_url, "html": terminal},
    ]
    with patch.object(ingest, "_utc_now", return_value=OBSERVED):
        assert len(ingest.parse_capture(capture(pages=pages))) == 2


def test_parser_defaults_to_diagnostics_but_research_adapter_suppresses_raw_values(
    caplog, capsys,
):
    sentinel = "PRIVATE_SENTINEL"
    invalid = html().replace("72030", sentinel)
    soup = BeautifulSoup(invalid, "html.parser")

    with caplog.at_level(logging.WARNING):
        assert extract_structured_data_from_page(soup, date(2026, 9, 4)) == []
    assert sentinel in caplog.text
    capsys.readouterr()

    caplog.clear()
    with caplog.at_level(logging.WARNING):
        assert extract_structured_data_from_page(
            soup, date(2026, 9, 4), log_validation_errors=False,
        ) == []
    assert sentinel not in caplog.text
    assert sentinel not in capsys.readouterr().err

    caplog.clear()
    with patch.object(ingest, "_utc_now", return_value=OBSERVED), \
         caplog.at_level(logging.WARNING), pytest.raises(ValueError):
        ingest.parse_capture(capture(invalid))
    assert sentinel not in caplog.text
    assert sentinel not in capsys.readouterr().err


class Response:
    def __init__(self, status, text):
        self.status_code, self.text = status, text
        self.content = text.encode("utf-8")


class EncodedResponse:
    def __init__(self, content, *, status=200, encoding="ISO-8859-1"):
        self.status_code = status
        self.content = content
        self.encoding = encoding

    @property
    def text(self):
        return self.content.decode(self.encoding)


class Session:
    def __init__(self, responses):
        self.responses, self.calls, self.headers = list(responses), [], {}

    def __enter__(self): return self
    def __exit__(self, *unused): return False
    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def test_fetch_capture_is_bounded_and_rejects_404_or_later_page_failure():
    for responses in ([Response(404, "")], [Response(200, html(next_page=True)), Response(500, "")]):
        session = Session(responses)
        with patch.object(ingest.requests, "Session", return_value=session), \
             patch.object(ingest, "_utc_now", return_value=OBSERVED), pytest.raises(ValueError):
            ingest.fetch_capture(date(2026, 9, 4))
        assert all(call[1] == {"timeout": 30, "allow_redirects": False} for call in session.calls)


def test_fetch_capture_strictly_decodes_utf8_bytes_despite_misleading_response_encoding():
    source_html = html(title="決算短信")
    session = Session([EncodedResponse(source_html.encode("utf-8"))])
    with patch.object(ingest.requests, "Session", return_value=session), \
         patch.object(ingest, "_utc_now", return_value=OBSERVED):
        captured = ingest.fetch_capture(date(2026, 9, 4))
        rows = ingest.parse_capture(captured)
    assert captured["raw_payload"]["pages"][0]["html"] == source_html
    assert rows[0]["title"] == "決算短信"


def test_fetch_capture_rejects_invalid_utf8_bytes():
    session = Session([EncodedResponse(b"\xff")])
    with patch.object(ingest.requests, "Session", return_value=session), \
         patch.object(ingest, "_utc_now", return_value=OBSERVED), \
         pytest.raises(ValueError, match="UTF-8"):
        ingest.fetch_capture(date(2026, 9, 4))


def test_cli_replays_capture_dry_run_without_http_or_overwrite(tmp_path):
    input_path, output_path = tmp_path / "capture.json", tmp_path / "copy.json"
    input_path.write_text(json.dumps(capture()), encoding="utf-8")
    stdout, stderr = StringIO(), StringIO()
    with patch.object(ingest, "_utc_now", return_value=OBSERVED), \
         patch.object(ingest, "fetch_capture") as fetch, \
         patch.object(ingest, "store_capture") as store, \
         patch("sys.stdout", stdout), patch("sys.stderr", stderr):
        code = ingest.main(["--input", str(input_path), "--dry-run", "--output", str(output_path)])
    assert code == 0
    assert json.loads(stdout.getvalue()) == {
        "source": "tdnet", "date": "2026-09-04", "record_count": 1,
        "snapshot_hash": "d6b0fa94f42e2336e4493d3bf7df90e5f78c523418e4c0b750b5ff7177cd4134",
        "persisted": False, "ready_for_analysis": False,
    }
    assert json.loads(output_path.read_text(encoding="utf-8")) == capture()
    fetch.assert_not_called()
    store.assert_not_called()
    with patch("sys.stdout", StringIO()), patch("sys.stderr", StringIO()):
        assert ingest.main(["--input", str(input_path), "--dry-run", "--output", str(output_path)]) != 0
