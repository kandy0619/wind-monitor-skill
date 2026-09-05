# KStock原生融合技术方案

## 目标与边界

把Wind资金监控的调度、运行状态、规范化数据、报告、交付和研究数据接入KStock现有FastAPI、SQLAlchemy与任务体系，同时保持本Skill仓库为唯一业务规则与可移植实现来源。KStock负责基础设施适配与产品能力，不能复制一套会独立漂移的业务口径。

目标机器安装本Skill和项目本地`wind-mcp-skill`，配置KStock数据库、Wind与飞书凭据后，应得到相同的档位路由、计算、卡片和故障恢复行为；自动化提示词只负责调用入口，不承载业务规则。

## 不可变业务契约

- 15:00是最后一个标准10分钟盘中档：`report_type=intraday`，读取上一成功盘中档，生成一张盘中四表卡。
- 15:10是独立统一收盘总结：`report_type=close_summary`，使用15:00收盘值、固定趋势样本、近5日行业和个股统计，形成一个逻辑报告与两个飞书分片。
- 计划档位、报告类型、字段结构和卡片模式必须一致；冲突时进入`pending_render/report_contract_mismatch`，不得猜测、改标题或发送。
- Wind是唯一市场数据源；原始回包先脱敏落盘，规范化失败与Wind无数据严格区分。
- 飞书只向启用MonitorTask配置的唯一群聊发送精简卡；Codex展示精简版和完整审计版。

## 目标架构

```text
KStock Scheduler / 手工触发
  -> Skill Slot Router
  -> Wind CLI Client
  -> Raw Envelope Store
  -> Deterministic Adapter / 受控LLM适配队列
  -> Canonical Observation Store
  -> Skill Calculators
  -> Report Package Store
  -> Skill Card Renderer
  -> KStock Feishu Adapter
  -> Outcome Collector / 事件研究与回归测试
```

Skill以Python包或固定CLI边界向KStock暴露能力。KStock服务只传入项目根目录、绝对时间、任务ID和数据库会话；不得在KStock重新实现档位表、计算公式或卡片模板。

## 数据模型

### `wind_monitor_runs`

一行代表“交易日 + 计划档位 + 模式”的一次逻辑运行。唯一键：`trade_date, planned_time, mode, task_id`。

主要字段：`report_type`、`status`、`triggered_at`、`started_at`、`completed_at`、`wind_data_time`、`failure_stage`、`error_code`、`error_detail_redacted`、`quality_status`、`retry_count`、`skill_version`、`adapter_version`。

### `wind_monitor_request_attempts`

保存每个Wind请求尝试的契约、脱敏原始回包位置或压缩内容、响应哈希、错误分类、耗时、字段映射版本和是否进入LLM适配。不得保存密钥。

### `wind_flow_observations`

保存规范化事实数据，实体类型包括股票、指数、行业、行业个股和上市板块候选。唯一键至少包含`run_id, query_profile, entity_code/name, metric, observed_at`。金额统一保存元，比例保存原始精度，并保存交易日、Wind数据时间、单位来源和provenance。

### `wind_flow_rankings`

保存行业双榜、行业个股Top 3、四板块候选、收盘Top 10、近5日行业/个股六类排名。字段包括`run_id`、`ranking_type`、`direction`、`rank`、`entity_code/name`、`source_board`、`metric_value`、`window_start/end`和`evidence_observation_ids`。

### `wind_monitor_reports`

保存同一计算结果派生的Codex精简版、完整审计版和飞书逻辑报告。字段包括`run_id`、`report_id`、`report_type`、`planned_time`、`normalized_payload`、`calculated_payload`、`concise_markdown`、`audit_markdown`、`required_parts`、`quality_status`。`15:00/intraday/1片`与`15:10/close_summary/2片`使用数据库约束或应用约束锁死。

### `wind_monitor_deliveries`

按报告分片和接收渠道记录渲染卡片哈希、发送状态、尝试次数、最后错误和成功时间。唯一键：`report_id, channel, part_index, recipient_config_version`。接收标识只通过KStock配置解析，不写入日志或用户可见报告。

### `wind_event_outcomes`

为历史研究保存入榜事件后的价格与收益。字段包括`ranking_id`、`entry_trade_date`、`horizon`、`entry_price_rule`、`exit_trade_date`、`exit_price_rule`、`raw_return`、`benchmark_return`、`excess_return`、`max_favorable_excursion`、`max_adverse_excursion`和`data_version`。

## 运行状态机

```text
scheduled -> collecting -> normalizing -> calculating -> persisted
          -> rendered -> pending_send -> delivering -> completed

可恢复分支：pending_fetch / pending_adapt / pending_calculate /
            pending_render / pending_send / completed_with_limits
终止分支：failed_terminal（仅不可恢复的配置或契约错误经人工确认后）
```

