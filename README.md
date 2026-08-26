# wind-monitor-skill

基于万得 Wind 金融数据的 A 股资金流向监控 Skill。它为 Codex 定义盘中 10 分钟报告、固定时点趋势采样、收盘主力榜、飞书互动卡片以及可审计状态文件的完整工作流。

> 本仓库是工作流 Skill，不包含 Wind API Key、飞书凭据或 KStock 运行配置。金融数据由独立的 `wind-mcp-skill` 提供。

## 主要能力

- 每 10 分钟监控三只自选股和四个代表指数的主力资金变化。
- 以独立Wind查询分别取得行业净流入/净流出 Top 5及榜内行业Top 3个股，避免混合粒度导致双榜缺失。
- 在固定时点保存沪市主板、深市主板、创业板和科创板候选样本。
- 15:10 生成“四板块各 Top 5 候选合并榜”收盘 Top 10。
- 收盘报告统计最近5个交易日的行业净额、加减仓天数及累计加减仓金额Top 5。
- 收盘报告增加全A个股对应的近5日六方向Top 5，并用逐日原值本地复算、剔除缺值股票。
- 为飞书生成适合移动端阅读的精简互动卡片。
- 在 Codex 中同时输出精简版和完整审计版。
- 使用 `pending` / `pending_send` 状态持续重试失败档位，任务触发后不因执行延迟而放弃交付。
- 严格区分计划档位、实际执行时间和 Wind 数据时间。

## 运行依赖

- Codex Skills 运行环境
- Node.js 20 或更高版本
- Python 3.11 或更高版本
- 已安装并配置可用的 `wind-mcp-skill`
- KStock 项目；飞书交付依赖：
  - `backend/services/feishu_bot.py`
  - 已启用且配置了 `feishu_chat_id` 的 `MonitorTask`

## 安装

全局安装：

```bash
npx skills add https://github.com/kandy0619/wind-monitor-skill.git --skill wind-monitor-skill -g -y
```

仅安装到当前项目：

```bash
npx skills add https://github.com/kandy0619/wind-monitor-skill.git --skill wind-monitor-skill -y
```

安装后应能在 Codex Skills 列表中看到 `wind-monitor-skill`。

首次运行时，Skill 会检查 `wind-mcp-skill`。如果依赖不存在，会自动执行：

```bash
npx skills add Wind-Information-Co-Ltd/wind-skills --skill wind-mcp-skill -g -y
```

主源因网络问题失败时，会尝试国内镜像：

```bash
npx skills add https://gitee.com/wind_info/wind-skills.git --skill wind-mcp-skill -g -y
```

自动安装只负责部署依赖 Skill，不会写入或上传 Wind Key。使用者仍需按照 `wind-mcp-skill` 的认证指引在本机安全配置自己的 Key。

## 使用方式

可以直接在 Codex 中提出以下类型的请求：

```text
执行一次 wind-monitor-skill，生成当前盘中资金报告。
```

```text
按照 wind-monitor-skill 配置 A 股资金监控定时任务，并将报告发送到飞书。
```

自动化任务应调用 `$wind-monitor-skill`，并以 [SKILL.md](SKILL.md) 和 [监控规格](references/monitor-spec.md) 作为业务口径。报告档只有在 Wind 取数、计算、状态落盘和飞书卡片发送全部成功后才算完成。

## 默认监控范围

| 类型 | 标的 |
| --- | --- |
| 自选股 | 中芯国际、新易盛、胜宏科技 |
| 代表指数 | 上证指数、深证成指、创业板指、科创 50 |
| 行业异动 | Wind 行业净流入 / 净流出 Top 5，各行业 Top 3 个股 |
| 收盘候选 | 沪市主板、深市主板、创业板、科创板各 Top 5 |

详细字段、时间点、计算和展示规则参见 [references/monitor-spec.md](references/monitor-spec.md)。Wind 查询映射参见 [references/wind-query-map.md](references/wind-query-map.md)。

## 定时与失败重试

- 时区固定为 `Asia/Shanghai`。
- 自动化只在工作日30个固定业务档位触发，不使用每5分钟心跳，也不增加额外重试档。
- 固定档位覆盖09:30—11:30和13:00—15:10的报告、采样与收盘任务，具体列表以监控规格为准。
- 固定趋势样本为 10:00、10:30、11:00、11:15、13:30、13:45、14:00、14:30、14:45。
- 收盘报告计划档为 15:10，读取 15:00 附近的收盘数据。
- 不设置“超过若干秒即放弃”的执行宽限。
- 已触发但未完成的档位进入 `pending`；飞书待发送状态使用 `pending_send`，后续触发优先重试。
- 延迟取得的数据必须展示真实 Wind 时间，不能冒充计划时点快照。

## 输出渠道

### 飞书

仅发送精简互动卡片，突出关键资金状态、代表指数、自选股和行业异动。接收群从 KStock 已启用的 `MonitorTask` 动态读取，不在 Skill 或仓库中硬编码群 ID。

### Codex

先展示与飞书信息等价的精简版，再展示包含原始字段、计算过程和必要限制的完整审计版。

## 仓库结构

```text
wind-monitor-skill/
├── SKILL.md
├── agents/
│   └── openai.yaml
├── references/
│   ├── monitor-spec.md
│   └── wind-query-map.md
├── scripts/
│   ├── calculate_monitor.py
│   └── render_feishu_card.py
└── tests/
    ├── test_industry_5d.py
    └── test_stock_5d.py
```

## 安全说明

- 不要向仓库提交 Wind API Key、飞书 App Secret、访问令牌或群 ID。
- 密钥应保存在本地配置、环境变量或安全凭据系统中。
- 状态文件和实际报告可能包含业务数据，应保留在运行项目中，不要提交到本 Skill 仓库。
- 该 Skill 只生成资金监控报告，不提供买卖建议。

## 本地验证

验证 Python 脚本语法：

```bash
python -m py_compile scripts/calculate_monitor.py scripts/render_feishu_card.py
```

验证 Skill 元数据时，可使用 Codex `skill-creator` 提供的 `quick_validate.py`。
