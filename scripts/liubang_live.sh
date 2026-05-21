#!/usr/bin/env bash
if [ -z "${BASH_VERSION:-}" ]; then
  exec /usr/bin/env bash "$0" "$@"
fi

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-"$ROOT/.venv/bin/python"}"

cd "$ROOT"

LOOP_INTERVAL="${LIUBANG_LOOP_SECONDS:-300}"
LOOP_MAX_TICKS="${LIUBANG_LOOP_MAX_TICKS:-0}"
LOOP_ARGS=()

PROFILE_ARGS=(
  --universe config/research_universe_dynamic.json
  --dynamic-source none
  --positions-file data/paper_positions.json
  --hard-stop-pct 0.03
  --symbol-cooldown-days 0
  --cooldown-journal-file data/paper_trade_journal.csv
)

RESEARCH_ARGS=(
  --universe config/research_universe_dynamic.json
  --hard-stop-pct 0.03
  --symbol-cooldown-days 0
)

usage() {
  cat <<'EOF'
Liubang live shortcuts

Usage:
  scripts/liubang_live.sh <command> [extra args]

Commands:
  preflight          检查环境、token、Discord webhook
  signals            生成今日观察信号并推送 Discord
  triggers           扫描盘中触发并推送 Discord
  triggers-heartbeat 扫描盘中触发；无触发也推送心跳
  workflow           跑完整纸面观察 workflow 并推送 Discord
  workflow-cache     同 workflow，但触发扫描使用缓存，适合调试
  day                先生成今日信号，再每 5 分钟循环监控
  loop               使用最新信号，每 5 分钟循环监控
  loop-heartbeat     同 loop，但无触发也推送心跳
  positions          监控 data/manual_positions.json 并推送 Discord
  paper-positions    监控 data/paper_positions.json 并推送 Discord
  paper-summary      汇总 data/paper_trade_journal.csv
  gex                计算单个 ticker 的本周 GEX
  dashboard          生成本地 HTML 监控面板
  dashboard-open     生成并打开本地 HTML 监控面板
  review             生成盘后复盘 JSON 和 HTML
  review-open        生成并打开盘后复盘 HTML
  build-universe     重新生成扩展研究股票池
  compare-scoring    对比 classic、ranked_v1、ranked_v2 选股评分回测
  cross-scoring      搜索 5m 和 1h 同时过线的评分过滤组合
  research           重跑当前通过验证的完整研究套件
  validate           验证最新研究报告

Examples:
  scripts/liubang_live.sh preflight
  scripts/liubang_live.sh signals
  scripts/liubang_live.sh triggers
  scripts/liubang_live.sh triggers-heartbeat
  scripts/liubang_live.sh workflow
  scripts/liubang_live.sh day
  scripts/liubang_live.sh loop
  scripts/liubang_live.sh gex AMD --refresh
  scripts/liubang_live.sh dashboard-open
  scripts/liubang_live.sh review-open
  scripts/liubang_live.sh compare-scoring
  scripts/liubang_live.sh cross-scoring
  scripts/liubang_live.sh positions

Append extra args after any command, for example:
  scripts/liubang_live.sh signals --account-equity 50000
  scripts/liubang_live.sh triggers --current-session-date 2026-05-20
  scripts/liubang_live.sh loop --interval-seconds 120
  scripts/liubang_live.sh loop --max-ticks 1
  scripts/liubang_live.sh dashboard --refresh-seconds 15
  scripts/liubang_live.sh review --review-date 2026-05-20

Loop-only args:
  --interval-seconds N  循环间隔，默认 300，最小 30
  --max-ticks N         最多运行 N 轮；0 表示一直运行，默认 0
EOF
}

run_signal_push() {
  "$PYTHON_BIN" scripts/generate_signals.py "${PROFILE_ARGS[@]}" --send-discord "$@"
}

run_paper_workflow_once() {
  local send_empty="$1"
  shift
  local args=(
    "${PROFILE_ARGS[@]}"
    --skip-signals
    --only-market-window
    --observation-only
    --record-paper-triggers
    --monitor-paper-positions
    --apply-paper-actions
    --summarize-paper-journal
    --send-discord
  )
  if [[ "$send_empty" == "true" ]]; then
    args+=(--send-empty-discord)
  fi
  "$PYTHON_BIN" scripts/run_workflow.py "${args[@]}" "$@"
}

render_dashboard_quiet() {
  if ! "$PYTHON_BIN" scripts/render_dashboard.py >/dev/null; then
    echo "dashboard render failed; continuing" >&2
  fi
}

parse_loop_args() {
  LOOP_INTERVAL="${LIUBANG_LOOP_SECONDS:-300}"
  LOOP_MAX_TICKS="${LIUBANG_LOOP_MAX_TICKS:-0}"
  LOOP_ARGS=()
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --interval-seconds)
        shift
        if [[ $# -eq 0 ]]; then
          echo "--interval-seconds requires a value" >&2
          exit 2
        fi
        LOOP_INTERVAL="$1"
        ;;
      --interval-seconds=*)
        LOOP_INTERVAL="${1#*=}"
        ;;
      --max-ticks)
        shift
        if [[ $# -eq 0 ]]; then
          echo "--max-ticks requires a value" >&2
          exit 2
        fi
        LOOP_MAX_TICKS="$1"
        ;;
      --max-ticks=*)
        LOOP_MAX_TICKS="${1#*=}"
        ;;
      *)
        LOOP_ARGS+=("$1")
        ;;
    esac
    shift
  done
  if ! [[ "$LOOP_INTERVAL" =~ ^[0-9]+$ ]] || [[ "$LOOP_INTERVAL" -lt 30 ]]; then
    echo "Loop interval must be an integer >= 30 seconds: $LOOP_INTERVAL" >&2
    exit 2
  fi
  if ! [[ "$LOOP_MAX_TICKS" =~ ^[0-9]+$ ]]; then
    echo "Loop max ticks must be an integer >= 0: $LOOP_MAX_TICKS" >&2
    exit 2
  fi
}

