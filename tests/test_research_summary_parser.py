from __future__ import annotations

import io
import hashlib
import json
import warnings
import zipfile
from decimal import localcontext

import pytest

from tdnet.research_summary_parser import (
    PARSER_VERSION_V2,
    parse_summary,
    parse_summary_v2,
)


TAX = "http://www.xbrl.tdnet.info/taxonomy/jp/tse/tdnet/ed/t/2014-01-12"
IX = "http://www.xbrl.org/2008/inlineXBRL"
XBRLI = "http://www.xbrl.org/2003/instance"
XBRLDI = "http://xbrl.org/2006/xbrldi"
ISO = "http://www.xbrl.org/2003/iso4217"
I18N = "http://www.xbrl.org/inlineXBRL/transformation/2011-07-31"


def summary_xml(*, facts: str | None = None) -> bytes:
    facts = facts or """
      <ix:nonFraction name="t:NetSales" contextRef="c1" unitRef="JPY"
        format="i:numdotdecimal" scale="3" decimals="0">11,141,000</ix:nonFraction>
      <ix:nonFraction name="t:OperatingIncome" contextRef="c1" unitRef="JPY"
        format="i:numdotdecimal" sign="-" decimals="INF">20</ix:nonFraction>
      <ix:nonFraction name="t:OrdinaryIncome" contextRef="c1" unitRef="JPY"
        format="i:numdotdecimal" xsi:nil="true" decimals="0">－</ix:nonFraction>
      <ix:nonFraction name="t:ProfitAttributableToOwnersOfParent" contextRef="c1"
        unitRef="JPY" decimals="0">30.50</ix:nonFraction>
    """
    return f"""<html xmlns="http://www.w3.org/1999/xhtml" xmlns:ix="{IX}"
      xmlns:xbrli="{XBRLI}" xmlns:xbrldi="{XBRLDI}" xmlns:iso4217="{ISO}"
      xmlns:t="{TAX}" xmlns:i="{I18N}"
      xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
      <body><ix:resources>
        <xbrli:context id="c1"><xbrli:entity>
          <xbrli:identifier scheme="http://example.test/entity">12340</xbrli:identifier>
          <xbrli:segment><xbrldi:explicitMember dimension="t:ConsolidatedOrNonConsolidatedAxis">t:ConsolidatedMember</xbrldi:explicitMember></xbrli:segment>
        </xbrli:entity><xbrli:period><xbrli:startDate>2025-04-01</xbrli:startDate><xbrli:endDate>2026-03-31</xbrli:endDate></xbrli:period></xbrli:context>
        <xbrli:unit id="JPY"><xbrli:measure>iso4217:JPY</xbrli:measure></xbrli:unit>
      </ix:resources>{facts}<ix:nonNumeric name="t:Other" contextRef="c1">ignored</ix:nonNumeric></body>
    </html>""".encode()


def zipped(xml: bytes | None = None, *, name: str = "XBRLData/Summary/report-ixbrl.htm", extras=()) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            archive.writestr(name, xml or summary_xml())
            for extra_name, extra_content in extras:
                archive.writestr(extra_name, extra_content)
    return output.getvalue()


def v2_summary_xml() -> bytes:
    added_facts = b"""
      <ix:nonFraction name="t:TotalAssets" contextRef="instant" unitRef="JPY" decimals="0">100</ix:nonFraction>
      <ix:nonFraction name="t:NetAssets" contextRef="instant" unitRef="JPY" decimals="0">80</ix:nonFraction>
      <ix:nonFraction name="t:OwnersEquity" contextRef="instant" unitRef="JPY" decimals="0">75</ix:nonFraction>
      <ix:nonFraction name="t:ComprehensiveIncome" contextRef="c1" unitRef="JPY" decimals="0">5</ix:nonFraction>
    """
    instant_context = b"""
      <xbrli:context id="instant"><xbrli:entity>
        <xbrli:identifier scheme="http://example.test/entity">12340</xbrli:identifier>
      </xbrli:entity><xbrli:period><xbrli:instant>2026-03-31</xbrli:instant></xbrli:period></xbrli:context>
    """
    xml = summary_xml().replace(b"</ix:resources>", instant_context + b"</ix:resources>")
    return xml.replace(b"<ix:nonNumeric name=", added_facts + b"<ix:nonNumeric name=")


