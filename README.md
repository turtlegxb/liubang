# Liubang

Liubang 是一个面向美股短线选股的本地信号系统。当前定位是“市场数据 + 手动下单”：系统生成候选、触发、风控和纸面观察记录，但不自动下单，也不需要 Schwab 账户权限。

## 当前范围

- Schwab 市场数据探针
- yfinance 数据补缺：财报日、新闻、筛选器，以及可选 5 分钟历史
- 固定核心股票池
- 从 yfinance screener 生成的扩展研究股票池
- 强势股浅回撤策略回测
- 每日 watchlist 生成
- 盘中 5 分钟触发扫描
- 本地手动持仓监控
- 重复持仓和总敞口保护
- 基于交易日志的风险熔断
- 当周期权链 GEX 上下文
- Discord 推送
- 离线 smoke tests

## 安装

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Schwab 环境变量会按顺序从当前环境、`.env`、`~/.zshrc` 读取：

```bash
SCHWAB_APP_KEY=...
SCHWAB_APP_SECRET=...
SCHWAB_TOKEN_PATH=...
```

Discord 可选：

```bash
DISCORD_WEBHOOK_URL=...
```

## 常用命令

最简单的实盘观察入口：

```bash
scripts/liubang_live.sh preflight
scripts/liubang_live.sh signals
scripts/liubang_live.sh triggers
scripts/liubang_live.sh workflow
scripts/liubang_live.sh day
scripts/liubang_live.sh loop
scripts/liubang_live.sh gex AMD --refresh
scripts/liubang_live.sh dashboard-open
scripts/liubang_live.sh review-open
scripts/liubang_live.sh positions
```

这些 shell 快捷命令默认使用当前已验证观察配置：

- `config/research_universe_dynamic.json`
- `--dynamic-source none`
- `--positions-file data/paper_positions.json`
- `--hard-stop-pct 0.03`
- `--symbol-cooldown-days 3`
- `--cooldown-journal-file data/paper_trade_journal.csv`
- Discord 推送开启

查看全部快捷命令：

```bash
scripts/liubang_live.sh help
```

盘中不使用 plist 的前台循环：

```bash
# 先生成今日信号，再进入 5 分钟循环；Ctrl+C 停止
scripts/liubang_live.sh day
```

如果已经生成过信号，只循环触发和纸面持仓监控：

```bash
scripts/liubang_live.sh loop
```

调整循环间隔：

```bash
scripts/liubang_live.sh loop --interval-seconds 120
```

只跑一轮自检后退出：

```bash
scripts/liubang_live.sh loop --max-ticks 1
```

如果习惯用 `sh` 调用，也可以：

```bash
sh scripts/liubang_live.sh loop
```

生成本地 HTML 监控面板：

```bash
scripts/liubang_live.sh dashboard
```

默认输出到 `data/dashboard/liubang_dashboard.html`，HTML 每 30 秒自动刷新。`loop` 每一轮结束后会自动重写这个 HTML；如果要生成并打开：

```bash
scripts/liubang_live.sh dashboard-open
```

面板包含：

- 最新 workflow 状态和最近 10 次 tick 历史
- signals 是否对当前美东交易日有效
- trigger 状态分布和未触发原因聚合
- watchlist、当周 GEX、paper 持仓、paper 日志
- 有开放 paper 持仓时显示浮动 R、距离止损和距离目标
- 当前建议运行的 shell 命令

生成盘后复盘报告：

```bash
scripts/liubang_live.sh review
```

默认按当前美东日期复盘，输出：

- `data/exports/review_*.json`：结构化日级样本，可作为后续统计样本库
- `data/reviews/liubang_review_YYYY-MM-DD.html`：本地 HTML 复盘页面

生成并打开 HTML：

```bash
scripts/liubang_live.sh review-open
```

复盘报告会汇总当日 signals、triggers、workflow、paper 持仓更新、paper 退出动作、开放 paper 持仓和 paper 交易日志。常用在美股收盘后或第二天盘前运行：

