from __future__ import annotations

import re
from typing import Any


NEWS_RISK_KEYWORDS = {
    "legal": (
        "lawsuit",
        "sues",
        "sued",
        "investigation",
        "probe",
        "sec",
        "doj",
        "antitrust",
        "fraud",
        "settlement",
    ),
    "capital": (
        "stock offering",
        "share offering",
        "public offering",
        "secondary",
        "secondary offering",
        "dilution",
        "convertible",
        "stock sale",
    ),
    "analyst": (
        "downgrade",
        "cuts rating",
        "price target cut",
        "lowered target",
    ),
    "business": (
        "guidance cut",
        "cuts forecast",
        "lowers forecast",
        "layoff",
        "recall",
        "breach",
        "outage",
    ),
}


def evaluate_news_risk(recent_news: list[dict[str, Any]]) -> dict[str, Any]:
    matches = []
    for item in recent_news:
        title = str(item.get("title") or "")
        normalized = title.lower()
        for category, keywords in NEWS_RISK_KEYWORDS.items():
            for keyword in keywords:
                if keyword_matches(normalized, keyword):
                    matches.append(
                        {
                            "category": category,
                            "keyword": keyword,
                            "title": title,
                            "provider": item.get("provider"),
                        }
                    )

    categories = sorted({match["category"] for match in matches})
    level = "low"
    if len(matches) >= 2 or "legal" in categories or "capital" in categories:
        level = "high"
    elif matches:
        level = "elevated"
    return {
        "level": level,
        "match_count": len(matches),
        "categories": categories,
        "matches": matches[:5],
        "method": "keyword_headline_scan",
    }


def keyword_matches(text: str, keyword: str) -> bool:
    escaped = re.escape(keyword.lower())
    return re.search(rf"(?<![a-z0-9]){escaped}(?![a-z0-9])", text) is not None


def evaluate_options_risk(summary: dict[str, Any]) -> dict[str, Any]:
    flags = []
    pc_oi = as_float(summary.get("put_call_oi_ratio"))
    pc_vol = as_float(summary.get("put_call_volume_ratio"))
    avg_spread_pct = as_float(summary.get("avg_spread_pct"))
    contracts = as_float(summary.get("contracts"))

    if pc_oi is not None and pc_oi >= 2.0:
        flags.append({"name": "high_put_call_open_interest_ratio", "value": pc_oi})
    if pc_vol is not None and pc_vol >= 2.0:
        flags.append({"name": "high_put_call_volume_ratio", "value": pc_vol})
    if avg_spread_pct is not None and avg_spread_pct >= 20.0:
        flags.append({"name": "wide_average_option_spread_pct", "value": avg_spread_pct})
    if contracts is not None and contracts < 10:
        flags.append({"name": "sparse_option_chain", "value": contracts})

    level = "low"
    if any(flag["name"].startswith("wide_") for flag in flags) or len(flags) >= 2:
        level = "high"
    elif flags:
        level = "elevated"
    return {
        "level": level,
        "flags": flags,
        "method": "option_chain_summary_thresholds",
    }


def as_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None
