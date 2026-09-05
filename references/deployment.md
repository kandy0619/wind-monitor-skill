# 部署与跨机器验收

## 边界

本仓库包含全部业务规格、取数桥接、格式适配、自愈SOP、计算、状态机、制卡和交付包装。目标机器只需提供：

- Python 3.11+与Node.js 20+；
- 可用的项目本地或用户级 `wind-mcp-skill`及本机Wind凭据；
- KStock项目、数据库中的启用`MonitorTask.feishu_chat_id`以及本机飞书应用凭据。

Skill不包含、不复制也不输出任何凭据或接收标识。

## 安装

```bash
npx skills add https://github.com/kandy0619/wind-monitor-skill.git --skill wind-monitor-skill -g -y
```

在KStock项目中优先安装Wind技能，以确保项目凭据边界：

```bash
npx skills add Wind-Information-Co-Ltd/wind-skills --skill wind-mcp-skill -y
```

## 自动化入口

定时任务只负责30个固定工作日触发时点，并使用薄提示词：

```text
在KStock项目执行一次$wind-monitor-skill轮询；全部业务规则、状态、取数、自愈、报告和双渠道交付严格使用已安装Skill当前版本。
```

不要在任务提示词复制股票清单、时间表、字段、卡片结构、收盘逻辑或接收目标。规则升级只通过Skill版本完成。

## 运行组件

1. `monitor_runtime.py poll`：按Asia/Shanghai解析当前档位，并优先返回pending任务。
2. `wind_cli_client.py`：发现项目本地Wind技能并用唯一临时请求文件调用。
3. `wind_response_adapter.py`：保存脱敏原始回包并输出稳定Schema；失败进入自愈SOP。
4. `calculate_monitor.py`：执行盘中、趋势、行业5日和个股5日计算。
5. `build_close_report.py`：把15:10所有组件合并为一个稳定报告ID。
6. `render_feishu_card.py`：只从规范化JSON生成卡片。
7. `deliver_report.py`：验证卡片并调用 `kstock_feishu_delivery.py`，逐片持久化交付结果。
8. `stage_rendered_cards.py`：把已由官方渲染器生成并验证的卡片原样绑定到交付包，确保发送内容不被二次改写。

## 验收清单

- 运行 `python -m compileall -q scripts`。
- 运行 `python -m unittest discover -s tests -v`。
- 验证09:30、午休、15:00、15:10、周末和UTC输入的路由fixture。
- 使用脱敏fixture验证旧Wind信封、新字段名、列式数组、未知单位、空结果和100行截断。
- 在测试数据库中验证优先任务、单一群聊、歧义失败和日志脱敏。
- 沙箱验证导入、文件访问、进程和网络代码均被拒绝。
- 集成环境试发卡时确认15:00一张盘中卡，15:10两张同报告ID收盘卡；模拟第二张失败后只重试第二张。
- 不在无授权环境发真实飞书消息或消耗Wind额度。

## 历史分片离线重放

使用独立输出目录重放，不读取当前时钟、不写生产状态、不调用Wind或飞书：

```bash
python scripts/replay_historical.py --kstock-root <KStock目录> --trade-date 2026-08-13 --output-root <隔离输出目录>
```

旧历史分片若缺少新版收盘必需的行业5日或个股5日数据，重放结果必须是 `pending_missing_historical_components`；这表示旧数据不足，不能用插值、空值补0或测试fixture把它伪装成完整收盘报告。

经明确授权进行真实历史演练时，使用独立目录补取缺失组件，不写生产档位完成状态：

```bash
python scripts/collect_historical_industry.py --dates <5个交易日> --project-root <KStock目录> --artifact-root <隔离目录> --output industry-days.json
python scripts/collect_historical_stock.py --dates <5个交易日> --project-root <KStock目录> --artifact-root <隔离目录> --output stock-days.json
python scripts/calculate_monitor.py industry-5d --input industry-days.json --output industry-5d.json
python scripts/calculate_monitor.py stock-5d --input stock-days.json --output stock-5d.json
```

构建报告时传入 `--simulation-label "历史演练 YYYY-MM-DD"`。两张卡必须分别通过 `render_feishu_card.py --part 1/2` 生成，再用 `stage_rendered_cards.py` 原样装入报告包；只有用户明确授权后才调用 `deliver_report.py`。历史演练报告不得更新或占用对应历史生产档位。
