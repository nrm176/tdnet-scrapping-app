"""Strict, deterministic extraction of selected TDnet Summary financial facts."""

from __future__ import annotations

import io
import re
import zipfile
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import PurePosixPath

from lxml import etree

from tdnet.research_artifacts import validate_binary


PARSER_VERSION = "tdnet-summary-financial-v1"
PARSER_VERSION_V2 = "tdnet-summary-financial-v2"
_IX = "http://www.xbrl.org/2008/inlineXBRL"
_XBRLI = "http://www.xbrl.org/2003/instance"
_XBRLDI = "http://xbrl.org/2006/xbrldi"
_XSI = "http://www.w3.org/2001/XMLSchema-instance"
_TAXONOMY = "http://www.xbrl.tdnet.info/taxonomy/jp/tse/tdnet/ed/t/2014-01-12"
_TRANSFORM = "http://www.xbrl.org/inlineXBRL/transformation/2011-07-31"
_ISO4217 = "http://www.xbrl.org/2003/iso4217"
_V1_CONCEPTS = {
    f"{{{_TAXONOMY}}}{name}"
    for name in (
        "NetSales", "OperatingIncome", "OrdinaryIncome",
        "ProfitAttributableToOwnersOfParent",
    )
}
_V2_CONCEPTS = _V1_CONCEPTS | {
    f"{{{_TAXONOMY}}}{name}"
    for name in ("TotalAssets", "NetAssets", "OwnersEquity", "ComprehensiveIncome")
}
_V2_DURATION_CONCEPTS = _V1_CONCEPTS | {f"{{{_TAXONOMY}}}ComprehensiveIncome"}
_V2_INSTANT_CONCEPTS = {
    f"{{{_TAXONOMY}}}{name}" for name in ("TotalAssets", "NetAssets", "OwnersEquity")
}
_V2_CONCEPT_LOCALS = {concept.rsplit("}", 1)[-1] for concept in _V2_CONCEPTS}
_FACT_KINDS = {f"{{{_IX}}}{name}" for name in ("nonNumeric", "nonFraction", "fraction")}
_FACT_KIND_LOCALS = {"nonNumeric", "nonFraction", "fraction"}
_SUMMARY_PATH = re.compile(r"^XBRLData/Summary/[^/]+-ixbrl\.html?$")
_NUMBER = re.compile(r"(?:0|[1-9][0-9]{0,2}(?:,[0-9]{3})*|[1-9][0-9]*)(?:\.[0-9]+)?\Z")
_XML_DECIMAL = re.compile(r"(?:0|[1-9][0-9]*)(?:\.[0-9]+)?\Z")
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}\Z")


def _invalid() -> ValueError:
    return ValueError("invalid TDnet Summary artifact")


def _qname(value: str | None, element: etree._Element) -> str:
    if not value or value.count(":") > 1:
        raise _invalid()
    if ":" in value:
        prefix, local = value.split(":", 1)
        namespace = element.nsmap.get(prefix)
    else:
        local = value
        namespace = element.nsmap.get(None)
    if not namespace or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.-]*", local):
        raise _invalid()
    return f"{{{namespace}}}{local}"


def _only_child(parent: etree._Element, tag: str) -> etree._Element:
    found = parent.findall(tag)
    if len(found) != 1:
        raise _invalid()
    return found[0]


