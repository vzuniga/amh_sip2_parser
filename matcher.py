from typing import Optional
from routing_config import find_routing_rule


VALID_BINS = set(range(0, 9))


def normalize_code(value: Optional[str]) -> str:
    return (value or "").strip().lower()


def parse_ct_value(route_value: Optional[str]):
    """
    Parse CT values like:
    1main:23052008403739

    Returns:
    - ct_destination_location: 1main
    - ct_patron_barcode: 23052008403739
    """
    if not route_value:
        return None, None

    value = route_value.strip()

    if ":" in value:
        location_code, patron_barcode = value.split(":", 1)
        return location_code.strip(), patron_barcode.strip()

    return value, None


def choose_transaction_datetime(row):
    """
    Prefer response log timestamp, then request log timestamp.
    """
    return (
        row.get("response_log_timestamp")
        or row.get("request_log_timestamp")
        or ""
    )


def split_transaction_datetime(timestamp_value):
    """
    Split ISO timestamp like:
    2026-03-16T09:54:24

    Returns:
    - transaction_date: 2026-03-16
    - transaction_time: 09:54:24
    """
    if not timestamp_value:
        return "", ""

    if "T" in timestamp_value:
        date_part, time_part = timestamp_value.split("T", 1)
        return date_part, time_part

    return timestamp_value, ""


def apply_sorting_matrix(rows, routing_rules):
    enriched = []

    for row in rows:
        aq_raw = (row.get("response_permanent_location_aq") or "").strip()
        aq_lookup = normalize_code(aq_raw)

        rule = find_routing_rule(aq_raw, routing_rules)

        expected_bin = rule.sort_1 if rule else 0
        if expected_bin not in VALID_BINS:
            expected_bin = 0

        config_description = rule.description if rule else "Exception / Unmapped"
        config_collection_code = rule.original_collection_code if rule else aq_raw

        observed_route = row.get("routing_target_ct") or row.get("routing_target_cl")
        ct_destination_location, ct_patron_barcode = parse_ct_value(observed_route)

        transaction_datetime = choose_transaction_datetime(row)
        transaction_date, transaction_time = split_transaction_datetime(transaction_datetime)

        row["transaction_datetime"] = transaction_datetime
        row["transaction_date"] = transaction_date
        row["transaction_time"] = transaction_time
        row["aq_display"] = aq_raw
        row["aq_lookup"] = aq_lookup
        row["config_collection_code"] = config_collection_code
        row["config_description"] = config_description
        row["expected_bin"] = expected_bin
        row["ct_destination_location"] = ct_destination_location
        row["ct_patron_barcode"] = ct_patron_barcode

        enriched.append(row)

    return enriched


def summarize_bins(rows):
    counts = {i: 0 for i in range(0, 9)}

    for row in rows:
        bin_num = row.get("expected_bin", 0)
        if bin_num not in counts:
            bin_num = 0
        counts[bin_num] += 1

    total = sum(counts.values())

    summary = []
    for bin_num in range(0, 9):
        count = counts[bin_num]
        percent = round((count / total) * 100, 2) if total else 0.0

        summary.append({
            "bin": bin_num,
            "label": "Bin 0 - Exceptions" if bin_num == 0 else f"Bin {bin_num}",
            "count": count,
            "percent": percent,
        })

    return {
        "total_rows": total,
        "bins": summary,
    }