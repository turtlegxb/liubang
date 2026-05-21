# 每日信号和盘中触发

信号系统从市场数据生成每日 watchlist。它不需要账户权限，不会下单。

## 运行前检查

```bash
.venv/bin/python scripts/preflight.py
```

更短的 shell 快捷命令：

```bash
scripts/liubang_live.sh preflight
```

## 实盘观察快捷命令

这些命令默认使用当前已验证观察配置，并推送到 Discord：

```bash
scripts/liubang_live.sh signals
scripts/liubang_live.sh triggers
scripts/liubang_live.sh triggers-heartbeat
scripts/liubang_live.sh workflow
scripts/liubang_live.sh day
scripts/liubang_live.sh loop
scripts/liubang_live.sh dashboard-open
scripts/liubang_live.sh positions
```

含义：

- `signals`：生成每日观察信号并推送 Discord
- `triggers`：扫描盘中触发并推送 Discord
- `triggers-heartbeat`：无触发也推送心跳
- `workflow`：完整纸面观察流程
- `day`：先生成今日信号，再每 5 分钟循环监控
- `loop`：使用最新信号，每 5 分钟循环触发和纸面持仓监控
- `dashboard`：生成本地 HTML 监控面板
- `dashboard-open`：生成并打开本地 HTML 监控面板
- `positions`：监控 `data/manual_positions.json` 中的真实手动持仓

可以在后面追加原脚本参数：

```bash
scripts/liubang_live.sh signals --account-equity 50000
scripts/liubang_live.sh triggers --current-session-date 2026-05-20
scripts/liubang_live.sh loop --interval-seconds 120
scripts/liubang_live.sh loop --max-ticks 1
scripts/liubang_live.sh dashboard --refresh-seconds 15
```

`day` 和 `loop` 是前台循环，不使用 plist。按 `Ctrl+C` 停止。`--max-ticks 1` 只跑一轮后退出，适合自检。脚本也兼容 `sh scripts/liubang_live.sh loop`。

HTML 面板默认输出到 `data/dashboard/liubang_dashboard.html`，页面每 30 秒自动刷新。`loop` 每一轮结束后会自动重写 HTML，因此浏览器刷新后会看到最新 workflow、trigger 和 paper 持仓状态。

当前面板会显示：

- 最新 workflow 状态和最近 10 次 tick 历史
- signals 是否对当前美东交易日有效
- trigger 状态分布和未触发原因聚合
- watchlist、paper 持仓、paper 日志
- 有开放 paper 持仓时的浮动 R、距离止损、距离目标
- 当前建议运行的 shell 命令

## 生成每日信号

```bash
.venv/bin/python scripts/generate_signals.py
```

默认股票池来自 `config/core_universe.json`，并叠加 yfinance 动态流动性补充。SPY 和 QQQ 自动加入，用于市场 regime；XLK 和 SMH 自动加入，用于行业上下文。

当前已验证观察配置使用已生成的扩展研究股票池，并关闭二次动态补充：

```bash
.venv/bin/python scripts/generate_signals.py \
  --universe config/research_universe_dynamic.json \
  --dynamic-source none \
  --hard-stop-pct 0.03 \
  --symbol-cooldown-days 3
```

默认评分模式是 `ranked_v2`。下面命令即使不写 `--scoring-mode ranked_v2` 也会使用 v2；显式写出方便确认当前口径：

```bash
.venv/bin/python scripts/generate_signals.py \
  --universe config/research_universe_dynamic.json \
  --dynamic-source none \
  --hard-stop-pct 0.03 \
  --symbol-cooldown-days 3 \
  --scoring-mode ranked_v2
```

`ranked_v2` 只在 `classic` 主分数上做小幅排序 overlay；它偏向 20/60 日相对 QQQ 更强且仍站在 SMA20 上方的候选。

当前默认还会根据大盘 regime 自动收紧：

- `strong`：启用严格过滤，要求 `rs20_rank >= 0.65`、`overlay >= 7.4`、`atr20_pct <= 0.08`、`pullback <= 5%`
- `neutral`：沿用普通 `ranked_v2`
- `weak`：不生成新开仓候选

需要回退旧评分口径时传入 `--scoring-mode classic`。

手动指定股票会覆盖 universe，并禁用动态补充：

```bash
.venv/bin/python scripts/generate_signals.py --symbols AAPL,NVDA,TSLA
```

关闭动态补充：

```bash
.venv/bin/python scripts/generate_signals.py --dynamic-source none
```

刷新 Schwab 历史：

```bash
.venv/bin/python scripts/generate_signals.py --refresh
```

使用 yfinance 作为历史来源：

```bash
.venv/bin/python scripts/generate_signals.py --history-provider yfinance
```

刷新外部缓存：

```bash
.venv/bin/python scripts/generate_signals.py --refresh-earnings
.venv/bin/python scripts/generate_signals.py --refresh-news
.venv/bin/python scripts/generate_signals.py --refresh-dynamic
.venv/bin/python scripts/generate_signals.py --refresh-options
```

设置账户和风险假设：

