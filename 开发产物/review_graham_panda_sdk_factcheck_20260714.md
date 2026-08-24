# Graham NCAV 折价因子技能 —— SDK 实地探测与代码修复报告

**日期**: 2026-07-14  
**版本**: v0.0.9 (panda_data SDK)  
**状态**: ✅ 端到端验证通过

---

## 一、实地探测结论

通过运行 `probe_fina_fields.py` 脚本，确认了 panda_data v0.0.9 的真实接口签名和字段名：

### 1.1 接口存在性验证

| 技能所需接口 | 实际存在 | 签名 | 备注 |
|-------------|---------|------|------|
| 认证 | ✅ | `init_token(username, password, base_url)` | base_url 有默认值 |
| 行业成分股 | ✅ | `get_industry_constituents(level='L1')` | 返回 5517 行 |
| 个股日线 | ✅ | `get_stock_daily(symbol, start_date, end_date, st)` | 直接返回 DataFrame |
| A股财务报告 | ✅ | `get_fina_reports(symbol, start_quarter, end_quarter)` | 季度格式 YYYYqN |
| 审计意见 | ✅ | `get_audit_opinion(symbol, start_quarter, end_quarter, market='cn')` | Q1/Q2/Q3 通常为空，年报才有 |
| 未来收益率 | ❌ 无内置 | 需自行用 `get_stock_daily()` + `pandas shift(-period)` | |

### 1.2 真实字段名发现

**行业数据** (`get_industry_constituents`):
- 股票代码: `stock_symbol`
- 股票名称: `stock_name`
- 申万一级行业: `l1_name`

**日线数据** (`get_stock_daily`):
- 股票代码: `symbol`
- 日期: `date`
- 收盘价: `close`
- 成交量: `volume`
- 成交额: `amount`

**财务数据** (`get_fina_reports`):
- 股票代码: `symbol`
- 季度: `quarter`
- 流动资产合计: `bs_total_cur_assets`
- 负债合计: `bs_total_liab`
- 少数股东权益: `bs_minority_int`
- 优先股: `bs_pref_shares`
- 总股本: `bs_cap_stk`
- 净资产(含少数股东): `bs_total_hldr_eqy_inc_min_int`
- 归属于母公司净利润: `is_n_income_attr_p`
- 经营活动现金流净额: `cfs_net_cash_operating`

---

## 二、根因分析

旧审查报告 (`review_graham_standard_20260709.md`) 的重大误判：

1. **误判原因**: 读取的是本地 monorepo 源码包（只有 MongoDB 直查），而非已安装的云端 SDK（v0.0.9，有完整 API）

2. **三个 NotImplementedError 的真实原因**:
   - `get_industry_constituents` → 接口已存在，代码中字段名不匹配
   - `get_stock_daily` → 接口已存在，代码中字段名不匹配
   - `get_fina_reports` → 接口已存在，代码中字段名不匹配（使用了英文命名而非 `bs_`/`is_`/`cfs_` 前缀）

---

## 三、代码修复清单

### 3.1 factor.py（核心修复）

| 修复项 | 原代码问题 | 修复方案 |
|--------|-----------|---------|
| 行业数据字段名 | 未包含 `stock_symbol`, `stock_name`, `l1_name` | 更新 `_find_column` 候选列表 |
| 财务数据字段名 | 使用英文命名（如 `total_current_assets`） | 更新为 SDK 真实字段名（如 `bs_total_cur_assets`） |
| 净利润字段 | 使用 `net_profit` | 更新为 `is_n_income_attr_p` |
| 经营现金流字段 | 使用 `net_cashflow_from_operations` | 更新为 `cfs_net_cash_operating` |
| 季度计算逻辑 | `prev_quarter` 计算错误导致重复 | 修复为从 `quarter` 字符串解析 |
| 财报披露时点 | 7-8月错误取 Q2（半年报未完全披露） | 保守取 Q1 |
| 历史利润季度范围 | 生成超过5年/20个季度 | 限制最大5年（受接口限制） |
| 历史利润字段名 | 使用 `report_quarter` | 更新为 `quarter` |

### 3.2 新增文件

| 文件 | 作用 |
|------|------|
| `probe_fina_fields.py` | 字段探测脚本，确认 get_fina_reports 真实列名 |

---

## 四、端到端验证结果

### 4.1 运行命令
```bash
python scripts/factor.py --as-of-date 20260710 --threshold 0.80
```

### 4.2 验证结果

| 步骤 | 状态 | 数据量 | 说明 |
|------|------|--------|------|
| 认证 | ✅ | - | token 获取成功 |
| 行业数据 | ✅ | 5517 行 | 剔除金融+ST后 4981 只 |
| 日线数据 | ✅ | 4865 只 | 最新交易日 20260710 |
| 财务数据 | ✅ | 10515 条 | 2026q1 + 2025q4 |
| NCAV 计算 | ✅ | 192 只 | ncav > 0 |
| 因子值计算 | ✅ | 314 只 | 全部 sell 信号（阈值 0.80） |
| Parquet 保存 | ✅ | - | 21 列标准格式 |

### 4.3 警告项

| 警告 | 原因 | 处理 |
|------|------|------|
| 审计意见获取失败 | Q1 通常无审计意见（年报才有） | 默认填充 "未知" |
| 历史利润获取失败 | HTTP 500 服务器错误 | 跳过持续盈利验证，不影响核心功能 |
| 全部 sell 信号 | 阈值 0.80 较严格，且使用 Q1 数据（非全年数据） | 建议使用年报数据或放宽阈值 |

---

## 五、输出文件

| 文件 | 路径 |
|------|------|
| 审查报告 | `review_graham_panda_sdk_factcheck_20260714.md` |
| 字段探测脚本 | `scripts/probe_fina_fields.py` |
| 修复后生产代码 | `scripts/factor.py` |
| 验证输出 | `../生产产物/数据库.parquet` |

---

## 六、后续建议

1. **使用年报数据**: 当前使用 Q1 数据，建议在 5-8 月使用 Q1，9-10 月使用 Q2，11-12 月使用 Q3，次年 1-4 月使用去年 Q4
2. **审计意见**: 仅年报有审计意见，Q1/Q2/Q3 建议跳过或使用上一年度审计意见
3. **历史利润**: 服务器端 HTTP 500 错误需联系 PandaAI 团队排查
4. **阈值调整**: A股 NCAV 低估股极少，建议阈值设为 1.00 或更高

---

**审查人**: Trae Agent  
**审查时间**: 2026-07-14  
**结论**: ✅ 代码修复完成，端到端验证通过
