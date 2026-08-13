# Wind查询与状态映射

## 依赖

使用同级 `wind-mcp-skill`。调用前完整读取其 `SKILL.md`，并按领域读取：

- 股票：`references/stock.md` 和需要时的 `stock-indicators.md`。
- 指数：`references/index.md` 和需要时的 `index-indicators.md`。
- 跨实体行业及排名：`references/analytics.md`。

## 批量股票字段

调用 `stock_data.get_stock_price_indicators`，代码：

`300476.SZ,300502.SZ,600971.SH`

字段逐字使用：

`最新交易日,交易时间,中文简称,涨跌幅,当日主力净流入额,当日主力净流入占比,该日机构资金净流入额,该日大户资金净流入额,该日中户资金净流入额,该日散户资金净流入额`

## 批量指数字段

调用 `index_data.get_index_price_indicators`，代码：

`000001.SH,399001.SZ,399006.SZ,000688.SH`

字段逐字使用：

`最新交易日,交易时间,中文简称,当日主力净流入额,当日主力净流入占比`

指数没有可靠返回机构、大户、中户、散户时只报告主力。

## 行业排名

使用 `analytics_data.get_financial_data`，问题必须同时要求：

- 日期为当天。
- 全部A股，Wind行业分类。
- 行业主力净流入最高5个和最低5个。
- 行业主力流入额、流出额、净流入额。
- 每个行业完整成分范围内主力净流入最高3只股票及其简称、代码、主力净流入额、涨跌幅。
- 行业和个股都按主力净流入额排序。

若返回把同一股票按资金类型拆成多行，视为明显不匹配；按Wind契约允许的重写边界，要求“每只唯一股票一行，不按机构/大户类型拆行”。

## 四板块Top 5

分别调用结构化排名，范围必须是：

- 沪市主板A股。
- 深市主板A股。
- 创业板A股。
- 科创板A股。

每个问题要求5只不同股票、每只一行，并返回板块内排名、简称、Wind代码、Wind行业、涨跌幅、主力净流入额、主力净流入占比和交易时间。不要用上证指数、深证成指、创业板指、科创50成分股替代上市板块范围。

## 单位

严格使用Wind返回的单位元数据。金额为元时除以1亿展示为亿元；金额已为亿元时不再换算；百万元除以100换算为亿元。百分比保留Wind字段本身的百分比口径，不在缺少元数据时擅自乘100。

## 状态文件

### 盘中状态

路径：`.codex/automation-state/a-share-watchlist-main-flow-10m.json`

顶层包含 `trade_date`、`baseline_type`、`baseline_note`、`stocks`、`indexes`。每个实体保存 `name`、`open_baseline_yuan`、`previous_yuan`、`previous_time`。

### 收盘趋势样本

路径：`.codex/automation-state/a-share-close-main-add-samples/YYYYMMDD.json`

至少保存：交易日、计划取样时点、实际Wind时间、板块查询完整性、股票代码、简称、来源板块、板块内排名、累计主力净流入原始值、单位、涨跌幅、主力占比。

### 收盘结果

路径：`.codex/automation-state/a-share-close-main-add-top10/YYYYMMDD.json`

至少保存：四板块原始候选、去重结果、综合Top 10、每只股票有效趋势样本、相邻增量、趋势分类、趋势变化额、主力净流入合计、平均收益率、Wind数据时间和数据限制。

### 运行档位状态

路径：`.codex/automation-state/a-share-monitor-run-slots/YYYYMMDD.json`

以计划档位为键，保存各模式的成功状态，至少包含 `planned_time`、`mode`、`completed_at`、`wind_data_time`。同一交易日、计划档位和模式只成功处理一次；只有规定的业务状态文件全部落盘后才写成功状态。失败不得占用档位。