```bash
.venv/bin/python scripts/generate_signals.py \
  --account-equity 50000 \
  --risk-per-trade-pct 0.01 \
  --max-position-pct 0.20
```

默认仓位假设：

- 账户权益：100,000 美元
- 单笔风险：1%
- 单票资金上限：20%
- 默认硬止损：4%
- 当前观察配置硬止损：3%
- 第一目标：1R

## 组合保护

如果 `data/manual_positions.json` 存在，信号生成会读取它并加入组合保护：

- strong：最多 3 个持仓
- neutral：最多 2 个持仓
- weak：最多 1 个持仓，且半仓
- 总敞口按 `entry_price * remaining_shares` 估计
- 如果没有持仓槽位或敞口空间，则阻止新入场

跳过组合保护：

```bash
.venv/bin/python scripts/generate_signals.py --ignore-positions
```

指定持仓文件：

```bash
.venv/bin/python scripts/generate_signals.py --positions-file data/manual_positions.json
```

## 风险熔断和单票冷却

如果 `data/trade_journal.csv` 存在，系统会按真实或手工录入的已关闭交易执行风险熔断。默认触发条件：

- 连续 3 笔亏损
- 最近 5 笔合计亏损达到 3R
- 当月已关闭交易亏损达到 4R

跳过风险熔断：

```bash
.venv/bin/python scripts/generate_signals.py --ignore-risk-throttle
```

单票冷却用于防止刚退出的股票立刻重新入选。当前观察配置使用 3 个交易日：

```bash
.venv/bin/python scripts/generate_signals.py --symbol-cooldown-days 3
```

默认从 `--journal-file` 读取冷却依据。纸面观察时建议指定纸面日志：

```bash
.venv/bin/python scripts/generate_signals.py \
  --symbol-cooldown-days 3 \
  --cooldown-journal-file data/paper_trade_journal.csv
```

## Discord

配置 webhook：

```bash
python scripts/configure_discord_webhook.py
```

发送信号摘要：

```bash
.venv/bin/python scripts/generate_signals.py --send-discord
```

webhook 写入本地 `.env`，该文件被 git 忽略。

## 盘中触发扫描

生成 watchlist 后，扫描 5 分钟入场确认：

```bash
.venv/bin/python scripts/scan_triggers.py
```

触发扫描默认刷新 5 分钟历史，只评估 regular-hours K 线，跳过第一根 5 分钟 K。触发条件：

- 最新 5 分钟收盘价高于 running VWAP
- 最新 5 分钟收盘价高于上一根 5 分钟 K 的高点

使用缓存而不刷新：

```bash
.venv/bin/python scripts/scan_triggers.py --use-cache
```

观察模式：

```bash
.venv/bin/python scripts/scan_triggers.py --observation-only
```

观察模式仍记录技术触发，但不输出 `manual_position_template`，`actionable_triggered_count` 保持为 0。

推送触发到 Discord：

```bash
.venv/bin/python scripts/scan_triggers.py --send-discord
```

默认只有触发时推送。需要无触发心跳时加：

```bash
.venv/bin/python scripts/scan_triggers.py --send-empty-discord
```

## 触发模板和纸面观察

导出可人工检查的触发模板：

```bash
.venv/bin/python scripts/export_trigger_templates.py
```

文件会写入 `data/exports/manual_position_templates_*.json`。实际使用前应按真实成交价和股数调整。

把观察模式触发记录到纸面持仓：

```bash
.venv/bin/python scripts/record_paper_triggers.py
```

它会写入 `paper_positions_update_*.json`，并把新标的追加到 `data/paper_positions.json`。已有开放纸面持仓的标的会被跳过。

监控纸面持仓：

```bash
.venv/bin/python scripts/monitor_positions.py --positions data/paper_positions.json
```

把纸面监控动作应用到纸面持仓和纸面日志：

```bash
.venv/bin/python scripts/apply_paper_actions.py
```

它会：

- 写入 `paper_actions_*.json`
- 追加 lot 到 `data/paper_trade_journal.csv`
- 更新部分止盈后的剩余仓位
- 删除完全关闭的纸面持仓
- 不修改 `data/manual_positions.json`

汇总纸面交易：

```bash
.venv/bin/python scripts/summarize_journal.py \
  --journal data/paper_trade_journal.csv \
  --report-prefix paper_journal
```

## 输出内容

信号 JSON 报告包含：

- 市场 regime
- XLK 和 SMH 的上下文 regime
- 动态股票池摘要
- 数据覆盖范围
- 数据质量状态
- watchlist 主题和来源集中度
- 排序后的 watchlist
- 标的来源：`fixed_core`、`dynamic_yfinance` 或 `manual_override`
- 核心股票池 metadata，例如 theme
- 动态股票池的 screener 和流动性 metadata
- Schwab/yfinance 数据来源统计
- strength score
- pullback score
- pullback percentage
- 默认 1% 到 6% 的回撤过滤
- 技术止损参考
- 硬止损规则
- 第一目标规则
- 剩余仓位退出规则
- trade plan：止损、第一目标、建议股数、最大美元风险、约束来源
- 盘中触发条件
- 下一次财报风险提示
- yfinance 近期新闻
- Schwab 期权链摘要
- 新闻和期权风险标记，这些标记不提高买入分数

