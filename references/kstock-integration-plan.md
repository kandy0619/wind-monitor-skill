# KStock原生融合技术方案与实施基线

## 已确认的职责边界

- Codex自动化是唯一业务触发入口，只负责在30个固定工作日时点调用本Skill；不在提示词复制业务规则。
- 本Skill是唯一业务实现：时区与档位路由、Wind查询、确定性/LLM受控适配、计算、15:00与15:10契约、Codex报告、飞书卡片均在仓库内维护。
- KStock常驻FastAPI只负责基础设施：MySQL事务、运行租约、幂等、CRUD、查询、飞书接收目标解析与outbox补偿。KStock后台任务不得调用Wind或自行生成报告。
- 正式运行以MySQL为唯一真相源；`.codex/automation-state`文件只允许离线测试、历史回放和显式一次性迁移。
- Wind是唯一市场数据源。KStock现有其它行情源不得替代或补齐本监控数据。

## 不可变报告契约

| 契约 | 15:00 | 15:10 |
| --- | --- | --- |
| mode | `intraday` | `close` |
| report_type | `intraday` | `close_summary` |
| previous | 上一成功盘中档，通常14:50 | 禁止传入 |
| 卡片 | 一张盘中四表卡 | 一个报告ID的一张统一决策摘要卡 |
| 数据口径 | 15:00实时/收盘附近值 | 15:00收盘值 + 固定样本 + 行业/个股5日 |

时间、类型、字段和卡片结构任一冲突均返回契约错误并停止发送。15:00不会因为数据接近收盘而变成收盘报告；15:10不会拆成两张卡。

## 目标数据流

```text
Codex固定时点唤醒
  -> Skill按Asia/Shanghai路由
  -> KStock HTTP读取当天运行/认领租约
  -> 项目本地wind-mcp-skill取数
  -> 每次脱敏Wind回包立即写KStock
  -> Skill确定性适配 / 受控LLM二次适配
  -> 规范化事实与排名写KStock
  -> Skill计算并生成Codex精简版+审计版
  -> Skill官方renderer生成并校验唯一卡片
  -> 报告与不可变卡片写KStock
  -> KStock解析MonitorTask唯一feishu_chat_id并发送
  -> 成功后完成档位；失败由outbox重发同一卡片
```

网络中断不会迫使流程重取Wind：请求尝试、事实、报告和卡片均按阶段事务提交。outbox只处理已经渲染并持久化的卡片，因此不会改变数值、标题或卡片内容；接收群始终由KStock在发送时解析，Skill不持有接收标识。

## HTTP接口 v1

根路径：`/api/v1/internal/wind-monitor`。客户端先访问`GET /capabilities`协商`contract_version=1.0`。

- `GET /context`：选择启用且配置群聊的MonitorTask，优先`监控-神龙7-全盘`；只返回任务ID、任务名和接收配置版本哈希。
- `GET /runs/day`：返回当天全部运行，用于已完成去重和未完成恢复；不返回租约令牌。
- `GET /runs/pending`：返回可恢复运行。
- `POST /runs/claim`、`POST /runs/{id}/lease/renew`：唯一档位认领与租约续期。
- `POST /runs/{id}/attempts`：保存请求契约、脱敏压缩回包、哈希、错误分类与适配版本。
- `POST /runs/{id}/facts:commit`：首次原子写入该运行的规范化观测和排名并保存内容哈希；相同内容可幂等重放，不同内容直接冲突。
- `POST /runs/{id}/adapter-incidents`：记录格式漂移、候选映射、生成适配器哈希和验证结果。
- `POST /runs/{id}/fail`：把取数、适配、计算、渲染或发送错误写为可恢复状态。
- `POST /runs/{id}/report:commit`：事实落库后保存同源报告及内容哈希；服务端再次验证15:00/15:10契约，重试不得改写报告。
- `POST /reports/{id}/card:commit`：保存卡片JSON与哈希；相同幂等键内容变化直接冲突。
- `POST /reports/{id}/feishu:dispatch`：KStock内部解析群聊并发送，不在响应中返回接收ID或消息ID。
- `POST /runs/{id}/complete`：只有事实已落库、必需报告存在且所有卡片成功发送才允许生产运行完成。
- `GET /reports/previous`、`GET /observations`、`GET /rankings`：向Skill提供上一成功盘中档、固定样本和跨日统计数据；默认只查询`production`，可显式查询迁移/回放数据。

认证使用环境变量`KSTOCK_AGENT_TOKEN`的Bearer令牌；令牌未配置时只允许loopback。Skill不接受密钥命令行参数。

## MySQL模型

