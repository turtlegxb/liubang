# 研究日志

## 2026-05-20 扩展动态研究股票池验证

问题：

- 25 只核心股票池有正收益，但验证失败，因为净收益过度依赖 MU 和相邻半导体主题。
- 单纯加入 5 到 20 天单票冷却可以降低集中度，但会损坏 5 分钟样本收益，或无法通过小时代理集中度。

改动：

- 新增 `scripts/build_research_universe.py`
- 生成 `config/research_universe_dynamic.json`
- 核心标的：25 只
- yfinance screener 新增标的：30 只
- 总可交易标的：55 只
- 上下文标的：SPY、QQQ、XLK、SMH
- 验证参数：
  - `min_score=7.0`
  - `min_pullback_pct=0.01`
  - `max_pullback_pct=0.06`
  - `hard_stop_pct=0.03`
  - `symbol_cooldown_days=3`

动态新增标的：

```text
SNDK, INTC, GOOG, MRVL, LLY, RKLB, CSCO, NBIS, QCOM, WDC, STX, CRWV,
ALAB, WMT, ASTS, IREN, DDOG, BAC, ADI, CRDO, IONQ, CIEN, AXTI, NOK,
APH, NEE, HOOD, TER, BSX, TGT
```

最新完整研究套件：

```text
command=.venv/bin/python scripts/run_research_suite.py --universe config/research_universe_dynamic.json --hard-stop-pct 0.03 --symbol-cooldown-days 3
report=data/exports/research_suite_20260520_120429_762272.json
validation=data/exports/strategy_validation_20260520_120429_758445.json
status=pass
readiness=observation_ready
```

验证指标：

```text
5m: trades=120 return=9.887% pf=1.4265 max_drawdown=6.292%
5m concentration: top1=MRVL net_share=0.3631 top3_net_share=0.8096
stress: positive=7/7 min_return=0.49% min_pf=1.0179
symbol ablation worst=MU return=3.965%
theme ablation worst=memory_semiconductor return=3.965%
hourly proxy: trades=312 return=27.728% pf=1.3802
daily proxy: trades=955 return=125.531% pf=1.6931 positive_month_share=0.7333
```

解释：

这组配置可以用于观察和纸面跟踪，但不等于可以自动交易。最弱点是 `strict_score_9` 压力场景，虽然仍为正，但余量很薄。下一步应继续积累纸面交易和手动交易日志。

## 2026-05-20 核心股票池基础 sweep

股票池：

- `config/core_universe.json`
- 25 只核心标的
- SPY 和 QQQ 用于市场 regime

数据：

- Schwab 5 分钟 K 线
- 每只标的 180 根 regular-session 日聚合 bar
- 时间范围：2025-09-02 到 2026-05-19

网格：

- `min_score`: 5、6、7
- `max_pullback_pct`: 2.5%、3%、4%
- `hard_stop_pct`: 2%、3%
- `min_pullback_pct`: 1%

小网格最好结果：

```text
min_score=7.0
max_pullback_pct=0.04
hard_stop_pct=0.03
trades=118
win_rate=57.63%
return=1.192%
max_drawdown=6.892%
```

决定：

- 第一版实用默认 `min_score=7.0`
- 初始回撤过滤 1% 到 4%
- 初始硬止损 3%

解释：

这不足以支持实盘，只说明 `min_score=5.0` 在当前样本里放进了太多弱候选。

## 2026-05-20 yfinance 财报过滤

数据：

- yfinance `get_earnings_dates`
- 缓存：`data/cache/earnings_calendar_yfinance.json`
- 首次完整刷新抓取 1106 条财报事件

规则：

- 财报日前一个交易日不新开仓
- 财报日不新开仓
- 持仓遇到财报风险日退出

带财报过滤结果：

```text
min_score=7.0
max_pullback_pct=0.04
hard_stop_pct=0.03
trades=121
win_rate=56.20%
return=-0.458%
max_drawdown=9.037%
```