def test_parse_summary_extracts_scaled_negative_nil_and_context_in_document_order():
    payload = parse_summary(zipped())

    assert payload["parser_version"] == "tdnet-summary-financial-v1"
    assert payload["member_path"] == "XBRLData/Summary/report-ixbrl.htm"
    assert payload["total_fact_count"] == 5
    assert payload["selected_fact_count"] == 4
    assert payload["omitted_fact_count"] == 1
    assert payload["facts"][0]["value_decimal"] == "11141000000"
    assert [fact["value_decimal"] for fact in payload["facts"]] == [
        "11141000000", "-20", None, "30.50",
    ]
    assert [fact["ordinal"] for fact in payload["facts"]] == [1, 2, 3, 4]
    first = payload["facts"][0]
    assert first["concept_qname"] == f"{{{TAX}}}NetSales"
    assert first["format_qname"] == f"{{{I18N}}}numdotdecimal"
    assert first["context"] == {
        "entity_identifier": "12340",
        "entity_scheme": "http://example.test/entity",
        "period": {"start_date": "2025-04-01", "end_date": "2026-03-31"},
        "dimensions": [{
            "axis_qname": f"{{{TAX}}}ConsolidatedOrNonConsolidatedAxis",
            "member_qname": f"{{{TAX}}}ConsolidatedMember",
            "placement": "segment",
        }],
    }
    assert first["unit"] == {"measure_qname": f"{{{ISO}}}JPY"}


def test_prefix_alias_and_summary_path_win_over_larger_attachment():
    xml = summary_xml().replace(b"xmlns:t=", b"xmlns:alias=").replace(b"t:", b"alias:")
    payload = parse_summary(zipped(xml, extras=[("XBRLData/Attachment/big-ixbrl.htm", b"x" * 10000)]))
    assert payload["selected_fact_count"] == 4


def test_parse_summary_v2_extracts_three_instant_balance_equity_plus_duration_comprehensive_income():
    xml = v2_summary_xml()

    payload = parse_summary_v2(zipped(xml))

    assert payload["parser_version"] == PARSER_VERSION_V2
    assert payload["selected_fact_count"] == 8
    assert payload["omitted_fact_count"] == 1
    assert {fact["concept_qname"].rsplit("}", 1)[-1] for fact in payload["facts"]} == {
        "NetSales", "OperatingIncome", "OrdinaryIncome",
        "ProfitAttributableToOwnersOfParent", "TotalAssets", "NetAssets",
        "OwnersEquity", "ComprehensiveIncome",
    }
    instant = payload["facts"][4]
    assert instant["context"]["period"] == {"instant": "2026-03-31"}
    assert payload["facts"][7]["context"]["period"] == {
        "start_date": "2025-04-01", "end_date": "2026-03-31",
    }


def test_parse_summary_v1_remains_four_concepts_when_v2_facts_are_present():
    added = b'<ix:nonFraction name="t:TotalAssets" contextRef="c1" unitRef="JPY">100</ix:nonFraction>'
    payload = parse_summary(zipped(summary_xml().replace(b"<ix:nonNumeric name=", added + b"<ix:nonNumeric name=")))

    assert payload["parser_version"] == "tdnet-summary-financial-v1"
    assert payload["selected_fact_count"] == 4


def test_parse_summary_v1_rejects_selected_fact_in_instant_context():
    instant_context = b"""
      <xbrli:context id="instant"><xbrli:entity>
        <xbrli:identifier scheme="http://example.test/entity">12340</xbrli:identifier>
      </xbrli:entity><xbrli:period><xbrli:instant>2026-03-31</xbrli:instant></xbrli:period></xbrli:context>
    """
    added = b'<ix:nonFraction name="t:NetSales" contextRef="instant" unitRef="JPY">100</ix:nonFraction>'
    xml = summary_xml().replace(b"</ix:resources>", instant_context + b"</ix:resources>")
    xml = xml.replace(b"<ix:nonNumeric name=", added + b"<ix:nonNumeric name=")

    with pytest.raises(ValueError):
        parse_summary(zipped(xml))


@pytest.mark.parametrize("concept", [
    "NetSales", "OperatingIncome", "OrdinaryIncome",
    "ProfitAttributableToOwnersOfParent", "ComprehensiveIncome",
])
def test_parse_summary_v2_rejects_duration_only_concept_in_instant_context(concept):
    added = f'<ix:nonFraction name="t:{concept}" contextRef="instant" unitRef="JPY">100</ix:nonFraction>'.encode()
    xml = v2_summary_xml()
    xml = xml.replace(b"<ix:nonNumeric name=", added + b"<ix:nonNumeric name=")

    with pytest.raises(ValueError):
        parse_summary_v2(zipped(xml))


@pytest.mark.parametrize("concept", ["TotalAssets", "NetAssets", "OwnersEquity"])
def test_parse_summary_v2_rejects_instant_only_concept_in_duration_context(concept):
    xml = v2_summary_xml().replace(
        f'name="t:{concept}" contextRef="instant"'.encode(),
        f'name="t:{concept}" contextRef="c1"'.encode(),
        1,
    )

    with pytest.raises(ValueError):
        parse_summary_v2(zipped(xml))


