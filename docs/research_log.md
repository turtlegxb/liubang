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

## 2026-05-21 当周 GEX 上下文

新增 `src/liubang/gex.py` 和 `scripts/calc_weekly_gex.py`。

行为：

- 使用 Schwab option chain 中的 `gamma`、`openInterest` 和 `multiplier`
- 默认只统计当前美东交易日至本周五到期的合约
- 默认只保留距离现货 ±15% 的 strike
- 计算约定：call GEX 为正，put GEX 为负
- 单位：标的上涨 1% 时的美元 gamma exposure 代理值
- `generate_signals.py` 默认把 `weekly_gex` 写入最终 watchlist 的 `options_context`
- dashboard 的 “Watchlist 今日行情” 显示 GEX 状态、净 GEX、Call Wall、Put Wall
- watchlist CSV 导出同样包含 `weekly_gex_regime`、`weekly_net_gex`、`weekly_call_wall`、`weekly_put_wall`

命令：

```bash
scripts/liubang_live.sh gex AMD --refresh
```

注意：该值是基于公开链上 OI 的代理值，不代表真实 dealer 持仓；当前只作为风险/上下文，不直接改变买入分数或自动下单。

## 2026-05-21 Paper 开仓槽位修正

问题：观察模式下 `signals` 默认用 `data/manual_positions.json` 计算 portfolio guard；本地 paper 模式实际持仓在 `data/paper_positions.json`，导致 dashboard 显示还有槽位，且 `record_paper_triggers.py` 没有二次限制新增 paper 持仓数量。

修正：

- `scripts/liubang_live.sh` 的默认观察配置增加 `--positions-file data/paper_positions.json`
- `scripts/record_paper_triggers.py` 根据最新 trigger report 的 `max_positions` 和当前 paper 开放持仓数量重新计算剩余槽位
- paper 持仓已满时，新触发写入 `skipped_portfolio_full`，不再追加到 `data/paper_positions.json`

当前状态示例：neutral regime 最大 2 个持仓，paper 已有 AAPL、ARM、CRWD 共 3 个开放持仓，因此 dashboard 显示 `仓位槽位 0 / 2`，`允许新开 no`。

## 2026-05-21 复盘和 Paper 执行口径修正

根据 2026-05-20 盘后复盘，保留 `news_risk=high` 为提示信息，不作为硬拦截。

修正：

- 复盘 `new_paper_positions` 改为用当日 paper journal 已平交易 + 当前开放 paper 持仓去重统计
- 同时保留 `raw_new_paper_position_events`，用于识别历史 update 事件污染
- 开放 paper 持仓补充 `entry_time_et` 展示；缺失时复盘和 dashboard 标记
- paper trigger 记录器按 `trigger_rank_score`、信号分数、触发确认强度排序，只记录剩余槽位内的最高优先级候选
- `target_1` 后剩余仓不再用盘中 low 触发 breakeven/stop，改为 5m 收盘跌破 post-target stop 才退出

当前 2026-05-20 复盘口径：原始新增事件 13，按 journal+open 去重后 9。

## 2026-05-21 复盘增强和长期样本

继续保留滑点为暂不建模项，其他复盘改进已落地。

新增行为：

- 仓位监控报告增加 `initial_risk_per_share`、`unrealized_pnl`、`floating_r`、`distance_to_stop_pct`、`distance_to_target_pct`
- 复盘 “最终触发状态” 同时展示首次触发时间/价格和最终状态，避免把“盘中曾触发”和“收盘后最终状态”混在一起
- 复盘 Watchlist 展示周 GEX 状态、净 GEX、Call Wall、Put Wall
- `workflow` 因 `outside ET market window` 被跳过时降级为 info，不再计入数据问题数量
- `portfolio_guard` 超过最大开放持仓或最大总敞口时，复盘标记 `paper_exposure_breach`
- `scripts/generate_daily_review.py` 默认维护 `data/reviews/review_samples.csv`，按 `review_date_et + symbol` 覆盖更新，用于长期统计候选、触发、GEX 和 paper 结果

