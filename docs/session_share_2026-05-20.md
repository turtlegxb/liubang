# Liubang 项目会话记录

日期：2026-05-20  
用途：给他人了解本项目的设计过程、当前能力、使用方式和已知边界。  
注意：本文是可分享版记录，不包含 Discord webhook、Schwab token、app key、app secret 或本地私密路径细节。

## 1. 初始目标

用户希望搭建一个美股选股策略，持仓周期控制在 5 天以内。最初采用问答方式确定策略模式，随后逐步落到本地项目实现。

最终确定的项目定位：

- 面向美股短线选股。
- 持仓周期不超过 5 个交易日。
- 系统负责生成候选、触发、风控、paper 观察和本地监控。
- 用户手动下单，不做自动下单。
- 不需要 Schwab 账户交易权限。
- 优先使用 Schwab 市场数据，缺失数据可用 yfinance 补缺。
- Discord 只做通知推送。
- 所有文档使用中文。

## 2. 运行模式

当前系统是“市场数据 + 手动下单 + paper 观察”的半自动系统。

系统会做：

- 每日生成 watchlist。
- 给每只候选标的计算分数、入场参考、止损参考、第一目标和建议股数。
- 盘中用 5 分钟 K 线扫描触发。
- 记录 paper 触发和 paper 持仓。
- 自动监控 paper 持仓的止损、第一目标、时间退出。
- 生成 Discord 推送。
- 生成本地 HTML dashboard。
- 生成盘后复盘 JSON 和 HTML。

系统不会做：

- 不自动下单。
- 不调用账户交易接口。
- 不把 paper 持仓等同于真实账户持仓。
- 不把新闻或期权活动作为独立买入触发。

## 3. 数据和环境

环境变量读取顺序：

1. 当前 shell 环境。
2. `.env`。
3. `~/.zshrc`。

Schwab token 文件已经存在，本项目会从环境变量读取路径。Discord webhook 已写入 `.env`，但不会出现在文档或 git 中。

主要数据源：

- Schwab：5 分钟历史、报价、期权链上下文。
- yfinance：财报日、新闻、动态股票池、历史补缺。
- 本地文件：paper 持仓、paper 交易日志、研究报告、dashboard、复盘报告。

本地输出被 git 忽略：

- `data/cache/`
- `data/exports/`
- `data/dashboard/`
- `data/reviews/`
- `data/manual_positions.json`
- `data/paper_positions.json`
- `data/trade_journal.csv`
- `data/paper_trade_journal.csv`
- `.env`

## 4. 当前策略规格

策略类型：强势股浅回撤。

交易方向：

- 当前只做多。
- 做空模型暂不实现。

核心规则：

- 从固定核心池和扩展研究股票池生成候选。
- 股票池文件：`config/research_universe_dynamic.json`。
- 每日信号运行时使用 `--dynamic-source none`，避免在研究股票池之外临时引入新标的。
- 最低分数：`min_score=7.0`。
- 回撤范围：1% 到 6%。
- 当前观察配置硬止损：3%。
- 单票冷却：已关闭交易后 3 个交易日。
- 最大持仓：5 个交易日。
- 市场 regime：`strong`、`neutral`、`weak`。
- SPY、QQQ 用于市场 regime。
- XLK、SMH 用于行业上下文。
- 财报过滤来自 yfinance。
- 新闻上下文来自 yfinance，只做风险提示。
- 期权上下文来自 Schwab，只做风险提示。

仓位和组合约束：

- 默认账户权益假设：100,000 美元。
- 单笔风险：1%。
- 单票资金上限：20%。
- strong：最多 3 个开放持仓。
- neutral：最多 2 个开放持仓。
- weak：最多 1 个开放持仓并降仓。

## 5. 盘中触发逻辑

每日 watchlist 生成后，盘中使用 5 分钟 K 线确认入场。

触发条件：

- 只评估 regular-hours K 线。
- 跳过第一根 5 分钟 K。
- 最新 5 分钟收盘价高于 running VWAP。
- 最新 5 分钟收盘价高于上一根 5 分钟 K 的高点。
- 最新价格不能跌破技术止损参考。

触发后：

- observation-only 模式下不会输出真实下单模板。
- paper 模式会记录为本地 paper 持仓。
- Discord 会推送触发信息。
- dashboard 会展示最新触发状态和 watchlist 今日行情。

## 6. 研究验证结果

2026-05-20 当前完整验证状态：

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

解释：

- `status=pass`：当前研究门槛通过。
- `readiness=observation_ready`：适合观察和 paper 跟踪，不代表可以自动实盘。
- `5m_return=9.887%`：在当前 5 分钟样本回测中收益为正。
- `5m_profit_factor=1.4265`：毛盈利约为毛亏损的 1.43 倍。
- `5m_max_drawdown=6.292%`：当前样本最大回撤约 6.29%。
- `stress_positive=7/7`：7 个压力场景都保持正收益。
- `hourly_proxy_return` 和 `daily_proxy_return`：更粗粒度代理回测仍为正，用来观察方向一致性。

