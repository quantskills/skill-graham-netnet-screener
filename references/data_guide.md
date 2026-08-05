# PandaAI Data 实现指南

## 数据源声明

本因子所有数据均来自 **PandaAI 量化数据平台 Python SDK**（`panda_data`），不使用任何来源不明、字段不稳定或个人临时整理的数据文件。

## 环境配置

### 安装

```bash
pip install panda_data pyarrow pandas numpy
```

### 环境变量

| 变量名 | 说明 | 示例 |
|--------|------|------|
| `PANDA_USERNAME` | PandaAI 用户名 | `your_username` |
| `PANDA_PASSWORD` | PandaAI 密码 | `your_password` |
| `PANDA_BASE_URL` | PandaAI 服务地址 | `https://api.pandaai.com` |

> **安全要求**：禁止将账号密码硬编码到代码或配置文件中。请通过环境变量或 `.env` 文件配置。

### 初始化

```python
import os
import panda_data

panda_data.init_token(
    os.environ["PANDA_USERNAME"],
    os.environ["PANDA_PASSWORD"],
    os.environ["PANDA_BASE_URL"],
)
```

## 接口清单

### 1. get_industry_constituents()

**功能**：获取申万一级行业成分股列表

**调用**：
```python
df = panda_data.get_industry_constituents()
```

**返回字段**（Parquet bytes → pyarrow 解析）：

| 字段 | 类型 | 说明 |
|------|------|------|
| symbol | str | 股票代码 |
| name | str | 股票名称 |
| industry | str | 申万一级行业分类 |
| is_st | bool | 是否 ST |

**用途**：
- 剔除银行、房地产、非银金融行业
- 剔除 ST / *ST 股票

**限制**：无明确限制

---

### 2. get_stock_daily(symbols, start_date, end_date)

**功能**：获取个股日线行情数据

**调用**：
```python
data = panda_data.get_stock_daily(
    symbols=["000001.SZ", "000002.SZ", ...],
    start_date="20250101",
    end_date="20250430",
)
# data 为 Parquet bytes
import pyarrow.parquet as pq
import io
table = pq.read_table(io.BytesIO(data))
df = table.to_pandas()
```

**参数**：

| 参数 | 类型 | 说明 | 限制 |
|------|------|------|------|
| symbols | list[str] | 股票代码列表 | **每次最多 300 只**，需分批调用 |
| start_date | str | 起始日期 | 格式 YYYYMMDD |
| end_date | str | 结束日期 | 格式 YYYYMMDD |

**返回字段**（Parquet bytes → pyarrow 解析）：

| 字段 | 类型 | 说明 |
|------|------|------|
| symbol | str | 股票代码 |
| date | str | 交易日期 YYYYMMDD |
| open | float | 开盘价 |
| high | float | 最高价 |
| low | float | 最低价 |
| close | float | 收盘价 |
| volume | float | 成交量 |
| amount | float | 成交额 |

**用途**：
- 获取 as_of_date 当日或最近交易日收盘价
- 计算市值 = close × total_shares

**限制**：
- 每次最多 300 只股票
- 日期格式必须为 YYYYMMDD
- 返回 Parquet bytes，需用 pyarrow 解析

---

### 3. 财务数据接口（待确认）

> **⚠️ 重要**：以下接口为预期设计，需与 PandaAI 平台确认实际函数名和返回字段。
> 若 SDK 暂不支持财务数据，需使用替代数据源（见下方"替代方案"）。

#### 3a. 资产负债表（预期）

**预期调用**：
```python
# 函数名需确认
bs = panda_data.get_balance_sheet(
    ts_codes=["000001.SZ", ...],
    as_of_date="20250430",  # SDK 内部处理披露时点对齐
    fields=[
        "ts_code", "report_date",
        "total_current_assets",     # 流动资产合计
        "total_liabilities",        # 负债合计
        "minority_interest",        # 少数股东权益
        "preferred_stock",          # 优先股（A股极少）
        "total_shares",             # 总股本
        "total_equity",             # 净资产（股东权益合计）
    ]
)
```

**NCAV 字段映射**：

| 计算项 | 字段名 | 来源 | 说明 |
|--------|--------|------|------|
| 流动资产合计 | `total_current_assets` | 资产负债表 | 包含货币资金、应收账款、存货等 |
| 负债合计 | `total_liabilities` | 资产负债表 | 流动负债 + 非流动负债 |
| 少数股东权益 | `minority_interest` | 资产负债表 | 中国准则下列在所有者权益侧，需单独扣除 |
| 优先股 | `preferred_stock` | 资产负债表 | A股极少，通常为 0 |
| 总股本 | `total_shares` | 资产负债表 | 用于计算每股 NCAV |
| 净资产 | `total_equity` | 资产负债表 | 归属于母公司股东权益 |

#### 3b. 利润表（预期）

