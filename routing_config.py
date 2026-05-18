from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Union
import pandas as pd


@dataclass
class RoutingRule:
    original_collection_code: str
    description: str
    sort_1: int
    pattern: str
    match_type: str   # "exact" or "prefix"
    prefix_length: int


def normalize_code(value: str) -> str:
    return (value or "").strip().lower()


def parse_sort_bin(value: str) -> int:
    value = (value or "").strip()
    try:
        return int(value) if value else 0
    except ValueError:
        return 0


def expand_collection_patterns(raw_value: str) -> List[str]:
    """
    Split comma-separated collection code values into individual patterns.

    Example:
    '3ct,c*' -> ['3ct', 'c*']
    '2hrmy*,h*' -> ['2hrmy*', 'h*']
    """
    if not raw_value:
        return []

    return [part.strip() for part in raw_value.split(",") if part.strip()]


def load_routing_config(csv_path: Union[str, Path]) -> List[RoutingRule]:
    """
    Load rstCCT.csv and return a list of routing rules.

    Supports:
    - exact codes
    - comma-separated multiple codes in one row
    - wildcard prefix rules ending in '*'
    """
    csv_path = Path(csv_path)
    df = pd.read_csv(csv_path, dtype=str).fillna("")

    required_columns = ["Collection Code", "Description", "Sort 1"]
    missing = [col for col in required_columns if col not in df.columns]
    if missing:
        raise ValueError(f"Missing required config columns: {', '.join(missing)}")

    rules: List[RoutingRule] = []

    for _, row in df.iterrows():
        original_collection_code = row["Collection Code"].strip()
        if not original_collection_code:
            continue

        description = row["Description"].strip()
        sort_1 = parse_sort_bin(row["Sort 1"])

        patterns = expand_collection_patterns(original_collection_code)

        for pattern in patterns:
            normalized = normalize_code(pattern)

            if normalized.endswith("*"):
                prefix = normalized[:-1]
                rules.append(
                    RoutingRule(
                        original_collection_code=original_collection_code,
                        description=description,
                        sort_1=sort_1,
                        pattern=prefix,
                        match_type="prefix",
                        prefix_length=len(prefix),
                    )
                )
            else:
                rules.append(
                    RoutingRule(
                        original_collection_code=original_collection_code,
                        description=description,
                        sort_1=sort_1,
                        pattern=normalized,
                        match_type="exact",
                        prefix_length=len(normalized),
                    )
                )

    return rules


def find_routing_rule(aq_value: str, routing_rules: List[RoutingRule]) -> Optional[RoutingRule]:
    """
    Match AQ against routing rules using this priority:
    1. exact match
    2. prefix wildcard match
    3. longest prefix wins among wildcard matches
    """
    aq_lookup = normalize_code(aq_value)

    if not aq_lookup:
        return None

    exact_matches = [
        rule for rule in routing_rules
        if rule.match_type == "exact" and rule.pattern == aq_lookup
    ]
    if exact_matches:
        return exact_matches[0]

    prefix_matches = [
        rule for rule in routing_rules
        if rule.match_type == "prefix" and aq_lookup.startswith(rule.pattern)
    ]
    if prefix_matches:
        prefix_matches.sort(key=lambda r: r.prefix_length, reverse=True)
        return prefix_matches[0]

    return None