主要限制：

- 样本仍然有限。
- 历史回测不等于未来收益。
- 当前结论只支持继续观察和积累 paper/手工交易日志。

## 7. 常用 shell 命令

环境预检：

```bash
sh scripts/liubang_live.sh preflight
```

生成每日信号并推送 Discord：

```bash
sh scripts/liubang_live.sh signals
```

盘中扫描触发：

```bash
sh scripts/liubang_live.sh triggers
```

跑完整 paper observation workflow：

```bash
sh scripts/liubang_live.sh workflow
```

先生成信号，再进入前台循环：

```bash
sh scripts/liubang_live.sh day
```

使用最新信号进入前台循环：

```bash
sh scripts/liubang_live.sh loop
```

只跑一轮自检：

```bash
sh scripts/liubang_live.sh loop --max-ticks 1
```

生成 dashboard：

```bash
sh scripts/liubang_live.sh dashboard
```

生成并打开 dashboard：

```bash
sh scripts/liubang_live.sh dashboard-open
```

生成盘后复盘：

```bash
sh scripts/liubang_live.sh review
```

生成并打开盘后复盘：

```bash
sh scripts/liubang_live.sh review-open
```

运行离线检查：

```bash
.venv/bin/python scripts/smoke_tests.py
```

## 8. 前台循环替代 plist

用户明确不喜欢 plist 方式，因此当前推荐直接使用 shell 前台循环。

`loop` 默认行为：

- 使用最新 signals。
- 每 5 分钟运行一轮。
- 只在美股常规交易窗口内执行核心扫描。
- 记录 paper triggers。
- 监控 paper positions。
- 应用 paper actions。
- 汇总 paper journal。
- 推送 Discord。
- 每轮结束自动重写 dashboard。

当前不需要 macOS launchd plist。plist 生成器仍保留，但只是可选工具。

## 9. Dashboard

dashboard 文件：

```text
data/dashboard/liubang_dashboard.html
```

HTML 默认每 30 秒自动刷新。注意：它是静态 HTML，浏览器刷新只会重新加载文件；如果后台没有运行 `loop` 或重新执行 `dashboard`，文件内容不会自动变化。

当前 dashboard 包含：

- 顶部运行状态、signals 状态、触发数量、paper 持仓数量、持仓提醒数量。
- 当前建议操作命令。
- Watchlist 今日行情。
- 盘中触发状态。
- Watchlist 明细。
- 市场和组合状态。
- Paper 持仓状态。
- Paper 日志。
- Workflow 详细面板，已移动到底部。

Watchlist 今日行情显示：

- 标的。
- 触发状态。
- 信号收盘价。
- 最新 5 分钟价。
- 相对信号收盘涨跌。
- VWAP。
- 触发参考。
- 距触发。
- 距止损。
- 最后一根 K 的 ET 时间。

Paper 持仓显示：

- 标的。
- 当前状态。
- 入场日。
- 剩余股数。
- 入场价。
- 止损价。
- 目标价。
- 最新价。
- 浮动 R。
- 距止损。
- 距目标。
- 建议动作。

## 10. Paper Trading 行为

Paper trading 是本地观察账户，不是真实账户。

涉及文件：

- 当前开放 paper 持仓：`data/paper_positions.json`
- 已关闭 paper 交易：`data/paper_trade_journal.csv`
- paper 触发更新报告：`data/exports/paper_positions_update_*.json`
- paper 退出动作报告：`data/exports/paper_actions_*.json`

当前 paper 规则：

- 触发后记录本地 paper 持仓。
- 如果同一标的已有开放 paper 持仓，不重复进。
- 如果同一标的当天已经在 paper journal 中有过 entry，不再二次进场。
- paper 持仓记录 `entry_time_et`。
- 持仓监控只评估入场 K 线之后的 K 线，避免把入场前低点误判为止损。
- 第一目标触发后卖一半，并把止损至少移动到入场价。
- 触及止损、breakeven stop 或时间退出时写入 paper journal。

这次会话中发现并修正的重要问题：

- 旧逻辑只记录 `entry_date`，导致止损监控使用当天全部 K 线，可能把入场前低点算作止损。
- 已修正为记录 `entry_time_et`，并且只看入场后的 K 线。
- 旧逻辑允许同一标的当天出场后再次 paper 进场。
- 已修正为读取 paper journal，按 `symbol + entry_date` 做日内一次锁。

## 11. 2026-05-20 当日观察记录

当日 watchlist：

```text
PANW, CSCO, AAPL, ASTS, CRWD, ARM, ALAB, SNOW
```

盘中触发记录：

