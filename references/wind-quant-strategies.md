# Wind D/E模拟交易扩展 v2

用户于2026-09-12明确授权新增模拟交易阶段的飞书通知；以下规则用于D/E，不改变原监控报告的档位及唯一卡片契约。

## 职责与统一入口

KStock原模拟交易账户配置本金并绑定 `wind_quant:D` / `wind_quant:E`。策略类位于 `backend/quant/strategy/wind_flow_strategy.py`，通过原策略扫描器管理。配置、持仓、买卖记账、交易任务日志复用KStock现有服务；Wind快照、批次状态、审计和通知增加专用扩展表。

KStock现有模拟交易调度器启动Wind工作进程，Agent原 `monitor_runtime.py --backend kstock poll` 仍可协同触发同一采集入口。两者用数据库阶段租约和账户快照幂等去重；不要另建Agent定时任务。Agent不自行计算模拟成交或声称订单已成交。

## 采集和执行时点

- 09:30：`--phase open_window`，持续取新报价至09:40。E只在09:35前新买；D/E仍持仓的批次转流出或达到上限则退出。部分退出、停牌、跌停保留待退出状态；暂停新买不能关闭已有退出义务。开盘复权参考在同一工作进程内缓存，避免每10秒重复查询历史。
- 14:50：`--phase window`，在全A当日主力净流入**金额Top10**中，按主力净流入/成交额取强度TopN，先选再过滤、不递补。不能使用原监控“四板块各Top5合并榜”代替。
- 14:55：按当时新卖一价买入。**默认不再受14:50冻结价格上限限制**；新报价更高时按预留预算重新计算更少的股数。仍要验证官方涨跌停、交易状态、报价时效、现金、申报单位和盘口容量。旧冻结限价仅作为可显式选择的对照配置 `entry_price_policy=frozen_limit`；默认 `current_ask`。
- 14:55恢复入口：`--phase execute_window`只处理已持久化的待买意向，不重新选号；供进程重启后的剩余窗口恢复。
- 14:56截止：不再新买，未成交意向过期释放预留，不追入集合竞价。
- 15:00：持仓估值、日终资金；股票跌出榜单仍采集。
- 15:10：全A收盘Top10、E所需原始/后复权历史、所有模拟持仓日终资金。生成量化收盘持仓总结及次日卖出计划。

资金观察从信号T+1开始，负值才触发下一交易日开盘卖；0不触发，缺失不当0。默认最大退出时点是T+6开盘（从T+1计5个交易日间隔），不是5个自然日。

## 数据契约

市场数据只用项目内 `.agents/skills/wind-mcp-skill`。每次Wind返回先脱敏存 `/api/v1/wind-quant/raw`，再经确定性适配发 `/snapshots`。资金榜时间、股票报价时间、采集接收时间和交易记录时间分别保存，不能互相冒充；日历按Wind真实交易日。盘中资金榜只有日期无可核验时分秒时，保存并记录 `rank_time_unknown_or_stale`，不伪造为14:50资金信号。

股票查询入口为 `stock_data.search_stocks`、`get_stock_price_indicators`、`get_stock_kline`；指数为对应index工具。原始价 `aftype=2`、后复权 `aftype=1`。盘口数量要求股；官方申报单位属于单独版本化规则，未知则不买。

适配失败执行主技能自愈流程，保留原回包与具体异常；不得编造数值或换源。14:55每次实时查询独立缓存、单次15秒时限，不能把交易补跑与原报告无限期补跑混为一谈。

## 飞书通知

由**KStock**按已提交事实生成并通过原 `feishu_bot.send_card` 发出；Agent不再重复发送同一交易通知。收件群由现有启用MonitorTask解析，优先“监控-神龙7-全盘”，不读取或展示群聊ID。

通知覆盖：D买入计划、E次日候选、买入成交、卖出成交、执行结果/未执行原因、下一日卖出计划、入场过期，以及包含现金、资产、持仓数量/市值和待退出义务的量化收盘总结。所有标题标注“模拟盘”；功能演练必须再标注“合成数据”。这些量化事件卡独立于原监控盘中/收盘卡。

通知与账本事务一起入库，后台Outbox异步发送；失败只重发同一不可变卡片，使用稳定Feishu消息UUID，不重跑策略或重复下单。选股通知入队在买入前，但网络投递不阻塞交易窗口；不得把发送失败报成交易失败或把入队报成已送达。

## 运维与本地演练

### 五个头部命名策略

KStock V4.2.0支持 `wind_quant:D_GE10`、`wind_quant:E_GE10_LT15`、`wind_quant:E_LT15`、`wind_quant:D_GE5`、`wind_quant:D_BASE`，分别表示D强度≥10%、E强度[10%,15%)、E强度<15%、D强度≥5%、D不附加强度门槛。均在原强度Top3中再过滤，不递补。策略范围由KStock配置和代码负责，Agent仍按D/E采集共同数据，不按账户重复请求榜单，也不把过滤规则重写在Agent里。

跨电脑初始化使用KStock的 `backend/scripts/provision_wind_leader_accounts.py --enable`，每个策略独立模拟账户，重复执行不重置资金。账户ID由目标数据库生成，不硬编码原机器ID。

本机 `references/wind-quant-local.json` 和旧脚本备份不提交仓库、不复制到另一台电脑。桥接程序优先使用显式 `KSTOCK_WIND_QUANT_PROJECT_ROOT`，其次本机配置，再使用调用方项目路径；优先启动目标项目 `.venv` 的Python，缺少时才使用当前Python。迁移机器后必须配置目标项目Wind凭据和KStock连接。

后端 `/api/v1/wind-quant` 与原 `/api/v1/paper-trade` 共用KStock服务。`KSTOCK_WIND_QUANT_URL`默认8002端口，认证沿用环境变量 `KSTOCK_AGENT_TOKEN`；未配置时仅loopback。项目路径由本地 `references/wind-quant-local.json` 或环境变量配置。

本地完整演练运行 `python backend/scripts/run_wind_quant_demo.py`。它在独立SQLite里通过原账户API创建不同本金的D/E账户，压缩时间完成选股、买入、收盘、转负、暂停新买后卖出、通知和回放；不向生产账户写合成成交。输出的收益只用于检验记账，不能作为策略收益验证。原有a-10任务无需新增时间档。