样本字段包括：

- 日期、标的、计划入场日、分数、回撤
- 是否盘中触发、首次触发时间/价格、最终状态
- news/options 风险、周 GEX、Call Wall、Put Wall
- paper 状态、paper PnL、paper R multiple

## 2026-05-21 观察可信度增强

本轮暂不做“下一根 5m K 入场”和滑点建模，其他观察可信度改进已落地。

新增口径：

- `scripts/generate_daily_review.py` 默认忽略早于 `2026-05-20T13:50:00+00:00` 的 `paper_positions_update` 报告，用于隔离初期开盘 bug 污染；被忽略报告以 `paper_update_boundary_filter` 记录为 info，不计入数据问题
- `record_paper_triggers.py` 为每个触发候选记录 `paper_candidate_rank`、`paper_slot_selected`、`paper_fill_status`、`paper_skip_reason`
- 未进入 paper 的触发候选统一写入 `missed_triggers`，状态为 `triggered_but_not_filled`
- `review_samples.csv` 新增槽位归因字段：`paper_candidate_rank`、`paper_slot_selected`、`paper_skip_reason`、`paper_fill_status`
- `review_samples.csv` 新增触发时段字段：`trigger_time_bucket`，分为 `open_30m`、`morning`、`midday`、`afternoon`、`late_day`
- `review_samples.csv` 新增 GEX 标签字段：`gex_risk_tags`
- `review_samples.csv` 新增 `exit_reason`，从 paper journal 的 notes 中识别 `target_1`、`stop`、`time_stop`、`manual_exit`
- trigger 扫描新增 5m 数据异常保护：最新两根 5m 成交量为 0、最新两根间隔异常、OHLC 不合理、5m 收盘跳变过大时，标记为 `data_suspect`，不触发 paper

注意：这些改动只增强观察记录和样本可信度，不会改变入场确认条件，也不会模拟滑点。

## 2026-05-21 1R 后 Trailing Stop

新增 1R 卖出一半后的剩余仓 trailing stop。

规则：

- 只在 `target_hit=true` 后启用
- trailing stop 使用“最高 5m 收盘价 - 1R”
- 最终 stop 取 `max(current_stop_price, entry_price, trailing_stop)`
- 剩余仓退出仍然要求最新 5m 收盘价跌破 stop；盘中影线不触发
- 如果 trailing stop 能上移，仓位监控输出 `raise_trailing_stop`
- paper action 对 `raise_trailing_stop` 只更新 `data/paper_positions.json` 的 `current_stop_price`，不写平仓 journal

该规则保留原来的“1R 卖半 + 保本”底线，同时让强势延续的半仓可以逐步锁定利润。

## 2026-05-21 复盘样本和 Dashboard 继续收紧

新增三项观察优化：

- `missed_triggers` 去重：按 `symbol + entry_date + paper_skip_reason` 保留首次事件，避免 5 分钟 loop 重复记录同一个错过原因
- dashboard 的 “Paper 持仓” 表新增 `止损模式` 和 `Trail Ref`
  - `initial`：尚未触及 1R
  - `breakeven`：1R 后保本
  - `locked`：止损已经高于入场价
  - `trailing`：当前有效止损来自 trailing stop
- `review_samples.csv` 新增 `mfe_r_5m_close` 和 `mae_r_5m_close`

MFE/MAE 当前口径：

- 基于当日每轮 trigger report 的 `last_close`
- 只统计首次触发之后的 5m 收盘路径
- 单位为 R
- 这是 5m 收盘代理值，不是逐笔最高/最低，也不是 5m high/low

## 2026-05-21 选股评分模式和对比回测

新增 `scoring_mode`，默认仍为 `classic`，确保当前观察 workflow 不被静默改变。

新增模式：