每次固定档触发先处理当日最早`pending`，再处理当前档。重试优先复用已持久化阶段：发送失败不重新取Wind，渲染失败不丢计算结果。档位只有在所需分片全部成功后完成。

## Wind格式自愈

1. 确定性适配器先处理已知信封、表格形态、别名和单位。
2. 映射失败保存脱敏原始回包，分类为`shape_mismatch/field_missing/unit_ambiguous/parse_failed`，不写`no_data`。
3. LLM只输出候选映射或受限`adapt(raw)`代码；沙箱禁止网络、进程、文件系统和市场数值生成。
4. 对候选结果执行Schema校验、单位证明、原始值回读、业务行数约束和回归样本测试。
5. 单次运行可使用通过验证的临时适配结果继续，但永久代码修改必须形成补丁、增加脱敏fixture、通过全量测试并保留可回滚版本；不得直接热改生产工作树后无审计生效。

## KStock服务边界

- `WindMonitorSchedulerAdapter`：调用Skill路由并维护数据库运行锁，替代单一`run_time`的普通策略调度语义。
- `WindMonitorRepository`：持久化运行、请求、观测、排名、报告与交付；事务边界按阶段提交。
- `WindMonitorSkillRunner`：以固定CLI/包接口调用本仓库脚本，校验Skill版本和结果Schema。
- `FeishuReportDeliveryAdapter`：只解析启用任务中的唯一`feishu_chat_id`，调用现有`send_card`并保存分片结果；禁止同时发送个人账号。
- `WindOutcomeService`：盘后按交易日历补齐未来1/3/5/10/20/60交易日结果，供回测与研究API使用。

现有通用MonitorTask与MonitorReport可保留为任务配置和页面入口，但不应继续用一个`feishu_sent`布尔值表示多分片交付，也不能用小时/每日频率模型承载30个固定业务档位。应新增上述资金监控实体，并通过`task_id`关联现有任务。

## 研究与回归测试

首期事件定义：收盘Top 10入榜。默认入场规则为下一交易日开盘，持有期为1、3、5、10、20和60个交易日；同时计算沪深300或用户选择基准的超额收益。分层维度包括综合排名、资金趋势、主力净流入分位、涨跌幅分位、所属行业和市场板块。

避免未来函数：排名数据只使用报告生成前已持久化的Wind数据；价格复权方式、停牌、涨跌停无法成交和退市处理必须版本化。研究结果按数据版本可重复计算，不覆盖旧版本。

## API与页面建议

- 任务页：档位运行时间线、失败阶段、待重试、Skill/适配器版本。
- 报告页：盘中与收盘类型分栏；15:00明确标识“盘中”，15:10标识“收盘总结”；展示飞书分片状态。
- 数据质量页：Wind请求错误分类、字段映射变化、LLM适配候选及审批状态。
- 研究页：Top 10事件样本数、胜率、均值/中位数收益、超额收益、MFE/MAE、分组对比与样本明细。

## 迁移计划

1. **契约固化**：先在Skill完成15:00/15:10硬隔离、Schema校验和离线回放，本次改造属于此阶段。
2. **影子落库**：KStock新增表与Repository，Skill文件状态仍为主，数据库同步写入，连续5个交易日比对哈希与数量。
3. **数据库主存**：数据库成为运行和研究事实库，Skill继续输出可移植JSON审计包；文件仅作应急导出。
4. **原生调度与交付**：KStock调度器调用Skill路由，接管锁、重试与分片发送，移除长自动化提示词依赖。
5. **事件研究**：上线结果补齐、回归测试API和页面，历史回填另行授权执行。

## 验收门槛

- 15:00只产生`intraday`、一张四表卡，标题不含“收盘”；15:10只产生`close_summary`、同报告ID两张卡。
- 任意混淆载荷在发送前失败并进入`pending_render`，不能通过字段形状或标题修补绕过。
- 相同档位并发触发只有一个逻辑运行；发送第二片失败时只补发第二片。
- Wind原始回包、规范化值、排名、报告和未来收益可从数据库追溯到同一run与版本。
- 连续5个交易日影子运行的盘中/收盘数值、排名、卡片哈希和状态与Skill文件流程一致。
- 日志、API和导出均不暴露Wind Key、飞书密钥或接收标识。

## 数据保留建议

- 脱敏原始Wind回包：在线保留90天，之后归档或按合规策略清理。
- 规范化观测、排名、报告、交付摘要与研究结果：长期保留。
- 生成卡片正文可保留压缩JSON和哈希；凭据、接收标识与未脱敏错误永不进入分析表。