run_loop() {
  local send_empty="$1"
  shift
  parse_loop_args "$@"
  local tick_count=0
  trap 'echo; echo "Liubang loop stopped."; exit 0' INT TERM
  echo "Liubang loop started. interval=${LOOP_INTERVAL}s. Press Ctrl+C to stop."
  echo "Using latest signal report; run 'scripts/liubang_live.sh signals' first if needed."
  while true; do
    echo
    echo "[$(date '+%Y-%m-%d %H:%M:%S %Z')] workflow tick"
    if ((${#LOOP_ARGS[@]} > 0)); then
      if ! run_paper_workflow_once "$send_empty" "${LOOP_ARGS[@]}"; then
        echo "workflow tick failed; continuing after sleep" >&2
      fi
    else
      if ! run_paper_workflow_once "$send_empty"; then
        echo "workflow tick failed; continuing after sleep" >&2
      fi
    fi
    render_dashboard_quiet
    tick_count=$((tick_count + 1))
    if [[ "$LOOP_MAX_TICKS" -gt 0 && "$tick_count" -ge "$LOOP_MAX_TICKS" ]]; then
      echo "Liubang loop finished after ${tick_count} tick(s)."
      break
    fi
    echo "sleeping ${LOOP_INTERVAL}s..."
    sleep "$LOOP_INTERVAL"
  done
}

command="${1:-help}"
if [[ $# -gt 0 ]]; then
  shift
fi

case "$command" in
  help|-h|--help)
    usage
    ;;
  preflight)
    "$PYTHON_BIN" scripts/preflight.py "$@"
    ;;
  signals)
    run_signal_push "$@"
    render_dashboard_quiet
    ;;
  triggers|trigger)
    "$PYTHON_BIN" scripts/scan_triggers.py --observation-only --send-discord "$@"
    render_dashboard_quiet
    ;;
  triggers-heartbeat|trigger-heartbeat)
    "$PYTHON_BIN" scripts/scan_triggers.py --observation-only --send-discord --send-empty-discord "$@"
    render_dashboard_quiet
    ;;
  workflow)
    "$PYTHON_BIN" scripts/run_workflow.py \
      "${PROFILE_ARGS[@]}" \
      --observation-only \
      --record-paper-triggers \
      --monitor-paper-positions \
      --apply-paper-actions \
      --summarize-paper-journal \
      --send-discord \
      "$@"
    render_dashboard_quiet
    ;;
  workflow-cache)
    "$PYTHON_BIN" scripts/run_workflow.py \
      "${PROFILE_ARGS[@]}" \
      --use-cache-for-triggers \
      --observation-only \
      --record-paper-triggers \
      --monitor-paper-positions \
      --apply-paper-actions \
      --summarize-paper-journal \
      --send-discord \
      "$@"
    render_dashboard_quiet
    ;;
  day)
    parse_loop_args "$@"
    if ((${#LOOP_ARGS[@]} > 0)); then
      run_signal_push "${LOOP_ARGS[@]}"
      run_loop false --interval-seconds "$LOOP_INTERVAL" --max-ticks "$LOOP_MAX_TICKS" "${LOOP_ARGS[@]}"
    else
      run_signal_push
      run_loop false --interval-seconds "$LOOP_INTERVAL" --max-ticks "$LOOP_MAX_TICKS"
    fi
    ;;
  loop|paper-loop)
    run_loop false "$@"
    ;;
  loop-heartbeat|paper-loop-heartbeat)
    run_loop true "$@"
    ;;
  positions)
    "$PYTHON_BIN" scripts/monitor_positions.py --send-discord "$@"
    ;;
  paper-positions)
    "$PYTHON_BIN" scripts/monitor_positions.py --positions data/paper_positions.json --send-discord "$@"
    ;;
  paper-summary)
    "$PYTHON_BIN" scripts/summarize_journal.py \
      --journal data/paper_trade_journal.csv \
      --report-prefix paper_journal \
      "$@"
    render_dashboard_quiet
    ;;
  gex)
    "$PYTHON_BIN" scripts/calc_weekly_gex.py "$@"
    ;;
  dashboard)
    "$PYTHON_BIN" scripts/render_dashboard.py "$@"
    ;;
  dashboard-open)
    "$PYTHON_BIN" scripts/render_dashboard.py --open "$@"
    ;;
  review)
    "$PYTHON_BIN" scripts/generate_daily_review.py "$@"
    ;;
  review-open)
    "$PYTHON_BIN" scripts/generate_daily_review.py --open "$@"
    ;;
  build-universe)
    "$PYTHON_BIN" scripts/build_research_universe.py --dynamic-limit 30 --refresh-dynamic "$@"
    ;;
  compare-scoring)
    "$PYTHON_BIN" scripts/compare_scoring_modes.py "${RESEARCH_ARGS[@]}" "$@"
    ;;
  cross-scoring)
    "$PYTHON_BIN" scripts/cross_validate_scoring_strategies.py \
      --universe config/research_universe_dynamic.json \
      "$@"
    ;;
  research)
    "$PYTHON_BIN" scripts/run_research_suite.py "${RESEARCH_ARGS[@]}" "$@"
    ;;
  validate)
    "$PYTHON_BIN" scripts/validate_strategy.py "$@"
    ;;
  plist-triggers)
    echo "plist-triggers is deprecated. Use: scripts/liubang_live.sh loop" >&2
    exit 2
    ;;
  *)
    echo "Unknown command: $command" >&2
    echo >&2
    usage >&2
    exit 2
    ;;
esac