- `classic`：原始强度分 + 回撤分，候选准入和排序都沿用旧逻辑
- `ranked_v1`：候选准入仍沿用 `classic`，主分数也以 `classic` 为底，再用同一天候选的横截面因子做小幅排序 overlay

`ranked_v1` 因子：

- 20 日相对 QQQ 强度
- 60 日相对 QQQ 强度
- 距离 SMA20 的强度位置
- 距离 20 日高点的位置
- ATR 标准化回撤质量
- 5 日成交量比率，缩量更优
- 当日收盘位置

新增回测入口：

```bash
sh scripts/liubang_live.sh compare-scoring
```

等价直接命令：

```bash
.venv/bin/python scripts/compare_scoring_modes.py \
  --universe config/research_universe_dynamic.json \
  --hard-stop-pct 0.03 \
  --symbol-cooldown-days 3
```

报告输出：`data/exports/scoring_compare_*.json`。

注意：

- `ranked_v1` 当前是排序 overlay，不改变原始 `min_score`、回撤范围、财报过滤等硬约束
- GEX、新闻、期权链仍是上下文提示，不进入历史评分；没有历史 GEX 数据前，不把它纳入回测分数
- 后续 `ranked_v2` 已通过跨周期检查，见下一节；`ranked_v1` 保留为对照模式

## 2026-05-21 ranked_v2 打分回测优化

继续用当前扩展研究股票池和已验证观察参数做打分优化：

```bash
universe=config/research_universe_dynamic.json
hard_stop_pct=0.03
symbol_cooldown_days=3
```

先在 5 分钟样本中 sweep 多种 overlay 方向，再用小时代理和日线代理做防过拟合检查。

候选结果：

```text
classic:
  5m     trades=121 return=10.026% pf=1.4325 dd=6.292% avgR=0.2031
  hourly trades=312 return=27.728% pf=1.3802 dd=6.388% avgR=0.1981
  daily  trades=955 return=125.531% pf=1.6931 dd=5.612% avgR=0.1213

ranked_v2 / rs_only overlay:
  5m     trades=135 return=15.489% pf=1.6099 dd=5.023% avgR=0.2829
  hourly trades=326 return=37.415% pf=1.4538 dd=5.458% avgR=0.2400
  daily  trades=983 return=143.580% pf=1.6403 dd=5.920% avgR=0.0980
```

正式报告：

```text
scoring_compare=data/exports/scoring_compare_20260520_230856_110336.json
stress=data/exports/stress_20260520_230916_699882.json
hourly_proxy=data/exports/hourly_proxy_20260520_230908_854433.json
daily_proxy=data/exports/daily_proxy_20260520_230909_028801.json
```

决定：

- 新增 `ranked_v2`
- 默认切换为 `ranked_v2`
- 旧口径可用 `--scoring-mode classic` 回退

`ranked_v2` 公式：

- 候选准入仍沿用 `classic`
- 主分数仍以 `classic_score` 为底
- overlay 只使用：
  - 20 日相对 QQQ 强度，权重 3
  - 60 日相对 QQQ 强度，权重 2
  - 相对 SMA20 距离，权重 1
- 最终分数：

```text
score = classic_score + (overlay_score - 5.0) * 0.20
```

解释：

`ranked_v1` 加入了过多回撤质量、近高点、缩量和收盘位置因素，在 5 分钟样本之外不够稳。`ranked_v2` 更接近“强者恒强”的横截面重排，只在候选槽位竞争时微调排名，因此更适合作为下一阶段观察模式。

## 2026-05-21 默认评分切换为 ranked_v2

根据前一节回测结果，系统级默认评分从 `classic` 切换为 `ranked_v2`。

影响范围：

- `generate_signals.py` 默认生成 `ranked_v2` watchlist
- `run_backtest.py`、`stress_backtest.py`、小时代理、日线代理默认使用 `ranked_v2`
- `run_research_suite.py` 默认使用 `ranked_v2`
- `scripts/liubang_live.sh signals/workflow/day/loop/research` 都会继承该默认值

回退旧口径：