不带财报过滤对照：

```text
trades=118
win_rate=57.63%
return=1.192%
max_drawdown=6.892%
```

解释：

财报过滤在这个短样本里降低收益，但它是风险约束，不是收益优化器。

## 2026-05-20 扩大回撤和止损 sweep

在带财报过滤默认结果转负后，扩大参数网格。

网格：

- `min_score`: 7、8、9
- `max_pullback_pct`: 4%、5%、6%
- `hard_stop_pct`: 3%、4%、5%

最好结果：

```text
min_score=7.0
max_pullback_pct=0.06
hard_stop_pct=0.04
trades=113
win_rate=57.52%
return=7.159%
max_drawdown=5.647%
average_r=0.0374
```

决定：

- 回撤窗口调整到 1% 到 6%
- 当时的硬止损默认调整到 4%
- 保持 `min_score=7.0`

解释：

这只是更好的中间基线，不是最终策略。它提示最初 3% 硬止损对 5 天持仓设计可能偏紧。后来扩展股票池验证又把观察配置调回 3%。

## 2026-05-20 yfinance 历史 fallback

新增 yfinance 作为 5 分钟历史补缺来源。

行为：

- 默认仍优先 Schwab
- Schwab 无 K 线时可用 yfinance 补缺
- `--history-provider yfinance` 可直接使用 yfinance
- yfinance 5 分钟缓存：`data/cache/yfinance_5m_60d_SYMBOL.json`
- yfinance 刷新异常会转成错误 payload
- 如果已有可用缓存，临时刷新失败时保留旧缓存并标记 `stale_due_to_empty_refresh`

解释：

yfinance 适合补缺和快速检查，但 Schwab 当前能返回更长 5 分钟窗口，仍是优先盘中数据源。

## 2026-05-20 行业上下文 ETF

新增 XLK 和 SMH。

行为：

- SPY 和 QQQ 继续驱动市场 regime
- XLK 和 SMH 只做上下文，不交易
- 信号报告显示最新上下文 regime

最新上下文示例：

```text
XLK=neutral
SMH=weak
```

## 2026-05-20 yfinance 新闻上下文

新增 yfinance 新闻。

行为：

- 只抓配置股票池中的标的
- 缓存：`data/cache/news/`
- 在 watchlist 候选中显示最近标题
- Discord 摘要包含截断后的第一条标题
- 不作为买入触发，不参与打分
- 临时刷新失败时保留旧缓存并标记 `stale_due_to_fetch_error`

## 2026-05-20 Schwab 期权上下文

新增最终 watchlist 标的的 option-chain 摘要。

行为：

- 只抓最终 watchlist，不抓全股票池
- 来源：Schwab option chain
- 默认 strike count 为 10，最大 DTE 为 45
- 缓存：`data/cache/options/`
- 摘要包含 call/put volume、open interest、put/call ratios、平均 bid/ask spread
- 只做上下文，不参与分数

## 2026-05-20 yfinance 动态股票池

新增 yfinance screener 补充。

行为：

- 默认 screeners：`most_actives`、`day_gainers`、`growth_technology_stocks`
- 缓存：`data/cache/dynamic_universe_yfinance.json`
- 排除固定核心池、SPY、QQQ、XLK、SMH
- 当前成交额要求大于 10 亿美元
- 10 日平均成交额代理要求大于 3 亿美元
- 价格大于 5 美元
- 市值大于 20 亿美元
- 入选标的标记为 `dynamic_yfinance`

动态 screener 用于实时信号。历史回测需要先生成明确的 research universe，避免使用今天的 screener membership 造成未来函数。

## 2026-05-20 盘中触发扫描器

新增 `scripts/scan_triggers.py`。

行为：

- 默认读取最新 `signals_*.json`
- 除非传入 `--use-cache`，否则刷新 5 分钟历史
- 只评估 regular-hours bar
- 跳过第一根 5 分钟 K
- 最新 5 分钟收盘价高于 running VWAP 且高于前一根 5 分钟 K 高点时触发
- 重新计算 trigger price reference 和 stop reference
- 可推送到 Discord

