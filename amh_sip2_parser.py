"""AMH SIP2 parser prototype."""
from dataclasses import dataclass, field
from datetime import datetime
import json
import re
from pathlib import Path
from typing import Any, Iterable, Optional


TIMESTAMP_FMT = "%m/%d/%Y %I:%M:%S %p"
SIP_DT_RE = re.compile(r"^\d{8}\s{4}\d{6}$")
FIELD_RE = re.compile(r"([A-Z]{2})([^|]*)\|")


@dataclass
class ParsedLogLine:
    raw_line: str
    log_timestamp: Optional[datetime]
    sip_message: str
    message_code: str
    parsed_at: dict = field(default_factory=dict)
    fixed: dict = field(default_factory=dict)
    fields: dict = field(default_factory=dict)
    line_kind: str = "other"

    def get(self, key: str, default: Any = None) -> Any:
        if key in self.fields:
            return self.fields[key]
        if key in self.fixed:
            return self.fixed[key]
        return default


@dataclass
class PairedCheckinTransaction:
    barcode: Optional[str]
    sequence: Optional[str]
    request: Optional[ParsedLogLine]
    response: Optional[ParsedLogLine]
    paired: bool
    route_status: str
    route_detail: Optional[str]
    confidence: str
    notes: list = field(default_factory=list)

    def to_row(self) -> dict:
        req = self.request
        resp = self.response
        return {
            "barcode": self.barcode,
            "sequence": self.sequence,
            "paired": self.paired,
            "request_log_timestamp": _iso(req.log_timestamp if req else None),
            "response_log_timestamp": _iso(resp.log_timestamp if resp else None),
            "request_sip_timestamp": req.fixed.get("transaction_date") if req else None,
            "response_sip_timestamp": resp.fixed.get("transaction_date") if resp else None,
            "request_current_location_ap": req.fields.get("AP") if req else None,
            "response_permanent_location_aq": resp.fields.get("AQ") if resp else None,
            "item_barcode_ab": self.barcode,
            "title_aj": resp.fields.get("AJ") if resp else None,
            "screen_message_af": resp.fields.get("AF") if resp else None,
            "callnumber_or_short_cs": resp.fields.get("CS") if resp else None,
            "source_location_cr": resp.fields.get("CR") if resp else None,
            "routing_target_ct": resp.fields.get("CT") if resp else None,
            "routing_target_cl": resp.fields.get("CL") if resp else None,
            "ok": resp.fixed.get("ok") if resp else None,
            "alert": resp.fixed.get("alert") if resp else None,
            "route_status": self.route_status,
            "route_detail": self.route_detail,
            "confidence": self.confidence,
            "notes": "; ".join(self.notes) if self.notes else "",
        }


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat() if value else None


def parse_log_timestamp(prefix: str) -> Optional[datetime]:
    try:
        return datetime.strptime(prefix.strip(), TIMESTAMP_FMT)
    except ValueError:
        return None


def split_log_line(line: str):
    line = line.rstrip("\r\n")
    if len(line) >= 23:
        ts = parse_log_timestamp(line[:22])
        if ts is not None:
            return ts, line[23:].strip()
    return None, line.strip()


def parse_variable_fields(payload: str) -> dict:
    fields = {}
    for key, value in FIELD_RE.findall(payload):
        fields[key] = value

    # AY/AZ appear at the tail without the normal '|' delimiter.
    ay_match = re.search(r"AY([0-9])AZ([0-9A-Fa-f]{4})$", payload)
    if ay_match:
        fields["AY"] = ay_match.group(1)
        fields["AZ"] = ay_match.group(2)
    else:
        az_match = re.search(r"AZ([0-9A-Fa-f]{4})$", payload)
        if az_match:
            fields["AZ"] = az_match.group(1)
    return fields


def normalize_sip_dt(value: Optional[str]) -> Optional[str]:
    if not value:
        return None
    value = value.strip()
    return value or None


def parse_09_checkin(payload: str, log_ts: Optional[datetime], raw_line: str) -> ParsedLogLine:
    fixed = {
        "no_block": payload[2:3] if len(payload) >= 3 else None,
        "transaction_date": normalize_sip_dt(payload[3:21]) if len(payload) >= 21 else None,
        "return_date": normalize_sip_dt(payload[21:39]) if len(payload) >= 39 else None,
    }
    remainder = payload[39:] if len(payload) > 39 else ""
    fields = parse_variable_fields(remainder)
    return ParsedLogLine(
        raw_line,
        log_ts,
        payload,
        "09",
        fixed=fixed,
        fields=fields,
        line_kind="checkin_request",
    )


def parse_10_checkin_response(payload: str, log_ts: Optional[datetime], raw_line: str) -> ParsedLogLine:
    fixed = {
        "ok": payload[2:3] if len(payload) >= 3 else None,
        "resensitize": payload[3:4] if len(payload) >= 4 else None,
        "magnetic_media": payload[4:5] if len(payload) >= 5 else None,
        "alert": payload[5:6] if len(payload) >= 6 else None,
        "transaction_date": normalize_sip_dt(payload[6:24]) if len(payload) >= 24 else None,
    }
    remainder = payload[24:] if len(payload) > 24 else ""
    fields = parse_variable_fields(remainder)
    return ParsedLogLine(
        raw_line,
        log_ts,
        payload,
        "10",
        fixed=fixed,
        fields=fields,
        line_kind="checkin_response",
    )