```text
09:38 ET:
PANW triggered
ARM triggered

09:43 ET:
PANW triggered
CSCO triggered
AAPL triggered
ARM triggered
ALAB triggered
```

当时 paper 状态解释：

- PANW 和 ARM 实际触发过，也被记录过 paper 持仓。
- 随后旧版持仓监控把它们判定为止损退出。
- 后来确认这是 paper 监控逻辑缺陷：入场前低点被错误计入止损判断。
- 这个缺陷已经修复，但历史 paper journal 没有自动回滚。

修复后的当前原则：

- 新 paper 持仓会带 `entry_time_et`。
- 当天同一标的只进一次。
- 未来新触发会按修复后的规则处理。

## 12. 盘后复盘报告

新增脚本：

```text
scripts/generate_daily_review.py
```

快捷命令：

```bash
sh scripts/liubang_live.sh review
sh scripts/liubang_live.sh review-open
```

输出：

- `data/exports/review_*.json`
- `data/reviews/liubang_review_YYYY-MM-DD.html`

复盘内容：

- 当日 signals。
- 当日 triggers。
- workflow 时间线。
- paper 持仓更新。
- paper 退出动作。
- 当前开放 paper 持仓。
- paper journal 当日已关闭交易。
- 数据问题和缺口。

这个 JSON 会成为后续样本库的基础。

## 13. 已修复问题列表

这次会话中处理过的主要问题：

- `sh scripts/liubang_live.sh loop` 报 `LOOP_ARGS[@]: unbound variable`。
- 用户不想用 plist，改为推荐 shell 前台循环。
- dashboard 需要自动刷新，已用 HTML meta refresh。
- dashboard 需要展示当前监控状态和 paper 持仓，已实现。
- workflow 面板位置太靠前，已移动到底部。
- watchlist 今日行情起初没显示，确认原因是旧 HTML 或旧 trigger 报告，并新增行情面板。
- paper 持仓显示问题经确认是旧页面，重建 dashboard 后可见。
- PANW/ARM 触发后没留在开放持仓，定位为旧 paper 监控误判止损。
- paper 监控已改为入场时间之后再判断止损。
- 同一标的一天可能重复进入，已加日内一次锁。
- 新增盘后复盘报告和复盘 HTML。
- smoke tests 已覆盖关键行为。

## 14. 当前验证命令

最近通过的本地检查：

```bash
python3 -m py_compile scripts/render_dashboard.py scripts/smoke_tests.py scripts/generate_daily_review.py
python3 -m py_compile src/liubang/positions.py scripts/record_paper_triggers.py scripts/run_workflow.py
bash -n scripts/liubang_live.sh
.venv/bin/python scripts/smoke_tests.py
sh scripts/liubang_live.sh dashboard
```

## 15. 文件地图

核心入口：

- `scripts/liubang_live.sh`：常用 shell 快捷命令。
- `scripts/run_workflow.py`：组合 workflow。
- `scripts/generate_signals.py`：生成每日信号。
- `scripts/scan_triggers.py`：盘中触发扫描。
- `scripts/record_paper_triggers.py`：把触发写入 paper 持仓。
- `scripts/monitor_positions.py`：监控手动或 paper 持仓。
- `scripts/apply_paper_actions.py`：应用 paper 退出和部分止盈。
- `scripts/render_dashboard.py`：生成 dashboard。
- `scripts/generate_daily_review.py`：生成盘后复盘。
- `scripts/smoke_tests.py`：离线检查。

核心库：

- `src/liubang/signals.py`
- `src/liubang/triggers.py`
- `src/liubang/positions.py`
- `src/liubang/market_data.py`
- `src/liubang/portfolio.py`
- `src/liubang/risk_throttle.py`
- `src/liubang/journal.py`

配置：

- `config/core_universe.json`
- `config/research_universe_dynamic.json`
- `config/manual_positions.example.json`
- `config/trade_journal.example.csv`

中文文档：

- `README.md`
- `docs/strategy_design.md`
- `docs/research_log.md`
- `docs/backtest.md`
- `docs/signals.md`
- `docs/schwab_probe.md`
- `docs/session_share_2026-05-20.md`

## 16. 后续计划

建议下一阶段优先做：

- 持续跑 `loop`，积累 paper 样本。
- 每天收盘后跑 `review`，形成结构化样本库。
- 用复盘 JSON 汇总：触发后 1 小时表现、当日收盘表现、次日表现、5 日内表现。
- 区分“触发但未入场”、“paper 入场”、“paper 出场”三类样本。
- 对 paper 规则继续做误判审计，尤其是入场时间、止损触碰和第一目标触碰。
- 观察至少数周后，再决定是否调整分数阈值、硬止损、回撤窗口或股票池。

## 17. 边界声明

本项目当前只适合研究、观察、paper tracking 和辅助人工决策。它不是自动交易系统，也不是投资建议。任何真实交易都需要用户自行确认、下单和承担风险。