def test_parse_summary_v2_rejects_selected_local_name_in_alternate_taxonomy_namespace():
    alternate = b'<ix:nonFraction name="other:ComprehensiveIncome" contextRef="c1" unitRef="JPY">101</ix:nonFraction>'
    xml = v2_summary_xml().replace(
        b"xmlns:t=", b'xmlns:other="http://example.test/other" xmlns:t='
    )
    xml = xml.replace(b"<ix:nonNumeric name=", alternate + b"<ix:nonNumeric name=")

    with pytest.raises(ValueError):
        parse_summary_v2(zipped(xml))


@pytest.mark.parametrize("fact_kind", ["nonFraction", "nonNumeric", "fraction"])
def test_parse_summary_v2_rejects_alternate_inline_xbrl_fact_namespace(fact_kind):
    alternate = f'<alt:{fact_kind} name="t:Other" contextRef="c1" unitRef="JPY">100</alt:{fact_kind}>'.encode()
    xml = v2_summary_xml().replace(b"xmlns:ix=", b'xmlns:alt="http://example.test/alternate-ix" xmlns:ix=')
    xml = xml.replace(b"<ix:nonNumeric name=", alternate + b"<ix:nonNumeric name=")

    with pytest.raises(ValueError):
        parse_summary_v2(zipped(xml))


@pytest.mark.parametrize("replacement", [
    (b"2026-03-31", b"2026-02-30"),
    (b"<xbrli:instant>2026-03-31</xbrli:instant>", b"<xbrli:instant><xbrli:nested/>2026-03-31</xbrli:instant>"),
])
def test_parse_summary_v2_rejects_malformed_instant_context(replacement):
    added = b'<ix:nonFraction name="t:TotalAssets" contextRef="instant" unitRef="JPY">100</ix:nonFraction>'
    instant_context = b"""
      <xbrli:context id="instant"><xbrli:entity>
        <xbrli:identifier scheme="http://example.test/entity">12340</xbrli:identifier>
      </xbrli:entity><xbrli:period><xbrli:instant>2026-03-31</xbrli:instant></xbrli:period></xbrli:context>
    """
    old, new = replacement
    instant_context = instant_context.replace(old, new, 1)
    xml = summary_xml().replace(b"</ix:resources>", instant_context + b"</ix:resources>")
    xml = xml.replace(b"<ix:nonNumeric name=", added + b"<ix:nonNumeric name=")

    with pytest.raises(ValueError):
        parse_summary_v2(zipped(xml))


@pytest.mark.parametrize("content", [
    b"not a zip",
    zipped(name="XBRLData/Attachment/report-ixbrl.htm"),
    zipped(extras=[("XBRLData/Summary/second-ixbrl.html", summary_xml())]),
    zipped(name=r"XBRLData\Summary\report-ixbrl.htm"),
    zipped(name="XBRLData/Summary/../Summary/report-ixbrl.htm"),
    zipped(name="xbrldata/summary/report-ixbrl.htm"),
    zipped(name="XBRLData/sUmMaRy/report-ixbrl.htm"),
    zipped(extras=[(".", b"")]),
    zipped(extras=[("./", b"")]),
    zipped(extras=[("XBRLData/Summary/report-ixbrl.htm", summary_xml())]),
    zipped(b"<!DOCTYPE html><html/>") ,
    zipped(b"<html>"),
])
def test_unsafe_zip_or_xml_is_normalized_to_value_error(content):
    with pytest.raises(ValueError):
        parse_summary(content)


@pytest.mark.parametrize("replacement", [
    ('contextRef="c1"', 'contextRef="missing"'),
    ('unitRef="JPY"', 'unitRef="missing"'),
    ('format="i:numdotdecimal"', 'format="t:unknown"'),
    ('11,141,000', '11.141,000'),
    ('scale="3"', 'scale="19"'),
    ('decimals="0"', 'decimals="101"'),
    ('xsi:nil="true"', 'xsi:nil="maybe"'),
    ('name="t:NetSales"', 'target="alternate" name="t:NetSales"'),
    ('<ix:nonFraction name="t:NetSales"', '<ix:nonNumeric name="t:NetSales"'),
    ('</ix:nonFraction>', '<span>nested</span></ix:nonFraction>'),
])
def test_malformed_selected_fact_is_rejected(replacement):
    old, new = replacement
    with pytest.raises(ValueError):
        parse_summary(zipped(summary_xml().replace(old.encode(), new.encode(), 1)))


