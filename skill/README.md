# skill-alpha-ncav-graham

Graham NCAV（净流动资产折价）Alpha 因子技能。

基于 Graham《证券分析》中的烟蒂股筛选逻辑，计算 A 股全市场 NCAV 折价因子，生成 buy/sell/hold 信号。

## 因子逻辑

```
NCAV = 流动资产合计 − 负债合计 − 少数股东权益 − 优先股
ncav_ratio = 市值 / NCAV          ← <1 表示低估
factor_value = 1 − ncav_ratio      ← 越大越低估
```

适用：A股全市场（剔除银行/房地产/非银金融）。

## 目录结构

```
skill-alpha-ncav-graham/
├── SKILL.md                          # 技能定义文档
├── README.md                          # 本文件
├── scripts/
│   ├── factor.py                     # 核心因子计算
│   ├── validate.py                   # 三层沙漏验证
│   ├── backtest.py                   # IC/ICIR/分层回测
│   └── probe_fina_fields.py          # SDK 字段探测
├── references/
│   └── data_guide.md                 # 数据接口说明
└── 生产产物/
    └── 数据库.parquet                # 因子输出示例
```

## 快速开始

```bash
# 认证（四选一）
export PANDA_USERNAME='86手机号'
export PANDA_PASSWORD='密码'

# 计算因子
python scripts/factor.py --as-of-date 20250430 --threshold 0.80

# 三层验证
python scripts/validate.py

# IC 回测
python scripts/backtest.py --period 20
```

详细用法见 `SKILL.md`。

## 依赖

- Python 3.x
- panda_data >= 0.0.9
- pandas, numpy, pyarrow
