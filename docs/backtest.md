# 回测框架

这个回测框架用于验证短线选股策略的基本形态，不是最终优化结论。当前策略核心是“强势股浅回撤 + 盘中确认 + 5 天内退出”。

## 策略假设

默认测试逻辑：

- 强势股浅回撤
- 回撤深度硬过滤，默认 1% 到 6%
- 5 分钟盘中确认
- SPY 和 QQQ 用于市场 regime 标注
- XLK 和 SMH 只作为行业上下文，不参与交易
- 手动下单假设，使用 limit 或 stop-limit 风格成交
- 单笔风险为账户权益 1%
- 单票资金上限为账户权益 20%
- 当前已验证观察配置使用 3% 硬止损上限
- 1R 卖出一半
- 第一目标达成后，剩余仓位止损移动到 breakeven
- 第 5 个交易日强制退出
- 可选单票冷却期，避免刚退出的股票立刻重新入选

## 基础运行

```bash
.venv/bin/python scripts/run_backtest.py
```

默认股票池：

```text
config/core_universe.json
```

覆盖股票列表：

```bash
.venv/bin/python scripts/run_backtest.py --symbols AAPL,NVDA,TSLA
```

运行当前已验证观察配置：

```bash
.venv/bin/python scripts/run_backtest.py \
  --universe config/research_universe_dynamic.json \
  --hard-stop-pct 0.03 \
  --symbol-cooldown-days 0
```

刷新 Schwab 历史缓存：

```bash
.venv/bin/python scripts/run_backtest.py --refresh
```

直接使用 yfinance 5 分钟历史：

```bash
.venv/bin/python scripts/run_backtest.py --history-provider yfinance
```

默认行为是 Schwab 优先，缺失时使用 yfinance fallback：

```bash
.venv/bin/python scripts/run_backtest.py \
  --history-provider schwab \
  --fallback-history-provider yfinance
```

## 参数

当前默认最低分数为 `7.0`。最新通过验证的观察配置为：

- 股票池：`config/research_universe_dynamic.json`
- `min_score=7.0`
- `min_pullback_pct=0.01`
- `max_pullback_pct=0.06`
- `hard_stop_pct=0.03`
- `symbol_cooldown_days=0`

调整最低分数：

```bash
.venv/bin/python scripts/run_backtest.py --min-score 8
```

运行小型参数 sweep：

```bash
.venv/bin/python scripts/sweep_backtest.py
```

## 选股评分模式

目前支持三种评分模式：

- `classic`：原始二元打分；可用 `--scoring-mode classic` 回退旧口径。
- `ranked_v1`：先沿用 `classic` 的硬过滤和主分数，再用同一天候选的横截面排名做小幅排序 overlay。它会重点看 20/60 日相对 QQQ 强度、距离 20 日高点、相对 SMA20 位置、ATR 标准化回撤、缩量程度和收盘位置。
- `ranked_v2`：当前默认。候选准入仍沿用 `classic`，主分数仍以 `classic` 为底，只用 20 日相对 QQQ 强度、60 日相对 QQQ 强度和相对 SMA20 距离做小幅重排。

默认回测已使用 `ranked_v2`，并默认启用 regime-aware v2 过滤：

- `strong`：自动加严格过滤，要求 `ranked_v2_rs_20d_rank >= 0.65`、`ranked_v2_overlay_score >= 7.4`、`atr20_pct <= 0.08`、`pullback <= 5%`
- `neutral`：沿用普通 `ranked_v2`
- `weak`：阻止新开仓候选

显式运行：

```bash
.venv/bin/python scripts/run_backtest.py \
  --universe config/research_universe_dynamic.json \
  --hard-stop-pct 0.03 \
  --symbol-cooldown-days 0 \
  --scoring-mode ranked_v2
```

对比旧评分和新评分：

```bash
sh scripts/liubang_live.sh compare-scoring
```

或直接运行：

```bash
.venv/bin/python scripts/compare_scoring_modes.py \
  --universe config/research_universe_dynamic.json \
  --hard-stop-pct 0.03 \
  --symbol-cooldown-days 0
```

对比报告写入 `data/exports/scoring_compare_*.json`。如需回退旧口径，传入 `--scoring-mode classic`。

寻找 5 分钟和 1 小时代理同时有效的过滤组合：

```bash
sh scripts/liubang_live.sh cross-scoring
```

或直接运行：