def test_conflicting_duplicate_rejected_but_identical_duplicate_preserved():
    marker = b"</ix:nonFraction>"
    base = summary_xml()
    end = base.index(marker) + len(marker)
    first = base[base.rfind(b"<ix:nonFraction", 0, end):end]
    identical = base[:end] + first + base[end:]
    assert parse_summary(zipped(identical))["selected_fact_count"] == 5
    conflicting = base[:end] + first.replace(b"11,141,000", b"11,142,000") + base[end:]
    with pytest.raises(ValueError):
        parse_summary(zipped(conflicting))


def test_unreferenced_instant_context_and_divided_unit_do_not_invalidate_selected_facts():
    extra = """<xbrli:context id="instant"><xbrli:entity><xbrli:identifier scheme="x">x</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:instant>2026-03-31</xbrli:instant></xbrli:period></xbrli:context>
    <xbrli:unit id="perShare"><xbrli:divide><xbrli:unitNumerator><xbrli:measure>iso4217:JPY</xbrli:measure></xbrli:unitNumerator><xbrli:unitDenominator><xbrli:measure>xbrli:shares</xbrli:measure></xbrli:unitDenominator></xbrli:divide></xbrli:unit>""".encode()
    xml = summary_xml().replace(b"</ix:resources>", extra + b"</ix:resources>")
    assert parse_summary(zipped(xml))["selected_fact_count"] == 4


@pytest.mark.parametrize("old,new", [
    (b"2025-04-01", b"2025-02-30"),
    (b"2025-04-01", b"2027-04-01"),
    (b"</xbrli:period>", b"<xbrli:instant>2026-03-31</xbrli:instant></xbrli:period>"),
    (b"</xbrli:entity>", b"<xbrli:unknown/></xbrli:entity>"),
])
def test_selected_context_rejects_invalid_dates_order_nested_or_unknown_children(old, new):
    with pytest.raises(ValueError):
        parse_summary(zipped(summary_xml().replace(old, new, 1)))


def test_hundred_digit_value_scales_exactly_without_decimal_context_rounding():
    digits = b"9" * 100
    xml = summary_xml().replace(b"11,141,000", digits).replace(b'scale="3"', b'scale="-2"')
    assert parse_summary(zipped(xml))["facts"][0]["value_decimal"] == (b"9" * 98 + b".99").decode()


def test_negative_hundred_digit_value_and_payload_ignore_ambient_decimal_precision():
    digits = b"9" * 100
    xml = summary_xml().replace(b"11,141,000", digits).replace(b'scale="3"', b'scale="0"').replace(
        b'decimals="0">' + digits, b'sign="-" decimals="0">' + digits,
    )
    expected = parse_summary(zipped(xml))
    with localcontext() as context:
        context.prec = 5
        context.Emax = 2
        context.Emin = -2
        actual = parse_summary(zipped(xml))
    assert actual["facts"][0]["value_decimal"] == "-" + "9" * 100
    canonical_actual = json.dumps(
        actual, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()
    canonical_expected = json.dumps(
        expected, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()
    assert hashlib.sha256(canonical_actual).hexdigest() == hashlib.sha256(canonical_expected).hexdigest()


@pytest.mark.parametrize("open_tag,close_tag", [
    (b"<ix:tuple>", b"</ix:tuple>"),
    (b"<ix:tuple><div>", b"</div></ix:tuple>"),
])
def test_selected_fact_nested_in_direct_or_indirect_ix_tuple_is_rejected(open_tag, close_tag):
    xml = summary_xml()
    start = xml.index(b'<ix:nonFraction name="t:NetSales"')
    end = xml.index(b"</ix:nonFraction>", start) + len(b"</ix:nonFraction>")
    with pytest.raises(ValueError):
        parse_summary(zipped(xml[:start] + open_tag + xml[start:end] + close_tag + xml[end:]))


def test_zip_member_with_nul_truncated_raw_name_is_rejected():
    canonical = "XBRLData/Summary/report-ixbrl.htm"
    placeholder = canonical + "0suffix"
    archive = zipped(name=placeholder)
    raw_name = canonical.encode() + b"\0suffix"
    assert len(raw_name) == len(placeholder.encode())
    mutated = archive.replace(placeholder.encode(), raw_name)
    assert mutated.count(raw_name) == 2
    with pytest.raises(ValueError):
        parse_summary(mutated)


@pytest.mark.parametrize("extra", [
    b'<xbrli:context id="c1"/>',
    b'<xbrli:unit id="JPY"><xbrli:measure>iso4217:JPY</xbrli:measure></xbrli:unit>',
])
def test_duplicate_context_or_unit_id_is_rejected_globally(extra):
    xml = summary_xml().replace(b"</ix:resources>", extra + b"</ix:resources>")
    with pytest.raises(ValueError):
        parse_summary(zipped(xml))
