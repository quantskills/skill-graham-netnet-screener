---
name: alpha-ncav-graham-production
description: NCAV 折价因子生产环境数据说明。提供已计算好的因子 Parquet 文件，agent 直接读取使用。包含文件路径、字段定义、主键、读取规则和禁止行为。
tags: [quant, alpha, production, stock]
---

# NCAV 折价因子 —— 生产数据

## 文件路径

```
生产产物/
└── 数据库.parquet    ← 因子结果文件
```

相对路径：`./生产产物/数据库.parquet`

## 主键

| 字段 | 说明 |
|------|------|
| trade_date | 筛选基准日（YYYYMMDD） |
| factor_id | 因子标识，固定值 "ncav_graham" |
| ts_code | 股票代码 |

**主键约束**：(trade_date, factor_id, ts_code) 组合唯一，无重复。

## 完整字段说明

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| trade_date | str | ✅ | 筛选基准日 YYYYMMDD |
| asset_type | str | ✅ | 固定值 "stock" |
| ts_code | str | ✅ | 股票代码（如 000001.SZ） |
| factor_id | str | ✅ | 固定值 "ncav_graham" |
| factor_name | str | ✅ | 固定值 "NCAV折价因子" |
| factor_value | float | ✅ | 1 − (市值/NCAV)，越大越低估 |
| score | float | ✅ | 截面 rank 百分位，范围 [0, 100] |
| rank | int | ✅ | 截面排名（1 = 最低估） |
| signal | str | ✅ | 枚举值：buy / hold / sell |
| confidence | float | ✅ | 信号置信度 [0, 1] |
| data_version | str | ✅ | 数据版本号 YYYYMMDD_HHMMSS |
| update_time | str | ✅ | 生成时间 ISO 8601 |
| ncav_ratio | float | 附加 | 市值 / NCAV |
| pb | float | 附加 | 市净率 |
| profit_years_5 | float | 附加 | 近5年盈利年数 |
| audit_opinion | str | 附加 | 审计意见类型 |
| guarantee_ratio | float | 附加 | 担保金额 / 净资产 |

## 读取规则

### 推荐读取方式

```python
import pyarrow.parquet as pq
import pandas as pd

# 读取全部数据
table = pq.read_table("生产产物/数据库.parquet")
df = table.to_pandas()

# 只取最新一期
latest_date = df["trade_date"].max()
latest = df[df["trade_date"] == latest_date]

# 只取 buy 信号
buy_signals = latest[latest["signal"] == "buy"]
```

### 有效数据选取

- 默认取 **最新 trade_date** 的全部数据
- 若需历史截面，按 trade_date 过滤
- 同一 trade_date 下 factor_id = "ncav_graham" 的数据为有效因子数据

### signal 含义

| signal | 含义 | 建议操作 |
|--------|------|----------|
| buy | 市值 < NCAV × 66%，P/B < 1，通过价值陷阱过滤 | 重点关注 |
| hold | 市值 < NCAV，但不满足 buy 全部条件 | 观察 |
| sell | 其余 | 不关注 |

## 数据质量保障

本数据已通过以下检查：

1. ✅ 主键 (trade_date, factor_id, ts_code) 唯一
2. ✅ 必填字段完整（12 个标准字段）
3. ✅ factor_value 无 NaN
4. ✅ score 范围 [0, 100]
5. ✅ signal 仅含 buy / hold / sell 枚举值
6. ✅ data_version 格式正确
7. ✅ 通过未来函数检测（validate.py 第一层）
8. ✅ 通过样本外检测（validate.py 第三层）

## 禁止行为

1. **禁止修改 Parquet 文件**：因子结果由 factor.py 生成，不得手动编辑
2. **禁止修改字段含义**：factor_value / score / signal 定义见上方字段说明
3. **禁止忽略 data_version**：每次重新计算 data_version 会更新，需使用最新版本
4. **禁止混用不同 trade_date 的截面**：不同基准日的数据不可混合计算
5. **禁止将 signal 作为唯一决策依据**：NCAV 筛选为初筛，需结合定性分析
6. **禁止在未通过 validate.py 验证的情况下使用数据**：数据必须通过全部检测

## 数据更新

### 重新计算

```bash
# 重新计算因子（默认 as_of_date = 当日）
python scripts/factor.py

# 指定基准日
python scripts/factor.py --as-of-date 20250430

# 放宽阈值
python scripts/factor.py --threshold 0.80
```

### 验证数据

```bash
# 运行三层沙漏验证
python scripts/validate.py

# 运行回测
python scripts/backtest.py
```

## 数据来源

所有数据来自 PandaAI 量化数据平台（`panda_data` SDK），详见：
- 开发版 SKILL.md：因子逻辑与计算公式
- data_guide.md：数据接口与字段映射
- factor.py：计算逻辑与时点对齐规则