```bash
scripts/liubang_live.sh review --review-date 2026-05-20
```

检查 Schwab 市场数据：

```bash
.venv/bin/python scripts/schwab_probe.py
```

运行离线检查：

```bash
.venv/bin/python scripts/smoke_tests.py
```

运行本地环境预检：

```bash
.venv/bin/python scripts/preflight.py
```

计算单个 ticker 的本周 GEX：

```bash
scripts/liubang_live.sh gex AMD --refresh
```

该值来自 Schwab option chain 的 `gamma` 和 `openInterest`，按 `call` 为正、`put` 为负计算，单位是标的上涨 1% 时的美元 gamma exposure 代理值。它只作为当日 watchlist 的风险/上下文，不会自动下单。

生成默认每日信号：

```bash
.venv/bin/python scripts/generate_signals.py
```

生成当前已验证的观察配置：

```bash
.venv/bin/python scripts/generate_signals.py \
  --universe config/research_universe_dynamic.json \
  --dynamic-source none \
  --hard-stop-pct 0.03 \
  --symbol-cooldown-days 3
```

使用自己的账户和仓位假设：

```bash
.venv/bin/python scripts/generate_signals.py --account-equity 50000 --risk-per-trade-pct 0.01
```

生成信号并推送到 Discord：

```bash
.venv/bin/python scripts/generate_signals.py --send-discord
```

导出最新 watchlist CSV：

```bash
.venv/bin/python scripts/export_watchlist_csv.py
```

扫描最新 watchlist 的 5 分钟触发：

```bash
.venv/bin/python scripts/scan_triggers.py
```

推送盘中触发到 Discord：

```bash
.venv/bin/python scripts/scan_triggers.py --send-discord
```

导出可人工检查的触发模板：

```bash
.venv/bin/python scripts/export_trigger_templates.py
```

监控手动录入的持仓：

```bash
mkdir -p data
cp config/manual_positions.example.json data/manual_positions.json
.venv/bin/python scripts/monitor_positions.py
```

汇总已关闭交易：

```bash
mkdir -p data
cp config/trade_journal.example.csv data/trade_journal.csv
.venv/bin/python scripts/summarize_journal.py
```

运行标准 workflow：

```bash
.venv/bin/python scripts/run_workflow.py --use-cache-for-triggers
```

运行当前推荐的纸面观察 workflow：

```bash
.venv/bin/python scripts/run_workflow.py \
  --universe config/research_universe_dynamic.json \
  --dynamic-source none \
  --hard-stop-pct 0.03 \
  --symbol-cooldown-days 3 \
  --cooldown-journal-file data/paper_trade_journal.csv \
  --use-cache-for-triggers \
  --export-watchlist-csv \
  --export-trigger-templates \
  --observation-only \
  --record-paper-triggers \
  --monitor-paper-positions \
  --apply-paper-actions \
  --summarize-paper-journal
```

不需要 plist 时，使用前面的 `day` 或 `loop` 前台循环即可。下面是可选的 macOS launchd plist 生成器，只在你明确想交给 launchd 托管时使用：

```bash
.venv/bin/python scripts/render_launchd_plist.py \
  --hour 9 \
  --minute 35 \
  --use-cache-for-triggers \
  --export-watchlist-csv \
  --export-trigger-templates
```

生成每 5 分钟运行一次、但只在美股常规交易窗口内执行的 trigger-only plist：

```bash
.venv/bin/python scripts/render_launchd_plist.py \
  --start-interval-seconds 300 \
  --only-market-window \
  --skip-signals \
  --use-cache-for-triggers \
  --observation-only \
  --record-paper-triggers \
  --monitor-paper-positions \
  --apply-paper-actions \
  --summarize-paper-journal \
  --export-trigger-templates
```

运行基础回测：

```bash
.venv/bin/python scripts/run_backtest.py
```

运行 yfinance 日线长历史代理回测：

```bash
.venv/bin/python scripts/run_daily_proxy_backtest.py
```