该层仍不下单，只做人工执行提醒。

## 2026-05-20 手动持仓监控

新增 `scripts/monitor_positions.py`。

行为：

- 读取 `data/manual_positions.json`
- 示例结构：`config/manual_positions.example.json`
- 默认刷新 5 分钟历史
- 触及 stop 时提醒
- 触及第一目标时提示卖出一半
- 第一目标后提示剩余仓位止损移动到 breakeven
- 默认第 5 个交易日提醒时间退出
- 可推送需要行动的提醒到 Discord

这完成第一版 market-data-only 闭环：watchlist、盘中触发、手动持仓退出监控。

## 2026-05-20 Trade Plan 和仓位

新增共享 trade-plan 模块。

默认：

- 账户权益 100,000 美元
- 单笔风险 1%
- 单票资金上限 20%
- 默认硬止损 4%
- weak regime 半仓
- 每日信号用最新收盘价做计划 entry reference
- 盘中触发从实际 trigger-price reference 重新计算

报告包含 stop price、first target、risk per share、suggested shares、suggested dollar risk、maximum allowed dollar risk、binding constraint。

后续补充：

- trigger 评估输出 `manual_position_template`
- 模板使用 trigger reference price、建议股数、stop、first target
- 交易者应按真实成交价和股数调整后再放入持仓监控

## 2026-05-20 组合保护

新增本地组合保护。

行为：

- 读取 `data/manual_positions.json`
- 统计 `remaining_shares > 0` 的持仓
- strong 最多 3 个，neutral 最多 2 个，weak 最多 1 个
- 估算当前总敞口：`entry_price * remaining_shares`
- 新入场必须同时满足有持仓槽位和有总敞口空间
- trigger 报告继承组合保护状态

该设计不需要 Schwab 账户权限。

## 2026-05-20 标准 workflow

新增 `scripts/run_workflow.py`。

行为：

- 生成信号
- 扫描触发
- 如果 `data/manual_positions.json` 存在，监控持仓
- 如果 `data/trade_journal.csv` 存在，汇总日志
- 报告写入 `data/exports/workflow_*.json`
- `--send-discord` 透传 Discord 推送
- `--use-cache-for-triggers` 和 `--use-cache-for-positions` 可避免重复刷新

## 2026-05-20 新闻和期权风险标记

新增轻量风险标记。

新闻关键词包括 lawsuit、investigation、downgrade、offering、dilution、outage、breach、guidance cut 等。

期权风险包括：

- put/call open-interest ratio 过高
- put/call volume ratio 过高
- 平均 option spread 过宽
- option chain 过稀疏

这些标记加入 `risk_notes`、`news_risk`、`options_risk`，不提高买入分数。

## 2026-05-20 交易日志汇总

新增 `scripts/summarize_journal.py` 和示例 `config/trade_journal.example.csv`。

行为：

- 读取 `data/trade_journal.csv`
- 按 `trade_id` 聚合部分退出
- 输出 realized PnL、win rate、average R、profit factor、holding weekdays、max drawdown
- 按 setup 和 symbol 分组
- workflow 在日志文件存在时自动汇总

## 2026-05-20 launchd plist 生成器

新增 `scripts/render_launchd_plist.py`。

行为：

- 为 `scripts/run_workflow.py` 生成 macOS launchd plist
- 默认输出：`data/launchd/com.liubang.workflow.plist`
- stdout/stderr 写入 `logs/`
- 不自动安装或加载 plist

## 2026-05-20 数据质量警告

新增信号报告数据质量检查。

行为：

- 比较预期标的和可用历史
- 少于 25 根日 bar 的标的被标记
- 最新日期落后的标的被标记 stale
- 控制台摘要显示数据质量状态和 warning categories

## 2026-05-20 报告清理

新增 `scripts/prune_reports.py`。

