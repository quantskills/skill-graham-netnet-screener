#格雷厄姆 NCAV 折价 Alpha / Graham NCAV (Net Current Asset Value) Alpha

## 项目概述 / Overview

基于本杰明·格雷厄姆《证券分析》中的烟蒂股筛选逻辑，计算 A 股全市场 NCAV 折价因子，
筛选市值低于净流动资产的极端低估个股，生成 buy/sell/hold 信号。

Deep-value screening factor based on Benjamin Graham's "Security Analysis" cigar-butt logic.
Calculates NCAV discount for the full A-share market, identifying extremely undervalued stocks
whose market cap falls below net current asset value, and generates buy/sell/hold signals.

## 因子逻辑 / Factor Logic

```
NCAV = 流动资产合计 − 负债合计 − 少数股东权益 − 优先股
ncav_ratio = 市值 / NCAV          # <1 表示低估
factor_value = 1 − ncav_ratio     # 越大越低估
```

- `factor_value` 越大 → 越低估 → 信号越强
- Higher `factor_value` → more undervalued → stronger signal

## 适用市场 / Applicable Market

- A 股全市场（剔除银行、房地产、非银金融）
- A-share full market (excluding banks, real estate, non-bank financials)
- 不适用于金融类公司（资产负债结构特殊）
- Not applicable to financial companies (balance sheet structure distorts NCAV)

## 关键文件 / Key Files

| 文件 | 说明 |
|------|------|
| `skill/SKILL.md` | 技能定义 / Skill definition |
| `skill/README.md` | 原始 README / Original README |
| `skill/scripts/factor.py` | 因子计算 / Factor calculation |
| `skill/scripts/validate.py` | 三层验证 / Three-layer validation |
| `skill/scripts/backtest.py` | IC/ICIR 回测 / Backtest |
| `skill/生产产物/数据库.parquet` | 因子输出 / Factor output |
| `开发产物/` | 开发产物副本 / Development artifacts copy |

## 信号规则 / Signal Rules

- `buy`：ncav_ratio < 0.66 且 P/B < 1 且通过价值陷阱过滤
- `buy`: ncav_ratio < 0.66 AND P/B < 1 AND passes value-trap filter
- `hold`：ncav_ratio < 1.0 但不满足 buy 全部条件
- `hold`: ncav_ratio < 1.0 but not all buy conditions met
- `sell`：其余 / `sell`: otherwise

## 附加筛选字段 / Additional Screening Fields

| 字段 | 说明 |
|------|------|
| ncav_ratio | 市值/NCAV |
| pb | 市净率 / P/B ratio |
| profit_years_5 | 近5年盈利年数 / Profitable years in last 5 |
| audit_opinion | 审计意见 / Audit opinion |
| guarantee_ratio | 担保/净资产 / Guarantee/equity |

## 快速开始 / Quick Start

```bash
pip install panda_data pandas numpy pyarrow
# 认证四选一 / Authentication (one of four methods)
export PANDA_USERNAME='86手机号'
export PANDA_PASSWORD='密码'

python scripts/factor.py --as-of-date 20250430 --threshold 0.80
python scripts/validate.py
python scripts/backtest.py --period 20
```

## 验收标准 / Acceptance Criteria

1. 未来函数检测通过 · No look-ahead bias
2. 样本外 IC 衰减 < 50% · Out-of-sample IC decay < 50%
3. |IC| > 0.03，|ICIR| > 0.5 · IC/ICIR thresholds met
4. PandaData 数据源确认 · PandaData source confirmed
5. Parquet 质量检查通过 · Parquet quality check passed
6. validate.py 输出 PASS · validate.py outputs PASS

## 依赖 / Dependencies

- Python 3.x
- panda_data >= 0.0.9
- pandas, numpy, pyarrow