运行 yfinance 小时级代理回测：

```bash
.venv/bin/python scripts/run_hourly_proxy_backtest.py
```

验证最新研究报告是否通过门槛：

```bash
.venv/bin/python scripts/validate_strategy.py
```

一条命令重跑完整研究套件：

```bash
.venv/bin/python scripts/run_research_suite.py \
  --universe config/research_universe_dynamic.json \
  --hard-stop-pct 0.03 \
  --symbol-cooldown-days 3
```

重新生成扩展研究股票池：

```bash
.venv/bin/python scripts/build_research_universe.py --dynamic-limit 30
```

导出回测交易：

```bash
.venv/bin/python scripts/run_backtest.py --trades-csv data/exports/latest_backtest_trades.csv
```

分析最新回测：

```bash
.venv/bin/python scripts/analyze_backtest.py
```

运行单票消融：

```bash
.venv/bin/python scripts/ablate_symbols.py
```

运行主题消融：

```bash
.venv/bin/python scripts/ablate_themes.py
```

运行参数 sweep：

```bash
.venv/bin/python scripts/sweep_backtest.py
```

运行紧凑压力测试：

```bash
.venv/bin/python scripts/stress_backtest.py
```

清理旧报告的 dry-run：

```bash
.venv/bin/python scripts/prune_reports.py --keep 30
```

## 数据来源

默认数据路径：

- Schwab：5 分钟历史、报价、期权链上下文
- yfinance：财报日
- yfinance：新闻上下文
- yfinance：动态股票池候选
- yfinance：Schwab 缺失 5 分钟历史时的可选 fallback

信号、触发、持仓、回测报告都会包含 `history_sources`，用于查看每只股票来自 Schwab 还是 yfinance fallback。

强制使用 yfinance 历史：

```bash
.venv/bin/python scripts/generate_signals.py --history-provider yfinance
```

刷新外部缓存：

```bash
.venv/bin/python scripts/generate_signals.py --refresh-dynamic --refresh-earnings --refresh-news --refresh-options
```

## 输出

这些本地输出被 git 忽略：

- `data/cache/`
- `data/exports/`
- `data/dashboard/`
- `data/reviews/`
- `data/manual_positions.json`
- `data/paper_positions.json`
- `data/trade_journal.csv`
- `data/paper_trade_journal.csv`
- `.env`

主要文档：

- `docs/strategy_design.md`
- `docs/research_log.md`
- `docs/backtest.md`
- `docs/signals.md`
- `docs/schwab_probe.md`
- `docs/session_share_2026-05-20.md`
- `docs/conversation_log_2026-05-20.md`

## 当前已验证观察配置

最新通过验证的配置：

- 股票池：`config/research_universe_dynamic.json`
- 每日信号运行时使用 `--dynamic-source none`，避免在研究股票池之外再次加入新标的
- 市场上下文：SPY、QQQ
- 行业上下文：XLK、SMH
- 最低分数：7.0
- 回撤范围：1% 到 6%
- 硬止损上限：3%
- 单票冷却：已关闭交易后 3 个交易日
- 仓位参考：100,000 美元权益、单笔 1% 风险、单票 20% 资金上限
- 财报过滤：yfinance
- 新闻上下文：yfinance
- 期权上下文：Schwab，仅用于最终 watchlist

2026-05-20 最新完整验证：

```text
status=pass
readiness=observation_ready
5m_return=9.887%
5m_profit_factor=1.4265
5m_max_drawdown=6.292%
stress_positive=7/7
hourly_proxy_return=27.728%
daily_proxy_return=125.531%
```

2026-05-20 最新观察配置 watchlist：

```text
PANW, CSCO, AAPL, ASTS, CRWD, ARM, ALAB, SNOW
```

## 边界

当前系统只适合观察和纸面跟踪。它不会下单，也不应被当成自动交易系统。下一阶段应继续收集纸面交易和手动交易日志，再决定是否提高 readiness。