def _contexts(root: etree._Element, referenced: set[str], *, allow_instant: bool) -> dict[str, dict[str, object]]:
    result: dict[str, dict[str, object]] = {}
    nodes: dict[str, etree._Element] = {}
    for node in root.iter(f"{{{_XBRLI}}}context"):
        context_id = node.get("id")
        if not context_id or context_id in nodes:
            raise _invalid()
        nodes[context_id] = node
    if not referenced <= nodes.keys():
        raise _invalid()
    for context_id in referenced:
        node = nodes[context_id]
        entity = _only_child(node, f"{{{_XBRLI}}}entity")
        identifier = _only_child(entity, f"{{{_XBRLI}}}identifier")
        identifier_text = (identifier.text or "").strip()
        scheme = identifier.get("scheme")
        if not identifier_text or not scheme or len(identifier):
            raise _invalid()
        period = _only_child(node, f"{{{_XBRLI}}}period")
        period_children = {child.tag for child in period}
        if period_children == {f"{{{_XBRLI}}}startDate", f"{{{_XBRLI}}}endDate"}:
            start = _only_child(period, f"{{{_XBRLI}}}startDate")
            end = _only_child(period, f"{{{_XBRLI}}}endDate")
            start_text, end_text = (start.text or "").strip(), (end.text or "").strip()
            try:
                start_date, end_date = date.fromisoformat(start_text), date.fromisoformat(end_text)
            except ValueError as exc:
                raise _invalid() from exc
            if (not _DATE.fullmatch(start_text) or not _DATE.fullmatch(end_text)
                    or start_date > end_date or len(period) != 2
                    or len(start) or len(end)):
                raise _invalid()
            period_payload = {"start_date": start_text, "end_date": end_text}
        elif period_children == {f"{{{_XBRLI}}}instant"}:
            if not allow_instant:
                raise _invalid()
            instant = _only_child(period, f"{{{_XBRLI}}}instant")
            instant_text = (instant.text or "").strip()
            try:
                date.fromisoformat(instant_text)
            except ValueError as exc:
                raise _invalid() from exc
            if not _DATE.fullmatch(instant_text) or len(instant):
                raise _invalid()
            period_payload = {"instant": instant_text}
        else:
            raise _invalid()
        dimensions: list[dict[str, str]] = []
        axes: set[str] = set()
        for placement in ("segment", "scenario"):
            holders = entity.findall(f"{{{_XBRLI}}}{placement}") if placement == "segment" else node.findall(f"{{{_XBRLI}}}{placement}")
            if len(holders) > 1:
                raise _invalid()
            if not holders:
                continue
            for child in holders[0]:
                if child.tag != f"{{{_XBRLDI}}}explicitMember" or len(child):
                    raise _invalid()
                axis = _qname(child.get("dimension"), child)
                member = _qname((child.text or "").strip(), child)
                if axis in axes:
                    raise _invalid()
                axes.add(axis)
                dimensions.append({"axis_qname": axis, "member_qname": member, "placement": placement})
        allowed_entity = {f"{{{_XBRLI}}}identifier", f"{{{_XBRLI}}}segment"}
        allowed_context = {f"{{{_XBRLI}}}entity", f"{{{_XBRLI}}}period", f"{{{_XBRLI}}}scenario"}
        if any(child.tag not in allowed_entity for child in entity) or any(child.tag not in allowed_context for child in node):
            raise _invalid()
        result[context_id] = {
            "entity_identifier": identifier_text,
            "entity_scheme": scheme,
            "period": period_payload,
            "dimensions": dimensions,
        }
    return result


def _units(root: etree._Element, referenced: set[str]) -> dict[str, dict[str, str]]:
    result: dict[str, dict[str, str]] = {}
    nodes: dict[str, etree._Element] = {}
    for node in root.iter(f"{{{_XBRLI}}}unit"):
        unit_id = node.get("id")
        if not unit_id or unit_id in nodes:
            raise _invalid()
        nodes[unit_id] = node
    if not referenced <= nodes.keys():
        raise _invalid()
    for unit_id in referenced:
        node = nodes[unit_id]
        if len(node) != 1:
            raise _invalid()
        measure = _only_child(node, f"{{{_XBRLI}}}measure")
        expanded = _qname((measure.text or "").strip(), measure)
        if expanded != f"{{{_ISO4217}}}JPY" or len(measure):
            raise _invalid()
        result[unit_id] = {"measure_qname": expanded}
    return result


def _bounded_integer(value: str | None, low: int, high: int, default: int | None) -> int | None:
    if value is None:
        return default
    if not re.fullmatch(r"-?(?:0|[1-9][0-9]*)", value):
        raise _invalid()
    parsed = int(value)
    if not low <= parsed <= high:
        raise _invalid()
    return parsed


