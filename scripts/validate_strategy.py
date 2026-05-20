#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Validate strategy research reports against live-readiness gates.")
    parser.add_argument("--backtest-report", default=None, help="5-minute backtest report. Defaults to latest backtest_*.json.")
    parser.add_argument("--stress-report", default=None, help="Stress-suite report. Defaults to latest stress_*.json.")
    parser.add_argument("--hourly-proxy-report", default=None, help="1-hour yfinance proxy report. Defaults to latest hourly_proxy_*.json when available.")
    parser.add_argument("--daily-proxy-report", default=None, help="Daily proxy report. Defaults to latest daily_proxy_*.json.")
    parser.add_argument("--symbol-ablation-report", default=None, help="Symbol ablation CSV. Defaults to latest ablation_*.csv when available.")
    parser.add_argument("--theme-ablation-report", default=None, help="Theme ablation CSV. Defaults to latest theme_ablation_*.csv when available.")
    parser.add_argument("--output-dir", default="data/exports")
    parser.add_argument("--fail-on-gate-failure", action="store_true")
    parser.add_argument("--min-5m-trades", type=int, default=100)
    parser.add_argument("--min-hourly-trades", type=int, default=150)
    parser.add_argument("--min-daily-trades", type=int, default=500)
    parser.add_argument("--min-profit-factor", type=float, default=1.2)
    parser.add_argument("--max-5m-drawdown-pct", type=float, default=10.0)
    parser.add_argument("--max-hourly-drawdown-pct", type=float, default=15.0)
    parser.add_argument("--max-daily-drawdown-pct", type=float, default=20.0)
    parser.add_argument("--min-positive-month-share", type=float, default=0.55)
    parser.add_argument("--max-5m-top1-net-share", type=float, default=0.50)
    parser.add_argument("--max-5m-top3-net-share", type=float, default=1.00)
    parser.add_argument("--max-hourly-top1-net-share", type=float, default=0.35)
    parser.add_argument("--max-hourly-top3-net-share", type=float, default=0.70)
    parser.add_argument("--max-daily-top1-net-share", type=float, default=0.25)
    parser.add_argument("--max-daily-top3-net-share", type=float, default=0.45)
    return parser


def main() -> int:
    args = build_parser().parse_args()
    output_dir = Path(args.output_dir)
    try:
        backtest_path = Path(args.backtest_report) if args.backtest_report else latest_report(output_dir, "backtest_")
        stress_path = Path(args.stress_report) if args.stress_report else latest_report(output_dir, "stress_")
        daily_proxy_path = (
            Path(args.daily_proxy_report)
            if args.daily_proxy_report
            else latest_report(output_dir, "daily_proxy_")
        )
        hourly_proxy_path = optional_report(output_dir, "hourly_proxy_", args.hourly_proxy_report, suffix=".json")
        symbol_ablation_path = optional_report(output_dir, "ablation_", args.symbol_ablation_report, suffix=".csv")
        theme_ablation_path = optional_report(output_dir, "theme_ablation_", args.theme_ablation_report, suffix=".csv")
        report = build_validation_report(
            backtest=read_json(backtest_path),
            stress=read_json(stress_path),
            hourly_proxy=read_json(hourly_proxy_path) if hourly_proxy_path else None,
            daily_proxy=read_json(daily_proxy_path),
            symbol_ablation=read_csv_rows(symbol_ablation_path) if symbol_ablation_path else None,
            theme_ablation=read_csv_rows(theme_ablation_path) if theme_ablation_path else None,
            paths={
                "backtest": str(backtest_path),
                "stress": str(stress_path),
                "hourly_proxy": str(hourly_proxy_path) if hourly_proxy_path else None,
                "daily_proxy": str(daily_proxy_path),
                "symbol_ablation": str(symbol_ablation_path) if symbol_ablation_path else None,
                "theme_ablation": str(theme_ablation_path) if theme_ablation_path else None,
            },
            args=args,
        )
        output_path = output_dir / f"strategy_validation_{datetime.now(UTC).strftime('%Y%m%d_%H%M%S_%f')}.json"
        write_json(output_path, report)
    except Exception as exc:
        print(f"validate_strategy failed: {exc}", file=sys.stderr)
        return 1

    print(format_validation_summary(report, output_path))
    if args.fail_on_gate_failure and report["overall_status"] == "fail":
        return 2
    return 0