**预期调用**：
```python
# 函数名需确认
income = panda_data.get_income_statement(
    ts_codes=["000001.SZ", ...],
    years=5,  # 近5年
    fields=["ts_code", "report_date", "net_profit"]
)
```

| 字段 | 说明 | 用途 |
|------|------|------|
| `net_profit` | 净利润 | 近5年盈利验证（至少3年盈利） |

#### 3c. 现金流量表（预期）

**预期调用**：
```python
# 函数名需确认
cf = panda_data.get_cashflow_statement(
    ts_codes=["000001.SZ", ...],
    years=3,
    fields=["ts_code", "report_date", "net_cashflow_from_operations"]
)
```

| 字段 | 说明 | 用途 |
|------|------|------|
| `net_cashflow_from_operations` | 经营活动现金流净额 | 近3年均为负则标记警告 |

#### 3d. 年报附注（预期）

| 字段 | 说明 | 用途 |
|------|------|------|
| `audit_opinion` | 审计意见类型 | 非"标准无保留意见"→剔除 |
| `guarantee_amount` | 对外担保金额 | 担保/净资产 > 50% → 剔除 |

> **⚠️ 字段核实结论（2026-07-14 SDK 实地探测）**：`panda_data` v0.0.9 的 `get_fina_reports()` **不返回**任何对外担保字段（`guarantee_amount` / `guarantee` / `担保金额` / `对外担保` 均未探测到）。
> 因此 `factor.py` 中 `guarantee_amount` 记为 `NaN`，担保风险过滤将被跳过并打印明确提示，**不会**用 0 值静默旁路。
> 如需启用担保过滤，需接入独立数据源（如年报附注或第三方担保数据），并补充对应字段。

---

## 替代数据源方案

若 `panda_data` 暂不支持财务数据接口，以下为替代方案：

### 方案 A：本地数据库

```python
# 示例：从本地 SQLite/PostgreSQL 读取
import sqlite3
conn = sqlite3.connect("local_financial.db")
bs = pd.read_sql("SELECT * FROM balance_sheet WHERE report_date <= ?", conn, params=[as_of_date])
```

需确认本地数据库表名和字段映射：

| 本地表名 | 对应字段 |
|----------|----------|
| `balance_sheet` | ts_code, report_date, total_current_assets, total_liabilities, minority_interest, preferred_stock, total_shares, total_equity |
| `income_statement` | ts_code, report_date, net_profit |
| `cashflow_statement` | ts_code, report_date, net_cashflow_from_operations |
| `audit_report` | ts_code, report_date, audit_opinion |
| `guarantee_info` | ts_code, report_date, guarantee_amount |

### 方案 B：第三方数据源

若使用 Tushare / AkShare / Wind 等，需确保：
1. 字段名与上述映射一致
2. 披露时点对齐逻辑正确
3. 数据源稳定性有保障

> **注意**：使用非 PandaAI 数据源时，需在代码中明确标注数据来源，并在 validate.py 中增加数据源一致性检查。

---

## 数据口径说明

### 时点对齐规则（as_of_date）

| 数据类型 | 取值规则 | 说明 |
|----------|----------|------|
| 行情（收盘价） | as_of_date 当日或最近交易日 | 避免使用未来价格 |
| 年报财务数据 | as_of_date 当日已披露的最近一期 | 年报最晚次年4月底披露 |
| 季报财务数据 | 同上 | Q1≤4月底, 半年报≤8月底, Q3≤10月底 |
| 行业分类 | as_of_date 当日状态 | 行业可能变更 |
| ST 标记 | as_of_date 当日状态 | ST 可能摘帽或新增 |

**关键风险**：
- 若 as_of_date = 20250315（3月15日），当年年报（2024年报）尚未披露（最晚4月底），应使用 2023 年报
- 若 as_of_date = 20250515（5月15日），2024 年报可能已部分披露，取该股票已披露的最新年报

### 交易日历

- 数据来源：`get_stock_daily` 返回的 date 字段隐含交易日历
- 非交易日无数据，取最近交易日即可

### 复权方式

- 本因子使用**不复权收盘价**
- 原因：NCAV 基于总股本计算市值，不复权价格 × 总股本 = 当前市值
- 若使用复权价格，需同步调整总股本

### 停牌处理

- 停牌股在 as_of_date 无交易数据，在 Step 1 中已剔除
- 长期停牌（> 20 个交易日）的股票不应进入股票池

### 接口限制汇总

| 接口 | 限制 | 处理方式 |
|------|------|----------|
| `get_stock_daily` | 每次 ≤ 300 只 | 分批调用，每批 300 |
| 日期格式 | 必须 YYYYMMDD | 统一格式化 |
| 返回格式 | Parquet bytes | pyarrow 解析 |
| 财务数据 | 待确认 | 见上方"待确认"说明 |