```bash
--scoring-mode classic
```

## 2026-05-21 默认 v2 验证和搜索入口收紧

已确认系统默认值：

```text
DEFAULT_SCORING_MODE=ranked_v2
BacktestParams().scoring_mode=ranked_v2
```

正式回测确认 CLI 也继承默认 v2：

```text
Scoring mode: ranked_v2
Trades: 135
Return: 15.489%
Profit factor: 1.6099
Max drawdown: 5.023%
```

验证命令：

```bash
.venv/bin/python scripts/smoke_tests.py
.venv/bin/python scripts/run_backtest.py \
  --universe config/research_universe_dynamic.json \
  --hard-stop-pct 0.03 \
  --symbol-cooldown-days 3
```

新增 `scripts/search_scoring_strategies.py` 的可控搜索能力：

- `--history-mode 5m`：默认使用 Schwab 5 分钟历史
- `--history-mode hourly`：使用 yfinance 1 小时代理历史
- `--progress-every`：长时间搜索时输出进度，避免无反馈长跑

当前发现：在 5 分钟样本中可以通过更严格过滤找到 PF 大于 2 的候选配置，但同一配置在 1 小时代理样本中明显下降。因此目前只把 `ranked_v2` 作为默认观察评分，不把 5 分钟 PF 大于 2 的搜索结果直接升级为默认策略。

## 2026-05-21 PF 大于 2 的交叉验证候选

新增联合验证脚本：

```bash
sh scripts/liubang_live.sh cross-scoring
```

等价直接命令：

```bash
.venv/bin/python scripts/cross_validate_scoring_strategies.py \
  --universe config/research_universe_dynamic.json \
  --max-configs 1152 \
  --progress-every 200
```

它会同时加载：

- Schwab 5 分钟样本
- yfinance 1 小时代理样本

然后对同一组过滤条件分别回测，并按 `min(5m_pf, hourly_pf)` 排序。

本轮结果：

```text
Rows: 1152
Target: min(5m_pf, hourly_pf)>=2.0
Target hits: 72
Best min_pf: 2.1183
Report: data/exports/scoring_cross_validation_20260520_234525_273983.json
```

最佳交集候选：

```text
min_classic_score >= 7.0
min_ranked_score >= 7.0
max_pullback_pct <= 0.05
hard_stop_pct = 0.045
min_rs20_rank >= 0.65
min_overlay_score >= 7.4
max_atr20_pct <= 0.08
regime_filter = strong
symbol_cooldown_days = 0
```

交叉验证表现：

```text
5m:     trades=79  return=24.739%  pf=2.1766  dd=5.331%
hourly: trades=194 return=65.006%  pf=2.1183  dd=4.333%
```

解释：

- 这不是改 `ranked_v2` 公式，而是在 v2 之后增加更严格的准入过滤。
- 核心含义是：只在大盘强势日交易；候选必须在当天横截面里相对 QQQ 的 20 日强度排名靠前；overlay 分数必须足够高；同时排除 ATR20 超过 8% 的高波动标的。
- 5 分钟和 1 小时代理同时过 PF 2，说明它不像上一轮纯 5 分钟 PF 2.86 那样明显依赖短样本。

日线代理 caveat：

同一过滤条件在 5 年日线代理中：

```text
trades=615 return=65.566% pf=1.3554 dd=11.487%
```

日线代理使用“次日开盘进场、同日 stop/target 同时触及时假设 stop 先触发”的保守粗粒度规则，和真实 5 分钟 VWAP/前高确认触发差别很大。因此它作为压力提示保留，不作为当前 5 天以内策略的硬否决。

## 2026-05-21 默认启用 regime-aware v2

根据“不要分档位，按 regime 自动选择”的决策，默认 `ranked_v2` 增加 regime-aware 准入过滤：

```text
strong:
  min_ranked_score >= 7.0
  max_pullback_pct <= 0.05
  ranked_v2_rs_20d_rank >= 0.65
  ranked_v2_overlay_score >= 7.4
  atr20_pct <= 0.08

neutral:
  普通 ranked_v2

weak:
  阻止新开仓候选
```