def latest_report(output_dir: Path, prefix: str) -> Path:
    reports = sorted(output_dir.glob(f"{prefix}*.json"), key=lambda path: path.stat().st_mtime)
    if not reports:
        raise FileNotFoundError(f"No {prefix}*.json reports found in {output_dir}")
    return reports[-1]


def optional_report(output_dir: Path, prefix: str, explicit_path: str | None, *, suffix: str) -> Path | None:
    if explicit_path:
        return Path(explicit_path)
    reports = sorted(output_dir.glob(f"{prefix}*{suffix}"), key=lambda path: path.stat().st_mtime)
    return reports[-1] if reports else None


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv_rows(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_validation_report(
    *,
    backtest: dict[str, Any],
    stress: dict[str, Any],
    hourly_proxy: dict[str, Any] | None,
    daily_proxy: dict[str, Any],
    symbol_ablation: list[dict[str, Any]] | None,
    theme_ablation: list[dict[str, Any]] | None,
    paths: dict[str, str],
    args: argparse.Namespace,
) -> dict[str, Any]:
    backtest_summary = backtest.get("summary") or {}
    daily_summary = daily_proxy.get("summary") or {}
    hourly_summary = (hourly_proxy or {}).get("summary") or {}
    stress_stability = stress.get("stability") or {}
    backtest_concentration = symbol_concentration(backtest_summary)
    hourly_concentration = symbol_concentration(hourly_summary)
    daily_concentration = symbol_concentration(daily_summary)

    gates = []
    gates.extend(
        validate_performance_block(
            prefix="5m_backtest",
            summary=backtest_summary,
            min_trades=args.min_5m_trades,
            min_profit_factor=args.min_profit_factor,
            max_drawdown_pct=args.max_5m_drawdown_pct,
        )
    )
    gates.append(
        gate(
            "5m_backtest_max_hold_days",
            "pass" if config_max_hold_days(backtest) <= 5 else "fail",
            config_max_hold_days(backtest),
            "<= 5",
            "holding-period constraint",
        )
    )
    gates.extend(
        validate_concentration_block(
            prefix="5m_backtest",
            concentration=backtest_concentration,
            max_top1_net_share=args.max_5m_top1_net_share,
            max_top3_net_share=args.max_5m_top3_net_share,
        )
    )
    gates.extend(
        validate_stress_block(
            stress,
            min_profit_factor=1.0,
        )
    )
    ablation_metrics = {
        "symbol": ablation_dependency(symbol_ablation, key="removed_symbol") if symbol_ablation is not None else None,
        "theme": ablation_dependency(theme_ablation, key="removed_theme") if theme_ablation is not None else None,
    }
    gates.extend(validate_ablation_block("symbol_ablation", ablation_metrics["symbol"]))
    gates.extend(validate_ablation_block("theme_ablation", ablation_metrics["theme"]))
    if hourly_proxy is None:
        gates.append(
            gate(
                "hourly_proxy_available",
                "warning",
                None,
                "hourly_proxy_*.json available",
                "longer intraday proxy check was not run",
            )
        )
    else:
        gates.extend(
            validate_performance_block(
                prefix="hourly_proxy",
                summary=hourly_summary,
                min_trades=args.min_hourly_trades,
                min_profit_factor=args.min_profit_factor,
                max_drawdown_pct=args.max_hourly_drawdown_pct,
            )
        )
        gates.append(
            gate(
                "hourly_proxy_max_hold_days",
                "pass" if config_max_hold_days(hourly_proxy) <= 5 else "fail",
                config_max_hold_days(hourly_proxy),
                "<= 5",
                "hourly proxy holding-period constraint",
            )
        )
        gates.extend(
            validate_concentration_block(
                prefix="hourly_proxy",
                concentration=hourly_concentration,
                max_top1_net_share=args.max_hourly_top1_net_share,
                max_top3_net_share=args.max_hourly_top3_net_share,
            )
        )
    gates.extend(
        validate_performance_block(
            prefix="daily_proxy",
            summary=daily_summary,
            min_trades=args.min_daily_trades,
            min_profit_factor=args.min_profit_factor,
            max_drawdown_pct=args.max_daily_drawdown_pct,
        )
    )
    gates.append(
        gate(
            "daily_proxy_positive_month_share",
            "pass" if positive_month_share(daily_summary) >= args.min_positive_month_share else "fail",
            round(positive_month_share(daily_summary), 4),
            f">= {args.min_positive_month_share}",
            "longer-history month stability",
        )
    )
    gates.extend(
        validate_concentration_block(
            prefix="daily_proxy",
            concentration=daily_concentration,
            max_top1_net_share=args.max_daily_top1_net_share,
            max_top3_net_share=args.max_daily_top3_net_share,
        )
    )

    failed = [item for item in gates if item["status"] == "fail"]
    warnings = [item for item in gates if item["status"] == "warning"]
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "mode": "strategy_validation",
        "overall_status": "fail" if failed else "warning" if warnings else "pass",
        "live_readiness": "not_ready" if failed else "paper_trade_ready" if warnings else "observation_ready",
        "source_reports": paths,
        "thresholds": {
            "min_5m_trades": args.min_5m_trades,
            "min_hourly_trades": args.min_hourly_trades,
            "min_daily_trades": args.min_daily_trades,
            "min_profit_factor": args.min_profit_factor,
            "max_5m_drawdown_pct": args.max_5m_drawdown_pct,
            "max_hourly_drawdown_pct": args.max_hourly_drawdown_pct,
            "max_daily_drawdown_pct": args.max_daily_drawdown_pct,
            "min_positive_month_share": args.min_positive_month_share,
            "max_5m_top1_net_share": args.max_5m_top1_net_share,
            "max_5m_top3_net_share": args.max_5m_top3_net_share,
            "max_hourly_top1_net_share": args.max_hourly_top1_net_share,
            "max_hourly_top3_net_share": args.max_hourly_top3_net_share,
            "max_daily_top1_net_share": args.max_daily_top1_net_share,
            "max_daily_top3_net_share": args.max_daily_top3_net_share,
        },
        "metrics": {
            "5m_backtest": compact_summary(backtest_summary) | {
                "candidate_count": backtest.get("candidate_count"),
                "concentration": backtest_concentration,
            },
            "stress": {
                "scenario_count": stress_stability.get("scenario_count"),
                "positive_scenarios": stress_stability.get("positive_scenarios"),
                "negative_scenarios": stress_stability.get("negative_scenarios"),
                "min_return_pct": stress_stability.get("min_return_pct"),
                "max_return_pct": stress_stability.get("max_return_pct"),
                "min_profit_factor": min_stress_profit_factor(stress),
            },
            "ablation": ablation_metrics,
            "hourly_proxy": (
                compact_summary(hourly_summary) | {
                    "candidate_count": hourly_proxy.get("candidate_count") if hourly_proxy else None,
                    "concentration": hourly_concentration,
                    "note": "hourly proxy uses yfinance 1-hour bars and does not verify the 5-minute trigger",
                }
                if hourly_proxy is not None
                else None
            ),
            "daily_proxy": compact_summary(daily_summary) | {
                "candidate_count": daily_proxy.get("candidate_count"),
                "positive_month_share": round(positive_month_share(daily_summary), 4),
                "concentration": daily_concentration,
                "note": "daily proxy uses 1-day bars and does not verify the 5-minute trigger",
            },
        },
        "gates": gates,
        "failed_gates": [item["name"] for item in failed],
        "warning_gates": [item["name"] for item in warnings],
    }