def _fixed(value: Decimal) -> str:
    return format(value, "f")


def _fact_payload(
    node: etree._Element,
    ordinal: int,
    concept: str,
    contexts: dict[str, dict[str, object]],
    units: dict[str, dict[str, str]],
) -> dict[str, object]:
    if node.tag != f"{{{_IX}}}nonFraction" or len(node):
        raise _invalid()
    if any(ancestor.tag == f"{{{_IX}}}tuple" for ancestor in node.iterancestors()):
        raise _invalid()
    if any(node.get(name) is not None for name in ("target", "tupleRef", "continuedAt")):
        raise _invalid()
    context_id, unit_id = node.get("contextRef"), node.get("unitRef")
    if not context_id or context_id not in contexts or not unit_id or unit_id not in units:
        raise _invalid()
    raw_text = node.text or ""
    nil_attr = node.get(f"{{{_XSI}}}nil")
    if nil_attr not in (None, "true", "false", "1", "0"):
        raise _invalid()
    is_nil = nil_attr in ("true", "1")
    format_value = node.get("format")
    format_qname = _qname(format_value, node) if format_value is not None else None
    scale = _bounded_integer(node.get("scale"), -18, 18, 0)
    sign = node.get("sign")
    if sign not in (None, "-"):
        raise _invalid()
    decimals = node.get("decimals")
    if decimals is not None and decimals != "INF":
        _bounded_integer(decimals, -100, 100, None)
    if is_nil:
        value_decimal = None
    else:
        lexeme = raw_text.strip()
        pattern = _XML_DECIMAL if format_qname is None else _NUMBER
        if format_qname not in (None, f"{{{_TRANSFORM}}}numdotdecimal") or not pattern.fullmatch(lexeme):
            raise _invalid()
        digits = lexeme.replace(",", "").replace(".", "")
        if len(digits) > 100:
            raise _invalid()
        try:
            unscaled = Decimal(lexeme.replace(",", ""))
            parts = unscaled.as_tuple()
            numeric = Decimal((0, parts.digits, parts.exponent + scale))
        except InvalidOperation as exc:
            raise _invalid() from exc
        if sign == "-":
            numeric = numeric.copy_negate()
        value_decimal = _fixed(numeric)
    return {
        "ordinal": ordinal,
        "concept_qname": concept,
        "context_id": context_id,
        "unit_id": unit_id,
        "raw_text": raw_text,
        "is_nil": is_nil,
        "value_decimal": value_decimal,
        "format_qname": format_qname,
        "scale": scale if node.get("scale") is not None else None,
        "sign": sign,
        "decimals": decimals,
        "source_line": node.sourceline,
        "context": contexts[context_id],
        "unit": units[unit_id],
    }


def _validate_v2_namespaces(root: etree._Element) -> None:
    for node in root.iter():
        if not isinstance(node.tag, str):
            continue
        qname = etree.QName(node)
        if qname.localname in _FACT_KIND_LOCALS and node.tag != f"{{{_IX}}}{qname.localname}":
            raise _invalid()
        if node.tag not in _FACT_KINDS:
            continue
        concept = _qname(node.get("name"), node)
        if concept.rsplit("}", 1)[-1] in _V2_CONCEPT_LOCALS and concept not in _V2_CONCEPTS:
            raise _invalid()