说明：

- 这不是新增手动 profile，默认策略会自动按 regime 切换。
- 暂不按 regime 改 `hard_stop_pct`；止损仍由全局参数控制，避免 paper、trigger、回测口径不一致。
- 交叉验证中 PF 大于 2 的纯 strong 配置仍保留为研究参考；默认自动策略会在 neutral 日继续保留普通 v2 候选，所以表现不会等同于纯 strong-only 交叉验证结果。

验证结果：

```text
默认 BacktestParams 口径:
  5m     trades=94  return=24.640%  pf=1.9750  dd=5.938%
  hourly trades=259 return=28.367%  pf=1.3581  dd=6.523%

当前 live/research shell 口径 hard_stop=0.03 cooldown=3:
  5m     trades=100 return=16.648%  pf=1.7507  dd=5.403%
  hourly trades=262 return=30.187%  pf=1.4684  dd=7.152%
  daily  trades=784 return=54.893%  pf=1.2973  dd=10.800%
```

新增报告中会显示：

```text
Regime policy: regime_aware_v2
Regime policy skipped: <count>
```

## 2026-05-21 单票冷却重新评估

用户观察到 2026-05-21 信号为空。复查最新信号报告：

```text
watchlist_count_before_regime_policy=4
watchlist_skipped_regime_policy=0
watchlist_count=0
symbol_cooldown.blocked_count=4
```

被挡标的：

```text
PANW, CSCO, ARM, ASTS
```

这些标的都因 2026-05-20 paper 交易记录被 `symbol_cooldown_days=3` 冷却到 2026-05-25。说明空信号不是 regime-aware v2 导致，而是旧冷却参数造成。

重新 sweep：

```bash
.venv/bin/python scripts/sweep_cooldown.py \
  --universe config/research_universe_dynamic.json \
  --cooldowns 0,1,2,3,5 \
  --modes backtest,hourly_proxy,daily_proxy \
  --hard-stop-pct 0.03
```

live 止损口径 `hard_stop=0.03` 结果：

```text
cooldown=0:
  5m     trades=110 return=13.868% pf=1.5343 dd=3.717%
  hourly trades=294 return=21.089% pf=1.2772 dd=10.838%
  daily  trades=856 return=69.627% pf=1.3436 dd=9.075%

cooldown=1:
  5m     trades=105 return=12.819% pf=1.5340 dd=2.990%
  hourly trades=285 return=16.430% pf=1.2294 dd=10.907%
  daily  trades=831 return=56.942% pf=1.2892 dd=7.180%

cooldown=3:
  5m     trades=98 return=11.442% pf=1.4856 dd=3.467%
  hourly trades=272 return=21.320% pf=1.3147 dd=8.907%
  daily  trades=809 return=40.647% pf=1.2060 dd=9.137%
```

决定：

- 当前 live/research 快捷命令默认改为 `--symbol-cooldown-days 0`
- 保留组合槽位、总敞口、risk throttle 和 trigger 排序作为主要风控
- `sweep_cooldown.py` 增加 `--hard-stop-pct`，以后可直接复现 live 止损口径

完整报告：

```text
data/exports/cooldown_sweep_live_stop_20260521_100228_403281.json
data/exports/cooldown_sweep_live_stop_20260521_100228_403281.csv
```

## 2026-05-21 signals 筛选结果落盘

新增精简信号筛选产物，避免只靠完整 `signals_*.json` 或 dashboard 反查最终候选。

每次运行 `generate_signals.py` 会默认写入：

```text
data/exports/signal_selection_*.json
data/exports/latest_signal_selection.json
```

内容包括：

- 来源 `signals_*.json`
- market regime、scoring mode、data quality、history sources
- regime policy、symbol cooldown、risk throttle、portfolio guard 摘要
- 最终 watchlist 的 rank、ticker、分数、入场/止损/目标参考、建议股数、新闻风险、期权风险和 weekly GEX 摘要