def validate_performance_block(
    *,
    prefix: str,
    summary: dict[str, Any],
    min_trades: int,
    min_profit_factor: float,
    max_drawdown_pct: float,
) -> list[dict[str, Any]]:
    return [
        gate(
            f"{prefix}_trade_count",
            "pass" if as_float(summary.get("trade_count")) >= min_trades else "fail",
            summary.get("trade_count"),
            f">= {min_trades}",
            "sample size",
        ),
        gate(
            f"{prefix}_return_positive",
            "pass" if as_float(summary.get("return_pct")) > 0 else "fail",
            summary.get("return_pct"),
            "> 0",
            "net profitability",
        ),
        gate(
            f"{prefix}_average_r_positive",
            "pass" if as_float(summary.get("average_r")) > 0 else "fail",
            summary.get("average_r"),
            "> 0",
            "risk-adjusted trade quality",
        ),
        gate(
            f"{prefix}_profit_factor",
            "pass" if as_float(summary.get("profit_factor")) >= min_profit_factor else "fail",
            summary.get("profit_factor"),
            f">= {min_profit_factor}",
            "profit factor",
        ),
        gate(
            f"{prefix}_max_drawdown",
            "pass" if as_float(summary.get("max_drawdown_pct")) <= max_drawdown_pct else "fail",
            summary.get("max_drawdown_pct"),
            f"<= {max_drawdown_pct}",
            "drawdown control",
        ),
    ]