def _parse_summary(content: bytes, *, parser_version: str, concepts: set[str],
                   allow_instant: bool, period_by_concept: dict[str, str] | None = None) -> dict[str, object]:
    """Parse one validated TDnet XBRL ZIP into a deterministic fact payload."""
    try:
        validate_binary(content, "xbrl_zip")
        with zipfile.ZipFile(io.BytesIO(content)) as archive:
            infos = archive.infolist()
            if any("\x00" in info.orig_filename or info.orig_filename != info.filename for info in infos):
                raise _invalid()
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise _invalid()
            for info, name in zip(infos, names, strict=True):
                raw_path = name[:-1] if info.is_dir() and name.endswith("/") else name
                raw_parts = raw_path.split("/")
                path = PurePosixPath(name)
                canonical = str(path) + ("/" if info.is_dir() else "")
                if ("\\" in name or not name or name.startswith("/") or "." in path.parts
                        or ".." in path.parts or not raw_path
                        or any(part in {"", ".", ".."} for part in raw_parts)
                        or name != canonical):
                    raise _invalid()
            candidates = [info for info in infos if not info.is_dir() and _SUMMARY_PATH.fullmatch(info.filename)]
            if len(candidates) != 1:
                raise _invalid()
            member = candidates[0]
            xml = archive.read(member)
        if re.search(br"<!\s*DOCTYPE", xml, re.IGNORECASE):
            raise _invalid()
        parser = etree.XMLParser(resolve_entities=False, load_dtd=False, no_network=True, recover=False)
        root = etree.fromstring(xml, parser)
        if root.getroottree().docinfo.doctype or any(isinstance(node, etree._Entity) for node in root.iter()):
            raise _invalid()
        if parser_version == PARSER_VERSION_V2:
            _validate_v2_namespaces(root)
        referenced_contexts: set[str] = set()
        referenced_units: set[str] = set()
        for node in root.iter():
            if node.tag not in _FACT_KINDS:
                continue
            concept = _qname(node.get("name"), node)
            if concept in concepts:
                context_ref, unit_ref = node.get("contextRef"), node.get("unitRef")
                if not context_ref or not unit_ref:
                    raise _invalid()
                referenced_contexts.add(context_ref)
                referenced_units.add(unit_ref)
        contexts = _contexts(root, referenced_contexts, allow_instant=allow_instant)
        units = _units(root, referenced_units)
        facts: list[dict[str, object]] = []
        total = 0
        seen: dict[tuple[str, str, str], dict[str, object]] = {}
        present: set[str] = set()
        for node in root.iter():
            if node.tag not in _FACT_KINDS:
                continue
            total += 1
            concept = _qname(node.get("name"), node)
            if concept not in concepts:
                continue
            fact = _fact_payload(node, len(facts) + 1, concept, contexts, units)
            if period_by_concept is not None:
                period_kind = "instant" if "instant" in fact["context"]["period"] else "duration"
                if period_by_concept.get(concept) != period_kind:
                    raise _invalid()
            key = (concept, str(fact["context_id"]), str(fact["unit_id"]))
            comparable = {k: v for k, v in fact.items() if k not in {"ordinal", "source_line"}}
            if key in seen and seen[key] != comparable:
                raise _invalid()
            seen[key] = comparable
            facts.append(fact)
            present.add(concept)
        if present != concepts or not any(not fact["is_nil"] for fact in facts):
            raise _invalid()
        return {
            "parser_version": parser_version,
            "member_path": member.filename,
            "total_fact_count": total,
            "selected_fact_count": len(facts),
            "omitted_fact_count": total - len(facts),
            "facts": facts,
        }
    except ValueError:
        raise
    except Exception as exc:
        raise _invalid() from exc


def parse_summary(content: bytes) -> dict[str, object]:
    """Parse one TDnet Summary artifact using the immutable v1 contract."""
    return _parse_summary(content, parser_version=PARSER_VERSION, concepts=_V1_CONCEPTS,
                          allow_instant=False)


def parse_summary_v2(content: bytes) -> dict[str, object]:
    """Parse one TDnet Summary artifact using the v2 financial allowlist."""
    return _parse_summary(content, parser_version=PARSER_VERSION_V2, concepts=_V2_CONCEPTS,
                          allow_instant=True,
                          period_by_concept={
                              **{concept: "duration" for concept in _V2_DURATION_CONCEPTS},
                              **{concept: "instant" for concept in _V2_INSTANT_CONCEPTS},
                          })