如果只想生成完整 signals 报告，可显式加：

```bash
.venv/bin/python scripts/generate_signals.py --skip-selection-export
```

## 2026-05-22 隔夜滞留持仓是否应按每日重选退出

问题：前一天未关闭的持仓，如果第二天不再进入 watchlist，是否应该退出？

新增实验口径：

```text
reselection_exit_mode=next_open_not_reselected
```

规则：

- 每个交易日用当前策略重新生成最终候选集
- 已持有且不是当日新开的仓位，如果 symbol 不在当日候选集中，则按当日第一根 5 分钟 K 的开盘价退出
- 该退出在当日新开仓前执行，因此会释放组合槽位

对比命令：

```bash
sh scripts/liubang_live.sh compare-reselection
```

当前 `config/research_universe_dynamic.json` + live 口径结果：

```text
none:
  trades=118 return=17.910% pf=1.6275 dd=6.473% avg_hold=2.29

next_open_not_reselected:
  trades=169 return=12.646% pf=1.3642 dd=6.445% avg_hold=1.74
  reselection_exits=75 reselection_exit_pnl=23031.07

next_open_not_reselected_when_slot_needed:
  trades=163 return=15.898% pf=1.4492 dd=4.428% avg_hold=1.90
  reselection_exits=50 reselection_exit_pnl=18403.99

replace_weak_hold_when_slot_needed:
  trades=120 return=20.355% pf=1.7889 dd=4.438% avg_hold=2.29
  reselection_exits=8 reselection_exit_pnl=-1732.05 avg_exit_hold_score=5.844
```

退出原因结构：

```text
none:
  stop=101 trades, time_exit=16 trades, earnings_exit=1 trade

next_open_not_reselected:
  reselection_exit=75 trades, stop=94 trades

next_open_not_reselected_when_slot_needed:
  reselection_exit=50 trades, stop=107 trades, time_exit=5 trades, earnings_exit=1 trade

replace_weak_hold_when_slot_needed:
  reselection_exit=8 trades, stop=95 trades, time_exit=16 trades, earnings_exit=1 trade
```

解读：

- “次日不再入选就退出”确实缩短持仓时间，也释放更多槽位，因此交易数从 118 增加到 169。
- “只有槽位不够时才替换旧仓”比无条件退出更合理，回撤也从 6.473% 降到 4.428%，但收益和 PF 仍低于默认继续持有。
- 两种重选退出都降低总收益、PF、平均 R，说明它们虽然能锁住部分滞留仓利润，但过早切掉了原本靠 time exit 扩大利润的赢家。
- 加入独立 `hold_score` 后，`replace_weak_hold_when_slot_needed` 触发 8 次，收益、PF、回撤都优于默认继续持有。
- 小网格显示 `replacement_min_hold_score=7.0` 比 5.0/6.0 更好；`replacement_min_candidate_score_margin` 在 0/0.5/1.0 三档对当前样本没有影响，说明被替换旧仓和新候选分差已经足够大。
- 这一步先没有切换实盘默认；后续 replace 参数优化完成后，已按用户确认把 live 观察默认切到更优组合，见下一节。

完整报告：

```text
data/exports/reselection_exit_compare_20260522_031537_122994.json
data/exports/reselection_exit_compare_20260522_031537_122994.csv
```

限制：历史回测没有真实复原 yfinance 每日 dynamic screener 成分变化；这里是在指定 universe 文件中每天重新评分和筛选，避免使用未来动态榜单造成未来函数。

## 2026-05-22 replace 逻辑参数优化

问题：在 `replace_weak_hold_when_slot_needed` 中，新候选是否应该直接使用 `hold_score`，以及替换阈值、比较方式、总槽位应该怎么取？

结论：