行为：

- 扫描被 git 忽略的 `data/exports/`
- 每个报告前缀保留最新 N 个，默认 30
- 默认 dry-run
- 只有传入 `--apply` 才删除

## 2026-05-20 回测诊断增强

新增：

- entry attempts
- filled entries
- unfilled entries
- duplicate-symbol skips
- missing-intraday skips
- no-slot skips
- 单票冷却 skips
- 每笔 initial risk、R multiple、holding weekdays、final exit date、primary exit reason
- average R、profit factor、average holding weekdays
- 按退出原因和退出月份汇总
- `scripts/run_backtest.py --trades-csv ...`
- `scripts/analyze_backtest.py`

当时扩展止损/回撤基线结果：

```text
candidates=901
entry_attempts=120
filled_entries=113
unfilled_entries=7
skipped_no_slot=767
trades=113
win_rate=57.52%
return=7.159%
average_r=0.0374
profit_factor=1.3363
max_drawdown=5.647%
```

解释：

多数候选没有进场，是因为组合槽位已满。后续参数研究不能只看候选质量，还要看排序和槽位分配。

## 2026-05-20 单票消融

新增 `scripts/ablate_symbols.py`。

行为：

- 先跑一次基准回测
- 每次移除一个可交易 symbol 后重跑
- 输出 `ablation_*.csv`
- 排序哪些移除改善或损害 PnL

这是研究诊断，不自动修改股票池。

## 2026-05-20 预检

新增 `scripts/preflight.py`。

行为：

- 检查 Python 模块
- 检查 Schwab 环境变量
- 检查 `SCHWAB_TOKEN_PATH`
- Discord webhook 缺失只警告，不失败
- 自动创建本地输出目录

## 2026-05-20 zshrc 环境变量 fallback

共享环境加载器增加 `~/.zshrc` fallback。

支持：

- `SCHWAB_APP_KEY`
- `SCHWAB_APP_SECRET`
- `SCHWAB_TOKEN_PATH`
- probe tuning variables
- `DISCORD_WEBHOOK_URL`

脚本不会打印 secret 值。覆盖范围包括 probe、preflight、信号、触发、workflow、回测、sweep、消融、持仓监控和日志汇总。

## 2026-05-20 数据来源诊断

新增 history-source diagnostics。

行为：

- Schwab cache wrapper 标记 `source=schwab`
- Schwab 无 K 线或失败且启用 yfinance fallback 时，yfinance wrapper 记录 `fallback_from` 和 `fallback_reason`
- 报告包含 source counts、fallback symbols、stale fallback refreshes、primary error details
- 控制台摘要显示 source counts 和 fallback count

## 2026-05-20 市场窗口保护

新增 New York time market-window guard。

行为：

- `run_workflow.py --only-market-window` 在 ET 09:31 到 15:55 之外返回 `skipped`
- 周末跳过
- `render_launchd_plist.py --start-interval-seconds 300` 生成重复运行 plist
- 可嵌入 `--skip-signals` 做 trigger-only 盘中扫描

## 2026-05-20 集中默认值

新增 `src/liubang/defaults.py`。

集中管理：

- initial equity
- risk per trade
- max position value
- hard stop cap
- first target R
- weak-regime size multiplier
- pullback range
- minimum score

回测参数、仓位计算、信号生成、回测 CLI、消融、sweep、日志汇总都读取共享常量。

## 2026-05-20 重复持仓触发保护

盘中触发扫描器新增 duplicate-symbol guard。

行为：

- 检查 `portfolio_guard.open_symbols`
- 已有开放持仓时仍可记录技术触发
- action 改为 `do_not_open_duplicate_symbol_position`
- 被阻止的触发不输出 `manual_position_template`
- 报告区分 `triggered_count` 和 `actionable_triggered_count`

## 2026-05-20 压力测试套件

新增 `scripts/stress_backtest.py`。

行为：