- `wind_monitor_runs`：逻辑运行、报告类型、状态、错误、数据质量、事实哈希、Skill/适配器/契约版本与租约。唯一键为`task_id + trade_date + planned_time + mode + run_kind`。
- `wind_monitor_run_events`：只追加的状态迁移审计。
- `wind_monitor_request_attempts`：请求尝试与脱敏原始回包。回包使用gzip JSON写MEDIUMBLOB，并保存SHA-256。
- `wind_flow_observations`：股票、指数和行业规范化事实；金额统一DECIMAL元，比例保留原精度，附Wind时间和provenance。
- `wind_flow_rankings`：行业双榜、行业个股、板块候选、收盘Top 10及未来扩展榜单。
- `wind_adapter_incidents`：格式自愈审计，不允许把解析失败记录成`no_data`。
- `wind_monitor_reports`：规范化/计算载荷、Codex精简/审计Markdown、报告哈希、卡片契约和质量状态；Markdown使用MEDIUMTEXT避免完整审计版被截断。
- `wind_monitor_deliveries`：不可变卡片、哈希、分片、重试、脱敏错误和消息ID哈希；永不保存接收ID。
- `wind_event_outcomes`：入榜后1/3/5/10/20/60交易日收益、基准、超额、MFE和MAE。

ORM由KStock维护，版本化迁移为`V4.2.0__add_wind_monitor_storage.py`。SQLite只用于快速单测，发布门槛包含MySQL 8隔离库DDL与事务验证。

## 可恢复状态机

```text
scheduled -> collecting -> persisted -> pending_send -> completed
                |              |             |
          pending_fetch  pending_render  pending_send
          pending_adapt  pending_calculate
```

- 当天档位查询必须同时返回终态和非终态：终态用于静默去重，非终态用于按计划时间恢复。
- claim只向认领者返回租约令牌；普通查询永不返回。
- 生产运行必须出现`facts_commit`审计事件；报告档还必须存在报告和足量`sent`投递。
- `historical_replay`、`preview`和`legacy_import`使用独立`run_kind`，不会占用生产档位，也不会发送飞书。

## Wind格式自愈

1. 确定性适配器处理已知信封、字段别名、行列式结构和单位。
2. 失败时保存脱敏原始回包，分类`shape_mismatch/field_missing/unit_ambiguous/parse_failed`。
3. LLM只生成候选映射或受限纯函数`adapt(raw)`，禁止网络、进程、文件和市场数值生成。
4. 通过Schema、单位证明、原始值回读、行数约束和历史fixture回归后，本次运行可使用候选结果。
5. 永久代码适配必须生成补丁、增加脱敏fixture、跑全量测试、提交版本并可回滚；禁止无审计热改生产工作树。

## 历史迁移

`scripts/import_legacy_state.py`默认仅扫描。增加`--apply`后将旧盘中规范化数据、固定趋势样本和收盘Top 10写为`legacy_import`运行，不发飞书、不占生产档。

旧文件缺少完整Wind原始回包，导入记录必须标记`raw_missing/completed_with_limits`。重复执行通过档位唯一键和请求尝试唯一键保持幂等。旧数据不足以组成新版行业/个股5日统一收盘报告时，只导入事实，不伪造报告。

## 研究能力后续阶段

首期事件为收盘Top 10入榜，默认下一交易日开盘入场，持有1/3/5/10/20/60个交易日，同时计算沪深300或配置基准的超额收益、MFE和MAE。所有入场/出场价格规则、复权、停牌、涨跌停不可成交和数据版本必须固化，防止未来函数。

按综合排名、资金趋势、主力净流入分位、涨跌幅分位、行业、板块和三维共振分组。样本达到门槛后，才可以根据胜率、收益分布和回撤升级“优先观察/等待确认/避免追高/持仓风控”等标签。个性化卖出建议仍需KStock持仓成本、仓位和风险约束，不能由通用资金榜单直接推出。

## 发布验收门槛

- 15:00只接受盘中契约，15:10只接受统一收盘契约且`required_parts=1`。
- 同档并发只有一个逻辑运行；已完成重复触发在路由层静默。
- Wind回包在规范化前脱敏压缩保存，API/日志不暴露Wind Key、飞书密钥、群聊ID或消息ID。
- 发送失败保留同一卡片哈希；补偿成功后才完成档位，补偿过程零Wind调用。
- 旧JSON迁移默认dry-run、显式apply、不发飞书、可重复执行。
- Skill与KStock全量单测通过，并在隔离MySQL 8完成DDL、DECIMAL、MEDIUMBLOB、唯一键和恢复流程验证。