```bash
.venv/bin/python scripts/cross_validate_scoring_strategies.py \
  --universe config/research_universe_dynamic.json \
  --max-configs 1152 \
  --progress-every 200
```

该脚本会按 `min(5m_pf, hourly_pf)` 排序，报告写入 `data/exports/scoring_cross_validation_*.json` 和 `.csv`。

运行单票冷却 sweep：

```bash
.venv/bin/python scripts/sweep_cooldown.py --cooldowns 0,1,2,3,4
```

## 压力测试和长历史代理

紧凑压力测试：

```bash
.venv/bin/python scripts/stress_backtest.py
```

完整验证当前观察配置：

```bash
.venv/bin/python scripts/run_research_suite.py \
  --universe config/research_universe_dynamic.json \
  --hard-stop-pct 0.03 \
  --symbol-cooldown-days 0
```

日线长历史代理：

```bash
.venv/bin/python scripts/run_daily_proxy_backtest.py
```

日线代理使用 yfinance 1 日线，默认周期 `5y`。它近似使用下一日开盘成交，不能证明 5 分钟 VWAP/prior-high 触发一定发生。

小时级代理：

```bash
.venv/bin/python scripts/run_hourly_proxy_backtest.py
```

小时级代理使用 yfinance 1 小时 K 线，默认周期 `2y`。它比日线代理更接近盘中策略，但仍不是 5 分钟级精确验证。

## 财报过滤

默认通过 yfinance 启用财报过滤：

- 财报日前一个交易日不新开仓
- 财报日不新开仓
- 持仓遇到财报风险日会退出
- 缓存路径：`data/cache/earnings_calendar_yfinance.json`

刷新财报缓存：

```bash
.venv/bin/python scripts/run_backtest.py --refresh-earnings
```

关闭财报过滤用于对照：

```bash
.venv/bin/python scripts/run_backtest.py --earnings-source none
```

使用手工财报文件：

```bash
.venv/bin/python scripts/run_backtest.py \
  --earnings-source file \
  --earnings-calendar config/earnings_calendar.example.json
```

## 报告

回测报告写入 `data/exports/`，历史缓存写入 `data/cache/`，两者都被 git 忽略。

导出交易 CSV：

```bash
.venv/bin/python scripts/run_backtest.py \
  --trades-csv data/exports/latest_backtest_trades.csv
```

回测报告包含：

- 候选数量
- entry attempts、filled entries、unfilled entries
- 因组合槽位已满跳过的候选
- 因单票冷却期跳过的候选
- 每笔交易 R multiple
- 持仓交易日数
- 主要退出原因
- profit factor 和 average R
- 按 symbol、regime、退出原因、退出月份汇总
- Schwab/yfinance 数据来源统计和 fallback 标的

分析最新回测：

```bash
.venv/bin/python scripts/analyze_backtest.py
```

分析器会输出 entry funnel、集中度、主题表现、最好/最差标的、退出原因、前后半段稳定性、年度/季度稳定性和正负月份数量。

## 消融研究

单票消融：

```bash
.venv/bin/python scripts/ablate_symbols.py
```

主题消融：

```bash
.venv/bin/python scripts/ablate_themes.py
```

消融是研究诊断，不是自动删除股票池的规则。只有在更长历史和纸面交易也支持时，才应考虑调整股票池。

## 验证门槛

验证最新回测、压力测试、小时代理、日线代理和消融报告：

```bash
.venv/bin/python scripts/validate_strategy.py
```

验证报告会写入 `strategy_validation_*.json`，检查：

- 样本交易数量
- 正收益
- average R 为正
- profit factor
- 最大回撤
- 5 天最大持仓约束
- 压力测试是否全部为正
- 单票和主题依赖
- 小时代理稳健性
- 日线代理月份稳定性
- top contributor 集中度

需要 CI 风格失败码时使用：

```bash
.venv/bin/python scripts/validate_strategy.py --fail-on-gate-failure
```

## 当前限制

- Schwab 5 分钟历史窗口有限。
- yfinance 5 分钟历史可补缺，但通常比 Schwab 短。
- 财报过滤依赖 yfinance，实盘前应检查数据质量。
- 新闻和期权只做上下文，不参与回测分数；GEX 暂不进入历史回测，除非有历史 GEX 数据源。
- 默认回测使用固定或指定股票池；当天动态 screener 不应直接混入历史回测，除非先生成明确的研究 universe 文件。
- 组合权益曲线是简化的 closed-trade 曲线。