def validate_concentration_block(
    *,
    prefix: str,
    concentration: dict[str, Any],
    max_top1_net_share: float,
    max_top3_net_share: float,
) -> list[dict[str, Any]]:
    return [
        gate(
            f"{prefix}_top1_net_concentration",
            "pass" if as_float(concentration.get("top1_net_share")) <= max_top1_net_share else "fail",
            concentration.get("top1_net_share"),
            f"<= {max_top1_net_share}",
            f"top1 positive contributor={concentration.get('top1_symbol')}",
        ),
        gate(
            f"{prefix}_top3_net_concentration",
            "pass" if as_float(concentration.get("top3_net_share")) <= max_top3_net_share else "fail",
            concentration.get("top3_net_share"),
            f"<= {max_top3_net_share}",
            f"top3 positive contributors={','.join(concentration.get('top3_symbols') or [])}",
        ),
    ]


def validate_stress_block(stress: dict[str, Any], *, min_profit_factor: float) -> list[dict[str, Any]]:
    stability = stress.get("stability") or {}
    scenario_count = as_float(stability.get("scenario_count"))
    positive = as_float(stability.get("positive_scenarios"))
    return [
        gate(
            "stress_all_scenarios_positive",
            "pass" if scenario_count > 0 and positive == scenario_count else "fail",
            f"{int(positive)}/{int(scenario_count)}" if scenario_count else None,
            "all scenarios",
            "parameter robustness",
        ),
        gate(
            "stress_min_return_positive",
            "pass" if as_float(stability.get("min_return_pct")) > 0 else "fail",
            stability.get("min_return_pct"),
            "> 0",
            "worst stress return",
        ),
        gate(
            "stress_min_profit_factor",
            "pass" if min_stress_profit_factor(stress) >= min_profit_factor else "fail",
            min_stress_profit_factor(stress),
            f">= {min_profit_factor}",
            "worst stress profit factor",
        ),
    ]


def validate_ablation_block(prefix: str, metrics: dict[str, Any] | None) -> list[dict[str, Any]]:
    if metrics is None:
        return [
            gate(
                f"{prefix}_available",
                "warning",
                None,
                "CSV available",
                "ablation dependency check was not run",
            )
        ]
    return [
        gate(
            f"{prefix}_keeps_positive_without_top_dependency",
            "pass" if as_float(metrics.get("worst_removed_return_pct")) > 0 else "fail",
            metrics.get("worst_removed_return_pct"),
            "> 0",
            f"worst removal={metrics.get('worst_removed_name')}",
        )
    ]


def gate(name: str, status: str, actual: Any, threshold: str, rationale: str) -> dict[str, Any]:
    return {
        "name": name,
        "status": status,
        "actual": actual,
        "threshold": threshold,
        "rationale": rationale,
    }


def compact_summary(summary: dict[str, Any]) -> dict[str, Any]:
    return {
        "trade_count": summary.get("trade_count"),
        "win_rate": summary.get("win_rate"),
        "return_pct": summary.get("return_pct"),
        "total_pnl": summary.get("total_pnl"),
        "average_r": summary.get("average_r"),
        "profit_factor": summary.get("profit_factor"),
        "max_drawdown_pct": summary.get("max_drawdown_pct"),
    }


def symbol_concentration(summary: dict[str, Any]) -> dict[str, Any]:
    by_symbol = summary.get("by_symbol") or {}
    positive = sorted(
        (
            (symbol, as_float(item.get("pnl")))
            for symbol, item in by_symbol.items()
            if as_float(item.get("pnl")) > 0
        ),
        key=lambda item: item[1],
        reverse=True,
    )
    gross_profit = sum(pnl for _, pnl in positive)
    net_pnl = as_float(summary.get("total_pnl"))
    top1_symbol = positive[0][0] if positive else None
    top1_pnl = positive[0][1] if positive else 0.0
    top3 = positive[:3]
    top3_pnl = sum(pnl for _, pnl in top3)
    return {
        "gross_profit": round(gross_profit, 2),
        "net_pnl": round(net_pnl, 2),
        "top1_symbol": top1_symbol,
        "top1_pnl": round(top1_pnl, 2),
        "top1_gross_share": round(safe_ratio(top1_pnl, gross_profit), 4),
        "top1_net_share": round(safe_ratio(top1_pnl, net_pnl), 4),
        "top3_symbols": [symbol for symbol, _ in top3],
        "top3_pnl": round(top3_pnl, 2),
        "top3_gross_share": round(safe_ratio(top3_pnl, gross_profit), 4),
        "top3_net_share": round(safe_ratio(top3_pnl, net_pnl), 4),
    }


