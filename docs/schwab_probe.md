# Schwab 探针

`scripts/schwab_probe.py` 用来验证短线美股策略的第一条数据路径。默认只检查市场数据，不请求账户或持仓权限。

## 检查内容

- 读取现有 Schwab token 文件
- 可选读取账户摘要和持仓
- 拉取批量 quotes
- 拉取 5 分钟价格历史
- 本地计算最近交易日 VWAP
- 写出已脱敏 JSON 报告

## 安装

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

配置读取优先级：

1. 命令行参数
2. 当前 shell 环境变量
3. `.env`
4. `~/.zshrc` 中缺失的变量

必要变量：

```bash
export SCHWAB_APP_KEY=...
export SCHWAB_APP_SECRET=...
export SCHWAB_TOKEN_PATH=/path/to/schwab_token.json
```

## 运行

```bash
python scripts/schwab_probe.py
```

默认标的：

```text
AAPL,NVDA,TSLA,QQQ,SPY
```

默认是 market-data-only 模式。第一版系统假设手动下单，因此不需要账户和持仓接口。

确实需要账户检查时再加：

```bash
python scripts/schwab_probe.py --include-accounts
```

覆盖标的：

```bash
python scripts/schwab_probe.py --symbols AAPL,NVDA,TSLA
```

只有调试时才写完整原始 API 报告：

```bash
python scripts/schwab_probe.py --write-debug-report
```

报告写入 `data/exports/`，该目录被 git 忽略。

## 401 排查

如果启用 `--include-accounts` 后账户接口返回 `401 Unauthorized`，探针仍会继续执行市场数据检查。优先检查：

- token 文件是否由同一个 `SCHWAB_APP_KEY` 生成
- Schwab app 是否处于 `Ready for Use`
- 账户是否启用 Trader API 权限
- token 是否被撤销或超过 Schwab refresh 限制

如果只做本策略的观察和手动下单流程，可以不处理账户权限问题。
