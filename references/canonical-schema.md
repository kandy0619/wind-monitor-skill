# Wind回包规范化Schema

## 目标

Wind原始回包的信封、字段名、表格形态和单位元数据可能变化。监控计算只消费稳定的规范化记录，原始回包始终作为事实源保存。适配器不得猜测缺失单位、把空值转成0或让模型直接生成市场数值。

## 处理顺序

1. `wind_cli_client.py` 调用实际安装的 `wind-mcp-skill`，项目本地安装优先。
2. 对原始成功/错误信封做字段级脱敏并落盘。
3. `wind_response_adapter.py` 解开 `content[0].text`、`data`、`result` 等信封，识别记录数组、嵌套表格块、列+行表格或列式数组；纯文本“没找到数据”必须识别为空结果，不能误判成一条记录。
4. 按profile的语义别名映射字段，并使用Wind返回的单位元数据统一换算到元。
5. 输出规范化记录、字段来源、原始值、单位、适配器版本和警告。
6. 规范化失败进入自愈SOP，不得报告为“Wind未返回数据”。

## Profiles

- `stock`：`code`、`name`、`trade_date`、`data_time`、`change_pct`、`main_yuan`，以及可选机构/大户/中户/散户和主力占比。
- `index`：`code`、`name`、`trade_date`、`data_time`、`main_yuan`和可选主力占比。
- `industry_summary`：行业完整名称、交易日、数据时间、主力净流入额；最多5行。
- `industry_daily_full`：单交易日全部Wind末级行业及主力净流入额；允许单向查询返回100行警告，但只能在与反向查询按行业全名合并后使用。
- `industry_stock`：Wind代码、简称、行业完整名称、交易日、数据时间、主力净流入额、涨跌幅和可选主力占比；最多3行。
- `board_candidate`：Wind代码、简称、交易日、数据时间、主力净流入额、涨跌幅、主力占比和可选板块；最多5行。

所有金额规范字段以 `_yuan` 结尾，比例以 `_pct` 结尾。单位规范化覆盖元、万元、百万元、百万人民币元和亿元；动态日期前缀字段可按业务字段后缀匹配。每个规范字段的 `provenance` 必须能定位到原始字段名、原始值和原始单位。

## 报告载荷信封

Wind profile规范化完成后，组装报告载荷时必须增加显式业务信封；渲染器不从业务字段形状推断报告类型。

- 盘中：`report_type=intraday`，`planned_time`必须属于标准10分钟盘中档（包含15:00），不得包含`top10`、`stock_5d`或收盘`card_mode`。
- 收盘：`report_type=close_summary`，`planned_time=15:10`。第一片`card_mode=close-overview`（可省略），第二片`card_mode=close-stock-5d`。
- 上一档比较载荷也必须是`report_type=intraday`并保留它自己的`planned_time`。
- 契约不一致时分类为`report_contract_mismatch`，进入`pending_render`；不得自动纠正、按形状猜测或发送。

## 错误分类

| 代码 | 含义 | 是否可称Wind无数据 |
| --- | --- | --- |
| `no_data` | Wind成功且业务记录明确为空 | 是 |
| `field_missing` | 有记录但必需字段无法映射 | 否 |
| `shape_mismatch` | 回包存在但表格/记录形态无法识别 | 否 |
| `parse_failed` | 嵌套文本或JSON无法解析 | 否 |
| `unit_ambiguous` | 金额存在但单位不可证明 | 否 |
| `row_count_mismatch` | 返回行数不符合查询契约 | 否 |
| `truncated` | 恰好100行，存在Wind结果截断风险 | 否，必须拆分查询 |

认证、额度、网络和Wind CLI运行时错误保留Wind错误码，不进入字段适配。

## 使用

```bash
python scripts/wind_response_adapter.py --input raw.json --profile stock --output normalized.json --fallback-request fallback.json
```

若模型已给出候选映射：

```bash
python scripts/wind_response_adapter.py --input raw.json --profile stock --candidate candidate.json --output normalized.json
```