def parse_other(payload: str, log_ts: Optional[datetime], raw_line: str) -> ParsedLogLine:
    code = payload[:2] if len(payload) >= 2 else ""
    return ParsedLogLine(
        raw_line,
        log_ts,
        payload,
        code,
        fields=parse_variable_fields(payload),
        line_kind="other",
    )


def parse_line(line: str) -> ParsedLogLine:
    log_ts, payload = split_log_line(line)
    if payload.startswith("09"):
        return parse_09_checkin(payload, log_ts, line.rstrip("\n"))
    if payload.startswith("10"):
        return parse_10_checkin_response(payload, log_ts, line.rstrip("\n"))
    return parse_other(payload, log_ts, line.rstrip("\n"))


def parse_log(lines):
    """
    Convert raw log lines into parsed SIP2 message dictionaries.
    """
    parsed = []

    for line in lines:
        msg = parse_line(line)
        if msg:
            parsed.append(msg)

    return parsed


def classify_routing(fields: dict):
    af = (fields.get("AF") or "").strip()
    ct = (fields.get("CT") or "").strip() or None
    cl = (fields.get("CL") or "").strip() or None
    msg = af.lower()

    if "cannot find record" in msg:
        return "error", af, "high"
    if "holdshelf" in msg:
        return "holdshelf", ct or cl or af, "high"
    if "transit hold" in msg:
        return "transit_hold", ct or cl or af, "high"
    if "transit to permanent location" in msg:
        return "transit_permanent", ct or cl or af, "high"
    if "status change" in msg:
        return "status_change", ct or cl or af, "medium"
    if ct or cl:
        return "routed_other", ct or cl, "medium"
    if fields.get("AQ"):
        return "in_place", fields.get("AQ"), "low"
    return "unknown", af or None, "low"


def pair_checkin_transactions(lines: Iterable[ParsedLogLine]):
    requests = []
    results = []

    for line in lines:
        if line.line_kind == "checkin_request":
            requests.append(line)
            continue
        if line.line_kind != "checkin_response":
            continue

        resp_barcode = line.fields.get("AB")
        resp_seq = line.fields.get("AY")
        match_idx = None

        for idx in range(len(requests) - 1, -1, -1):
            req = requests[idx]
            if resp_seq and req.fields.get("AY") and resp_seq == req.fields.get("AY"):
                match_idx = idx
                break
            if resp_barcode and req.fields.get("AB") and resp_barcode == req.fields.get("AB"):
                match_idx = idx
                break

        req = requests.pop(match_idx) if match_idx is not None else None
        route_status, route_detail, confidence = classify_routing(line.fields)
        notes = []

        if req is None:
            notes.append("No matching 09 request found for response.")
        else:
            if req.fields.get("AY") and line.fields.get("AY") and req.fields.get("AY") != line.fields.get("AY"):
                notes.append("Paired by barcode, not AY sequence.")
            if req.fields.get("AB") != line.fields.get("AB"):
                notes.append("Barcode mismatch between request and response.")

        results.append(
            PairedCheckinTransaction(
                barcode=resp_barcode or (req.fields.get("AB") if req else None),
                sequence=resp_seq or (req.fields.get("AY") if req else None),
                request=req,
                response=line,
                paired=req is not None,
                route_status=route_status,
                route_detail=route_detail,
                confidence=confidence,
                notes=notes,
            )
        )

    for req in requests:
        results.append(
            PairedCheckinTransaction(
                barcode=req.fields.get("AB"),
                sequence=req.fields.get("AY"),
                request=req,
                response=None,
                paired=False,
                route_status="missing_response",
                route_detail=req.fields.get("AP"),
                confidence="low",
                notes=["09 request has no matching 10 response."],
            )
        )

    results.sort(
        key=lambda t: (
            t.request.log_timestamp if t.request and t.request.log_timestamp else datetime.min,
            t.response.log_timestamp if t.response and t.response.log_timestamp else datetime.min,
        )
    )
    return results


def parse_text(text: str):
    return [parse_line(line) for line in text.splitlines() if line.strip()]


def parse_file(path):
    return parse_text(Path(path).read_text(encoding="utf-8", errors="replace"))


def transactions_from_text(text: str):
    return pair_checkin_transactions(parse_text(text))


def transactions_from_file(path):
    return pair_checkin_transactions(parse_file(path))


def rows_for_sorting_matrix(path_or_text, is_text: bool = False):
    txns = transactions_from_text(str(path_or_text)) if is_text else transactions_from_file(path_or_text)
    return [txn.to_row() for txn in txns]


def summarize_transactions(transactions):
    summary = {
        "total_transactions": len(transactions),
        "paired_transactions": 0,
        "unpaired_transactions": 0,
        "route_status_counts": {},
    }
    for txn in transactions:
        if txn.paired:
            summary["paired_transactions"] += 1
        else:
            summary["unpaired_transactions"] += 1
        summary["route_status_counts"][txn.route_status] = (
            summary["route_status_counts"].get(txn.route_status, 0) + 1
        )
    return summary


def to_json(data):
    if isinstance(data, list):
        payload = [item.to_row() if hasattr(item, "to_row") else item for item in data]
    else:
        payload = data
    return json.dumps(payload, indent=2, ensure_ascii=False)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Parse AMH SIP2 check-in log lines.")
    parser.add_argument("path", help="Path to a log file")
    parser.add_argument("--summary", action="store_true", help="Print only summary statistics")
    args = parser.parse_args()

    transactions = transactions_from_file(args.path)
    print(to_json(summarize_transactions(transactions) if args.summary else transactions))