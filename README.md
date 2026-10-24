# 美股信用利差每日监控

零运行依赖的 Python + 静态 HTML 项目。每天北京时间约 23:30 通过 GitHub Actions 检查 FRED，保存完整的**本项目已获取**历史，并生成可搜索、排序、缩放和导出 CSV 的中文仪表盘。首次启动会下载 FRED 当时全部可访问的观测；FRED 自 2026 年 4 月起对这两条 ICE BofA 序列仅开放近三年，项目不能补回 FRED 不再提供的更早历史。

## 数据和口径

- HY：`BAMLH0A0HYM2`；BBB：`BAMLC0A4CBBB`。单位是**百分比**，如 `3.40` 表示 3.40%，即约 340 基点。两者与原截图里的 Bloomberg 指数不完全同口径，不作一比一比较。
- 官方来源：[HY](https://fred.stlouisfed.org/series/BAMLH0A0HYM2)、[BBB](https://fred.stlouisfed.org/series/BAMLC0A4CBBB)。公开 CSV 下载不需要 FRED API key。
- 原始表保留全部非空观测，包括 FRED 在周末月末或假日给出的值；信号计算只采用美股常规交易日，跳过周末、纽约证券交易所常规假日和 `data/non_trading_dates.csv` 中手工补充的临时休市日。缺失行不补值。每个序列分别计算，交易日历无需假设完全相同。未来若出现临时休市，请在例外表增加日期后运行 `python monitor.py build` 只会重建页面；需运行 `python monitor.py sync` 才会重算信号。
- 每次检查重新下载 FRED 当前可访问窗口，与本地历史按序列和观测日合并；新增历史永久保留，不因为 FRED 滚动窗口而删除。若历史数值变化，`data/revisions.csv` 记下旧值、新值和发现时间，历史信号随修订重算。
- `data/status.json` 与仪表盘分别标注**检查时间**、**最新观测日**和 FRED 序列页面标出的**页面更新时间**，并计算页面更新距检查的小时数。页面时间是整条序列的更新时间，并非单条观测的发布时间；页面解析失败时显示“未能获取”，不以检查时间冒充源时间。`data/runs.jsonl` 记录每次检查，即使没有新观测；仪表盘显示观测滞后天数。定时任务可能晚于 23:30 启动。

## 信号定义

每条序列独立计算，严格使用 `>`，不使用未来观测生成当日信号：

1. **候选高点**：当前值严格高于前 20 个有效观测值；至少已有 20 个值才开始判定。
2. **连续 3 / 5 日下行**：候选高点之后，连续 3 / 5 个有效观测值逐日严格下降，且低于该高点。持平或上涨中断计数；此后可重新累计。
3. **正式历史峰值**：高点严格高于之后 20 个有效观测值，仅在第 20 个后续观测日确认。表中“观测日”是确认日，“关联高点”是原高点日。
4. 后续任何一个值 **大于或等于** 候选高点，该候选峰值失效；并列高点本身也不能满足候选所需的严格大于条件。历史回测只按当时及此前数据发信号；修订后的回测表会重算，已抓取的修订证据留在修订文件中。

这是一套观察规则，不构成交易建议。

## 本地运行

需要 Python 3.10+ 和连接 FRED 的网络：

```bash
python -m unittest discover -s tests -v
python monitor.py sync
python -m http.server 8000
```

浏览器打开 `http://localhost:8000`。也可直接打开 `index.html`，但部分浏览器限制本地 CSV 下载。`python monitor.py build` 可从已有 CSV 重建网页，无需联网。

## GitHub 部署与每日自动更新

1. 本项目仓库为 [`mzhenry/credit-spread-monitor`](https://github.com/mzhenry/credit-spread-monitor)。工作流必须位于默认分支的 `.github/workflows/daily.yml`。
2. 仓库 **Settings → Actions → General → Workflow permissions** 选择读写权限，允许工作流提交每日 CSV。仓库 **Settings → Pages → Build and deployment → Source** 选择 **GitHub Actions**。
3. 到 **Actions → 每日信用利差更新 → Run workflow** 手动执行一次。成功后在 Pages 部署链接查看仪表盘；之后每天北京时间约 23:30 自动检查。GitHub 计划任务可能延迟或因仓库长期无活动被停用，需留意 Actions 运行状态。
4. 若需私有仓库，需确认账户的 Pages 权限和计划。也可只保留仓库数据，不公开页面。

工作流失败时不会把失败抓取当成新数据。两条序列只要有一条失败，任务报错并停止提交/部署，便于在 Actions 发现问题。手工重复运行不会重复观测行；检查日志会如实多记一次。项目没有接入可选的 SPX/NDX/QQQ/SOXL，避免增加不同来源与非同步收盘带来的前瞻偏差。

## 文件

`data/observations.csv` 为持久化主表；`data/signals.csv` 为按当前修订版本重算的历史信号；`data/revisions.csv` 为修订审计；`data/runs.jsonl` 为每次检查日志；`data/status.json` 为最近状态。`index.html` 是可直接托管的静态仪表盘。