触发报告包含：

- 每个标的的触发状态
- 原始触发数量和可行动触发数量
- 盘中数据来源统计
- 最新 regular-hours 5 分钟收盘价
- running VWAP
- 上一根 5 分钟 K 高点
- trigger price reference
- stop reference
- 从触发价重新计算的建议股数
- 可选 `manual_position_template`
- observation-only 模式下的诊断
- 组合保护状态
- 风险熔断状态
- 重复持仓保护

## 手动持仓监控

手动下单后，把开放持仓维护在 `data/manual_positions.json`。格式参考：

```bash
mkdir -p data
cp config/manual_positions.example.json data/manual_positions.json
.venv/bin/python scripts/monitor_positions.py
```

监控内容：

- 当前止损或 breakeven stop
- 第一目标部分止盈
- 第一目标后移动止损提示
- 默认 5 个交易日的时间退出
- 开放持仓的数据来源统计

只把需要行动的持仓提醒推送到 Discord：

```bash
.venv/bin/python scripts/monitor_positions.py --send-discord
```

## 交易日志

已关闭交易可记录到 `data/trade_journal.csv`。从示例文件开始：

```bash
mkdir -p data
cp config/trade_journal.example.csv data/trade_journal.csv
.venv/bin/python scripts/summarize_journal.py
```

必需列：

- `trade_id`
- `symbol`
- `side`
- `entry_date`
- `exit_date`
- `entry_price`
- `exit_price`
- `shares`
- `initial_stop_price`

可选列：

- `fees`
- `setup`
- `source`
- `notes`

部分止盈使用同一个 `trade_id`。汇总会把 lot 聚合成 trade，并输出 PnL、胜率、average R、持仓交易日数、profit factor 和最大回撤。

## 标准 workflow

运行常规市场数据流程：

```bash
.venv/bin/python scripts/run_workflow.py --use-cache-for-triggers
```

workflow 会依次运行：

- `generate_signals.py`
- `scan_triggers.py`
- 如果 `data/manual_positions.json` 存在，运行 `monitor_positions.py`
- 如果 `data/trade_journal.csv` 存在，运行 `summarize_journal.py`

当前推荐纸面观察流程：

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

常用 workflow 参数：

- `--send-discord`：每步复用 Discord 推送
- `--send-empty-discord`：允许无触发心跳
- `--export-watchlist-csv`：生成 flat watchlist CSV
- `--export-trigger-templates`：生成手动入场模板
- `--record-paper-triggers`：把 observation trigger 写入纸面持仓
- `--monitor-paper-positions`：监控纸面持仓
- `--apply-paper-actions`：应用纸面退出/部分止盈动作
- `--summarize-paper-journal`：生成纸面日志汇总

重复盘中运行时，加 `--only-market-window`，让流程在纽约时间常规交易窗口外直接跳过：

```bash
.venv/bin/python scripts/run_workflow.py \
  --skip-signals \
  --use-cache-for-triggers \
  --observation-only \
  --record-paper-triggers \
  --monitor-paper-positions \
  --apply-paper-actions \
  --summarize-paper-journal \
  --only-market-window
```

更推荐直接使用快捷循环：

```bash
scripts/liubang_live.sh day
```

它会先生成今日信号，然后用最新信号每 5 分钟运行一次 trigger/paper workflow，并自动带上 `--only-market-window`。

## 前台循环和可选定时

默认不需要 plist。盘中直接开一个终端跑：

```bash
scripts/liubang_live.sh day
```

如果当天信号已经生成过，只需要继续监控：

```bash
scripts/liubang_live.sh loop
```

下面是可选的 macOS launchd plist 生成器，只在你明确想交给 launchd 托管时使用：

```bash
.venv/bin/python scripts/render_launchd_plist.py \
  --hour 9 \
  --minute 35 \
  --use-cache-for-triggers \
  --export-watchlist-csv \
  --export-trigger-templates
```

plist 写入 `data/launchd/`，该目录被 git 忽略。先检查文件，再手动复制到 `~/Library/LaunchAgents/` 并用 `launchctl` 加载。

盘中触发检查建议使用 interval plist 加 ET 市场窗口保护：

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

## CSV 和清理

导出最新 watchlist CSV：

```bash
.venv/bin/python scripts/export_watchlist_csv.py
```

CSV 包含：theme/source metadata、watchlist 集中度、分数组成、计划入场日、entry/stop/target 参考、建议股数、组合保护、风险熔断、新闻风险、期权风险、yfinance 第一条新闻。

旧报告清理 dry-run：

```bash
.venv/bin/python scripts/prune_reports.py --keep 30
```

确认 dry-run 输出后再加 `--apply`。

## 当前限制

- 财报过滤默认来自 yfinance。
- 新闻默认来自 yfinance，只做风险上下文，不影响分数。
- 期权上下文来自 Schwab，只用于最终 watchlist，不影响分数。
- 动态 yfinance 候选默认用于信号生成；历史回测应使用明确的 universe 文件，避免未来函数。