- 加载同一股票池和历史
- 比较 baseline、无财报过滤、更高分数、更窄回撤、更紧/更松止损
- 输出 `stress_*.json` 和 `stress_*.csv`
- 报告 return range 和正负场景数量

当前扩展股票池验证结果：

```text
scenarios=7
positive_scenarios=7
negative_scenarios=0
min_return=0.49%
min_profit_factor=1.0179
baseline_return=9.887%
```

## 2026-05-20 时间稳定性分析

增强 `scripts/analyze_backtest.py`。

新增输出：

- 前半段 vs 后半段交易表现
- 正/负/持平月份数量
- 年度稳定性
- 最差季度

用途：

避免只看 headline return，而忽略收益集中在某段样本里的问题。

## 2026-05-20 日志风险熔断

信号生成新增 journal-based risk throttle。

行为：

- 读取 `data/trade_journal.csv`
- 连续 3 笔亏损后阻止新入场
- 最近 5 笔亏损达到 3R 后阻止新入场
- 当月亏损达到 4R 后阻止新入场
- 日志格式异常时 fail closed
- `--ignore-risk-throttle` 可关闭

## 2026-05-20 Watchlist CSV

新增 `scripts/export_watchlist_csv.py`。

行为：

- 默认读取最新 `signals_*.json`
- 输出 `watchlist_*.csv`
- 展开分数、计划入场日、entry/stop/target、建议股数、组合保护、风险熔断、新闻风险、期权风险、put/call OI、第一条 headline

## 2026-05-20 日线长历史代理

新增 `scripts/run_daily_proxy_backtest.py`。

行为：

- yfinance 1 日线，默认 `5y`
- 沿用强势股浅回撤候选逻辑
- 近似下一日开盘成交
- 模拟 stop、1R 部分止盈、breakeven stop、财报退出、5 日时间退出
- 报告明确说明不验证 5 分钟 VWAP/prior-high 触发

扩展股票池验证结果：

```text
candidates=15853
trades=955
win_rate=49.11%
return=125.531%
average_r=0.1213
profit_factor=1.6931
max_drawdown=5.612%
positive_month_share=0.7333
```

## 2026-05-20 核心股票池 metadata

核心股票池 metadata 流入信号报告和 watchlist CSV。

行为：

- 固定核心标的包含 `theme`
- 动态 yfinance 标的包含 screener 和 liquidity metadata
- CSV 包含 `theme` 和 `source_reason`

## 2026-05-20 主题和集中度分析

增强 `scripts/analyze_backtest.py`。

新增：

- top1 和 top3 贡献集中度
- 按 `theme` 输出最好/最差组
- 年度稳定性
- 最差季度

## 2026-05-20 Watchlist 主题集中度

信号报告新增 watchlist concentration。

行为：

- 按配置的 `theme` 分组
- 动态 yfinance 标的回退到 source label
- 记录 top theme、数量、占比和 source counts
- 当 3 个以上 watchlist 中某主题占比至少一半时警告
- CSV 同步输出这些字段

## 2026-05-20 触发模板导出

新增 `scripts/export_trigger_templates.py`。

行为：

- 默认读取最新 `triggers_*.json`
- 只导出 action 为 `prepare_manual_entry` 的 `manual_position_template`
- 单独记录被阻止的技术触发
- 输出 `manual_position_templates_*.json`

## 2026-05-20 Workflow CSV 和模板选项

新增：

- `run_workflow.py --export-watchlist-csv`
- `run_workflow.py --export-trigger-templates`

用途：

- 信号后立即输出 flat CSV
- 触发后输出可人工检查的入场模板
- launchd plist 也支持同样 flags

## 2026-05-20 策略验证门槛

新增 `scripts/validate_strategy.py`。

检查：

- 5 分钟回测
- 压力测试
- 小时代理
- 日线代理
- 单票消融
- 主题消融
- 交易数量
- 正收益
- average R
- profit factor
- 最大回撤
- 5 天最大持仓
- top contributor 集中度