def positive_month_share(summary: dict[str, Any]) -> float:
    months = summary.get("by_exit_month") or {}
    if not months:
        return 0.0
    positive = sum(1 for item in months.values() if as_float(item.get("pnl")) > 0)
    return positive / len(months)


def min_stress_profit_factor(stress: dict[str, Any]) -> float:
    rows = stress.get("rows") or []
    factors = [
        as_float(row.get("profit_factor"))
        for row in rows
        if row.get("profit_factor") is not None
    ]
    return min(factors) if factors else 0.0


def ablation_dependency(rows: list[dict[str, Any]] | None, *, key: str) -> dict[str, Any]:
    rows = rows or []
    if not rows:
        return {
            "row_count": 0,
            "worst_removed_name": None,
            "worst_removed_return_pct": None,
            "worst_removed_delta_return_pct": None,
        }
    worst = min(rows, key=lambda row: as_float(row.get("delta_return_pct")))
    return {
        "row_count": len(rows),
        "worst_removed_name": worst.get(key),
        "worst_removed_return_pct": as_float(worst.get("return_pct")),
        "worst_removed_delta_return_pct": as_float(worst.get("delta_return_pct")),
        "worst_removed_symbols": worst.get("removed_symbols"),
    }


def config_max_hold_days(report: dict[str, Any]) -> float:
    return as_float(((report.get("config") or {}).get("params") or {}).get("max_hold_days"))


def as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def safe_ratio(numerator: float, denominator: float) -> float:
    if denominator <= 0:
        return 0.0
    return numerator / denominator


def format_validation_summary(report: dict[str, Any], output_path: Path) -> str:
    metrics = report["metrics"]
    lines = [
        "Strategy validation",
        f"Generated: {report['generated_at']}",
        f"Overall status: {report['overall_status']}",
        f"Live readiness: {report['live_readiness']}",
        (
            "5m: "
            f"trades={metrics['5m_backtest']['trade_count']} "
            f"return={metrics['5m_backtest']['return_pct']}% "
            f"pf={metrics['5m_backtest']['profit_factor']} "
            f"dd={metrics['5m_backtest']['max_drawdown_pct']}%"
        ),
        (
            "5m concentration: "
            f"top1={metrics['5m_backtest']['concentration']['top1_symbol']} "
            f"net_share={metrics['5m_backtest']['concentration']['top1_net_share']} "
            f"top3_net_share={metrics['5m_backtest']['concentration']['top3_net_share']}"
        ),
        (
            "Stress: "
            f"positive={metrics['stress']['positive_scenarios']}/{metrics['stress']['scenario_count']} "
            f"min_return={metrics['stress']['min_return_pct']}% "
            f"min_pf={metrics['stress']['min_profit_factor']}"
        ),
        (
            "Ablation: "
            f"symbol_worst={ablation_note(metrics['ablation']['symbol'])} "
            f"theme_worst={ablation_note(metrics['ablation']['theme'])}"
        ),
        "Hourly proxy: " + proxy_summary_note(metrics.get("hourly_proxy")),
        (
            "Daily proxy: "
            f"trades={metrics['daily_proxy']['trade_count']} "
            f"return={metrics['daily_proxy']['return_pct']}% "
            f"pf={metrics['daily_proxy']['profit_factor']} "
            f"month_share={metrics['daily_proxy']['positive_month_share']}"
        ),
    ]
    if report["failed_gates"]:
        lines.append("Failed gates: " + ", ".join(report["failed_gates"]))
    if report["warning_gates"]:
        lines.append("Warning gates: " + ", ".join(report["warning_gates"]))
    lines.append(f"JSON: {output_path}")
    return "\n".join(lines)


def ablation_note(metrics: dict[str, Any] | None) -> str:
    if metrics is None:
        return "missing"
    return f"{metrics.get('worst_removed_name')} return={metrics.get('worst_removed_return_pct')}%"


def proxy_summary_note(metrics: dict[str, Any] | None) -> str:
    if metrics is None:
        return "missing"
    return (
        f"trades={metrics.get('trade_count')} "
        f"return={metrics.get('return_pct')}% "
        f"pf={metrics.get('profit_factor')} "
        f"top1={((metrics.get('concentration') or {}).get('top1_symbol'))}"
    )


if __name__ == "__main__":
    raise SystemExit(main())