- 新候选不直接使用 `hold_score`。`hold_score` 是旧仓继续持有价值，包含浮盈 R、当前止损、是否打过 1R、持仓后的相对强弱等路径信息；新候选还没有这些状态。
- 新候选使用独立 `replacement_candidate_score`，可选 `entry_score`、`entry_plus_rs`、`entry_plus_overlay`、`entry_plus_quality`。
- 替换动作比较的是“新候选预期强度”与“旧仓继续持有价值/旧入场分/两者均值”，而不是把两类资产强行塞进同一个 hold 分数。

运行命令：

```bash
sh scripts/liubang_live.sh optimize-replacement \
  --hold-score-thresholds 6,7,8 \
  --candidate-score-margins 0,0.5 \
  --replacement-score-modes entry_score,entry_plus_rs,entry_plus_overlay,entry_plus_quality \
  --replacement-compare-modes candidate_vs_hold,candidate_vs_entry,candidate_vs_blend \
  --weak-slots 1 \
  --neutral-slots 2,3 \
  --strong-slots 3,4 \
  --top 12
```

网格规模：

```text
replacement_configs=288
baselines=4
total_runs=292
```

各槽位 baseline：

```text
w1_n2_s3: return=17.910% pf=1.6275 dd=6.473% trades=118
w1_n2_s4: return=10.739% pf=1.2893 dd=7.432% trades=146
w1_n3_s3: return=15.460% pf=1.4935 dd=6.473% trades=127
w1_n3_s4: return=9.434% pf=1.2395 dd=7.167% trades=155
```

各槽位最优：

```text
w1_n2_s3:
  hold<=7 margin=0 entry_plus_overlay candidate_vs_entry
  return=20.933% delta=+3.023% pf=1.8322 dd=4.353% trades=118 reselect=5

w1_n2_s4:
  hold<=7 margin=0 entry_plus_overlay candidate_vs_blend
  return=18.859% delta=+8.120% pf=1.5683 dd=5.224% trades=151 reselect=13

w1_n3_s3:
  hold<=7 margin=0 entry_plus_overlay candidate_vs_blend
  return=21.306% delta=+5.846% pf=1.7688 dd=4.438% trades=129 reselect=9

w1_n3_s4:
  hold<=7 margin=0 entry_plus_overlay candidate_vs_blend
  return=19.981% delta=+10.547% pf=1.5756 dd=5.596% trades=158 reselect=13
```

当前最高收益组合：

```text
weak_max_positions=1
neutral_max_positions=3
strong_max_positions=3
replacement_min_hold_score=7.0
replacement_min_candidate_score_margin=0.5
replacement_score_mode=entry_plus_overlay
replacement_compare_mode=candidate_vs_blend

return=21.306%
delta_vs_same_slot_baseline=+5.846%
profit_factor=1.7688
max_drawdown=4.438%
trades=129
reselection_exits=9
reselection_exit_pnl=-1830.57
```

解读：

- `strong_max_positions=4` 在当前样本里明显变差，说明强市多加一个槽位会引入边际质量更低的交易。
- `neutral_max_positions=3` 配合弱仓替换，收益最高；但如果不启用替换，`w1_n3_s3` baseline 低于当前默认 `w1_n2_s3`。
- 候选评分的多种模式出现大量并列，说明触发替换的样本分差较大；真正决定结果的是旧仓是否足够弱、以及槽位是否被更高质量候选占用。
- `candidate_vs_blend` 和 `candidate_vs_hold` 在最高收益组合中并列；文档推荐 `candidate_vs_blend`，因为它不会只因旧仓短期回撤而完全忽略旧仓原始入场质量。
- 已按实盘观察默认切到该组合：`scripts/liubang_live.sh signals/workflow/day/loop` 会默认带入上述参数。注意这仍是观察和纸面记录默认，不会自动向券商下单；旧仓替换退出仍由人工确认。

完整报告：

```text
data/exports/replacement_logic_optimization_20260522_033358_498141.json
data/exports/replacement_logic_optimization_20260522_033358_498141.csv
```