默认只写报告。传入 `--fail-on-gate-failure` 时才用非零退出码当作 CI gate。

## 2026-05-20 主题消融

新增 `scripts/ablate_themes.py`。

行为：

- 按 `theme` 分组核心标的
- 每次移除一个主题组后重跑回测
- 输出 `theme_ablation_*.csv`
- 辅助区分单票依赖和主题依赖

扩展股票池最新验证：

```text
theme_worst=memory_semiconductor
return_without_worst_theme=3.965%
```

## 2026-05-20 研究套件 runner

新增 `scripts/run_research_suite.py`。

顺序运行：

- 5 分钟基准回测
- 压力测试
- yfinance 小时代理
- yfinance 日线代理
- 单票消融
- 主题消融
- 最终验证门槛

当前可复现命令：

```bash
.venv/bin/python scripts/run_research_suite.py \
  --universe config/research_universe_dynamic.json \
  --hard-stop-pct 0.03 \
  --symbol-cooldown-days 3
```

## 2026-05-20 yfinance 小时代理

新增 `scripts/run_hourly_proxy_backtest.py`。

行为：

- yfinance 盘中历史，默认 interval `1h`、period `2y`
- 复用候选、盘中 VWAP/prior-high 触发、stop/target/time-exit 和组合槽位逻辑
- 输出 `hourly_proxy_*.json`
- validation 自动读取最新小时代理报告

扩展股票池验证结果：

```text
trades=312
return=27.728%
profit_factor=1.3802
top1=SNDK
top1_net_share=0.1464
top3_net_share=0.4293
```

## 2026-05-20 触发观察模式

新增 `scan_triggers.py --observation-only` 和 `run_workflow.py --observation-only`。

行为：

- 仍评估并记录技术触发
- 触发标的 action 为 `observe_only_no_manual_entry`
- 不输出 `manual_position_template`
- actionable triggered count 为 0
- launchd plist 支持同样 flag

## 2026-05-20 纸面观察持仓

新增 `scripts/record_paper_triggers.py` 和 workflow flags：

- `--record-paper-triggers`
- `--monitor-paper-positions`

行为：

- 读取最新 trigger report
- 把 `observe_only_no_manual_entry` 触发记录到 `data/paper_positions.json`
- 使用与 `data/manual_positions.json` 相同结构
- 跳过已有开放纸面持仓的标的
- 输出 `paper_positions_update_*.json`
- 用现有持仓监控器跟踪纸面持仓，不碰真实手动持仓文件

## 2026-05-20 纸面动作应用

新增 `scripts/apply_paper_actions.py` 和 workflow flag `--apply-paper-actions`。

行为：

- 默认读取最新 `positions_*.json`
- 应用纸面部分止盈、stop、breakeven、time exit
- 追加 lot 到 `data/paper_trade_journal.csv`
- 部分退出后更新 `data/paper_positions.json`
- 完全关闭后从纸面持仓文件移除
- 输出 `paper_actions_*.json`

## 2026-05-20 纸面日志汇总

`scripts/summarize_journal.py` 新增 `--report-prefix`，workflow 新增 `--summarize-paper-journal`。

行为：

- 真实日志默认输出 `journal_*.json`
- 纸面日志使用 `--report-prefix paper_journal`
- workflow 在 `data/paper_trade_journal.csv` 存在时输出 `paper_journal_*.json`

## 2026-05-20 单票冷却研究

新增冷却期研究和策略参数。

行为：

- `BacktestParams.symbol_cooldown_days`
- 5 分钟回测、小时代理、日线代理支持同一参数
- `scripts/sweep_cooldown.py` 可扫 0/1/2/3/4/5/10/20 天
- 信号端支持 `--symbol-cooldown-days`
- 可通过 `--cooldown-journal-file` 指定纸面日志做冷却依据

关键结论：

- 仅靠核心池冷却不能解决依赖问题。
- 扩展股票池 + 3 天单票冷却显著改善集中度，并通过 validation。
