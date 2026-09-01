---
name: graham-netnet-screener
description: 当需要开发、计算、验证 Graham 净净营运资本(NCAV) 因子时，使用此 skill。适用于 A 股全市场深度价值筛选，排除银行/房地产/非银金融，计算 NCAV 折价因子并生成 buy/sell/hold 信号。
tags: [quant, alpha, development, stock]
---

# NCAV 折价 Alpha（Graham 净流动资产模型）

## 适用场景

1. 用户需要计算或验证基于 Graham NCAV 的深度价值因子
2. 用户需要筛选 A 股中市值低于净流动资产的极端低估个股
3. 用户提到格雷厄姆、净流动资产、NCAV、烟蒂股、深度价值筛选

## 因子逻辑

### 核心假设

Graham 在《证券分析》中提出：当个股市值低于其净流动资产(NCAV)的 2/3 时，相当于以六折买入"净流动资产白送全部固定资产"，属于极端低估。NCAV 折价因子衡量的是"清算价值相对于市值的安全边际"。

### 计算公式

```
NCAV = 流动资产合计 − 负债合计 − 少数股东权益 − 优先股（如有）
NCAV_per_share = NCAV / 总股本
ncav_ratio = 市值 / NCAV          ← <1 表示市值低于净流动资产，即低估
factor_value = 1 − ncav_ratio     ← 越大越低估
```

> 中国会计准则下，少数股东权益列在所有者权益侧而非负债侧，必须单独扣除，否则 NCAV 系统性高估导致假阳性。

### 排序方向

factor_value 越大 → 越低估 → 信号越强（升序排列，值大优先）

### 适用市场

- A 股全市场（剔除银行、房地产、非银金融）
- 不适用于金融类公司（资产负债结构特殊，NCAV 概念失真）

## 输入数据

| 字段 | 来源 | 说明 |
|------|------|------|
| trade_date | `get_stock_daily` 返回 | 筛选基准日（as_of_date） |
| ts_code | `get_stock_daily` 返回 | 股票代码 |
| close | `get_stock_daily` 返回 | 收盘价（用于计算市值） |
| total_shares | 财务数据接口 | 总股本 |
| total_current_assets | 资产负债表 | 流动资产合计 |
| total_liabilities | 资产负债表 | 负债合计 |
| minority_interest | 资产负债表 | 少数股东权益 |
| preferred_stock | 资产负债表 | 优先股（A股极少，通常为0） |
| total_equity | 资产负债表 | 净资产（用于 P/B） |
| audit_opinion | 年报附注 | 审计意见类型 |
| guarantee_amount | 年报附注 | 对外担保金额 |
| net_profit | 利润表 | 净利润（近5年，用于持续盈利验证） |
| net_cashflow_from_operations | 现金流量表 | 经营现金流（近3年，价值陷阱标记） |

### 时点对齐（as_of_date）

- **行情数据**：取 as_of_date 当日或最近交易日的收盘价
- **财务数据**：取 as_of_date 当日已披露的最近一期年报/季报（严禁使用尚未披露的财报，避免前视偏差）
- **行业分类 / ST 标记**：取 as_of_date 当日状态
- A股年报最晚次年4月底披露，若 as_of_date 在4月前，最近完整年报应为上上年

### PandaAI data 实现

详见 [data_guide.md](references/data_guide.md)

## 输出结果

### 标准 Parquet 字段

| 字段 | 类型 | 说明 |
|------|------|------|
| trade_date | str | 筛选基准日 YYYYMMDD |
| asset_type | str | 固定值 "stock" |
| ts_code | str | 股票代码 |
| factor_id | str | 固定值 "ncav_graham" |
| factor_name | str | 固定值 "NCAV折价因子" |
| factor_value | float | 1 − (市值/NCAV)，越大越低估 |
| score | float | 截面 rank 百分位，0-100 |
| rank | int | 截面排名（升序，rank=1 最低估） |
| signal | str | 枚举值：buy / hold / sell |
| confidence | float | 信号置信度（0-1） |
| data_version | str | 数据版本号 YYYYMMDD_HHMMSS |
| update_time | str | 生成时间 ISO 8601 |

### signal 生成规则

- `buy`：ncav_ratio < ncav_threshold（默认 0.66）且 P/B < 1 且通过价值陷阱过滤
- `hold`：ncav_ratio < 1.0 但不满足 buy 全部条件
- `sell`：其余

### 附加输出字段（筛选明细）

| 字段 | 说明 |
|------|------|
| ncav_ratio | 市值 / NCAV |
| pb | 市净率 |
| profit_years_5 | 近5年盈利年数 |
| audit_opinion | 审计意见 |
| guarantee_ratio | 担保金额 / 净资产 |

## 使用方式

### 认证方式

使用此 skill 需要 PandaAI 账号权限，支持以下四种认证方式（优先级从高到低）：

**方式一：命令行参数（推荐用于脚本自动化）**
```bash
python scripts/factor.py --username '86手机号' --password '密码'
python scripts/validate.py --username '86手机号' --password '密码'
python scripts/backtest.py --username '86手机号' --password '密码'
```

**方式二：环境变量**
```bash
# Linux/Mac
export PANDA_USERNAME='86手机号'
export PANDA_PASSWORD='密码'
python scripts/factor.py

# Windows PowerShell
$env:PANDA_USERNAME='86手机号'
$env:PANDA_PASSWORD='密码'
python scripts/factor.py
```

**方式三：.env 文件**
在项目根目录创建 `.env` 文件：
```
PANDA_USERNAME=86手机号
PANDA_PASSWORD=密码
PANDA_BASE_URL=http://pandadata.pandaaiquant.com
```

**方式四：交互式输入（默认）**
运行脚本时未提供认证信息，将自动提示输入：
```bash
python scripts/factor.py
# 请输入 PandaAI 用户名（86手机号）: 
# 请输入 PandaAI 密码:
```

### 禁用交互式输入
```bash
python scripts/factor.py --no-interactive
```
使用此参数后，若认证信息缺失将直接报错，适用于 CI/CD 环境。

### 常用命令

```bash
# 1. 计算因子（默认 as_of_date 为当日）
python scripts/factor.py

# 指定 as_of_date 和阈值
python scripts/factor.py --as-of-date 20250430 --threshold 0.80

# 2. 验证因子（未来函数/过拟合/样本外三层检测）
python scripts/validate.py

# 3. 回测因子（IC/ICIR/分层收益/回撤/换手）
python scripts/backtest.py --period 20
```

## 验收要求

以下六项全部通过才可进入生产：

1. **未来函数检测通过**：shift 对齐验证，因子计算不使用任何未来数据（行情/公告/财务）
2. **样本外检测通过**：留出最近 12 个月作样本外，IC 衰减不超过 50%
3. **回测指标达标**：|IC| > 0.03，|ICIR| > 0.5，多空收益单调
4. **PandaAI data 数据源确认**：所有数据来自 panda_data SDK，无来源不明数据
5. **Parquet 质量检查通过**：主键唯一、字段完整、score 范围 0-100、signal 枚举正确
6. **验证脚本输出 PASS**：validate.py 所有检测项输出 ✅ PASS
