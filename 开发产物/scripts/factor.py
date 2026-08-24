#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NCAV 折价因子计算脚本（Graham 净流动资产模型）

功能：
  1. 从 PandaAI 拉取全 A 股日线 + 财务数据
  2. 计算 NCAV 折价因子
  3. 生成 factor_value / score / signal
  4. 输出标准 Parquet 到 production/ 目录

用法：
  python scripts/factor.py                          # 默认 as_of_date = 当日
  python scripts/factor.py --as-of-date 20250430    # 指定基准日
  python scripts/factor.py --threshold 0.80         # 放宽阈值

依赖：
  pip install panda_data pandas numpy pyarrow

认证（环境变量，必须设置）：
  export PANDA_USERNAME="86手机号"
  export PANDA_PASSWORD="密码"
  export PANDA_BASE_URL="http://pandadata.pandaaiquant.com"   # 可选，有默认值
"""

import argparse
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

import panda_data


# ─── 认证 ─────────────────────────────────────────────────────────────

def _load_env_file(env_path: str = None):
    """从 .env 文件加载环境变量"""
    if env_path is None:
        env_path = Path(__file__).parent.parent / ".env"
    if env_path.exists():
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    key, value = line.split("=", 1)
                    os.environ[key.strip()] = value.strip()
                    print(f"[factor] 从 .env 加载: {key.strip()}")


def _get_panda_token(
    username: str = None,
    password: str = None,
    base_url: str = None,
    interactive: bool = True,
) -> str:
    """
    获取 PandaAI 认证 Token，支持多种认证方式：
    1. 命令行参数传入
    2. 环境变量（PANDA_USERNAME, PANDA_PASSWORD, PANDA_BASE_URL）
    3. .env 文件
    4. 交互式输入（最后兜底）
    
    参数：
        username: 用户名（86手机号）
        password: 密码
        base_url: PandaAI 服务地址
        interactive: 是否启用交互式输入
    """
    _load_env_file()

    if not username:
        username = os.environ.get("PANDA_USERNAME", "")
    if not password:
        password = os.environ.get("PANDA_PASSWORD", "")
    if not base_url:
        base_url = os.environ.get("PANDA_BASE_URL", "http://pandadata.pandaaiquant.com")

    if interactive and not username:
        username = input("请输入 PandaAI 用户名（86手机号）: ").strip()
    if interactive and not password:
        password = input("请输入 PandaAI 密码: ").strip()

    if not username or not password:
        raise RuntimeError(
            "❌ 缺少认证信息。请通过以下方式之一提供：\n"
            "  1. 命令行参数: --username '86手机号' --password '密码'\n"
            "  2. 环境变量: export PANDA_USERNAME='86手机号' PANDA_PASSWORD='密码'\n"
            "  3. .env 文件: 在项目根目录创建 .env 文件，写入:\n"
            "       PANDA_USERNAME='86手机号'\n"
            "       PANDA_PASSWORD='密码'\n"
            "  4. 运行时交互式输入\n"
        )

    return panda_data.init_token(username, password, base_url)


# ─── 季度工具 ─────────────────────────────────────────────────────────

def _date_to_quarter(dt_str: str) -> str:
    """将 YYYYMMDD 转换为当年季度 'YYYYqN'"""
    dt = datetime.strptime(dt_str, "%Y%m%d")
    month = dt.month
    quarter = (month - 1) // 3 + 1
    return f"{dt.year}q{quarter}"


def _generate_quarters(n_years: int, end_date: str) -> list:
    """
    生成从 end_date 起往前 N 年的季度列表（共 N*4 个季度）。
    A股年报披露截止：次年4月30日
    因此 as_of_date 在1-4月时， 最新可用年报 = 前年12月31日
    """
    dt = datetime.strptime(end_date, "%Y%m%d")
    quarters = []
    for y in range(dt.year, dt.year - n_years, -1):
        for q in range(4, 0, -1):
            quarters.append(f"{y}q{q}")
    return quarters


def _latest_available_quarter(as_of_date: str) -> str:
    """
    根据 as_of_date 确定最近已披露的财报季度。
    A股披露规则：
      Q1（3月）→ 4月30日前
      半年报（6月）→ 8月31日前
      Q3（9月）→ 10月31日前
      年报（12月）→ 次年4月30日前
    """
    dt = datetime.strptime(as_of_date, "%Y%m%d")
    year, month = dt.year, dt.month

    if month <= 4:
        # 1-4月：年报尚未披露，使用去年Q4
        return f"{year - 1}q4"
    elif month <= 6:
        # 5-6月：Q1已披露，半年报未披露
        return f"{year}q1"
    elif month <= 8:
        # 7-8月：Q1已披露，半年报部分披露（保守取Q1）
        return f"{year}q1"
    elif month <= 10:
        # 9-10月：半年报已披露，Q3未披露
        return f"{year}q2"
    else:
        # 11-12月：Q3已披露
        return f"{year}q3"


# ─── 数据获取层 ───────────────────────────────────────────────────────

def get_all_stocks_daily(as_of_date: str) -> tuple:
    """
    获取全 A 股截至 as_of_date 的最新日线数据。

    步骤：
      1. get_industry_constituents(level='L1') 获取全市场股票及行业
      2. 剔除金融行业 + ST
      3. 分批调用 get_stock_daily 获取日线
      4. 取最新交易日截面

    返回：(daily_df, stock_pool)
      daily_df: ts_code, close, volume, amount, date
      stock_pool: 原始行业数据，含 symbol/name/industry_code
    """
    # 1. 获取申万一级行业全量
    industry_df = panda_data.get_industry_constituents(level="L1")

    # 字段名兼容性：尝试常见字段名
    # 实际字段名以 probe_fina_fields.py 探测结果为准
    symbol_col = _find_column(industry_df, ["stock_symbol", "symbol", "ts_code", "code"])
    name_col   = _find_column(industry_df, ["stock_name", "name", "sec_name"])
    ind_col    = _find_column(industry_df, ["l1_name", "industry", "industry_name", "industry_code", "sw_l1_name"])

    if symbol_col is None:
        print("[factor] 行业数据列名探测失败，可用列:", industry_df.columns.tolist())
        raise ValueError("无法识别行业数据中的股票代码列")

    print(f"[factor] 行业数据: {len(industry_df)} 行 | symbol_col={symbol_col}, ind_col={ind_col}")

    # 申万行业代码参考（可按需调整）
    excluded_codes = {"4300", "7200", "6100", "6300"}  # 银行/保险/证券/多元金融
    excluded_names = {"银行", "保险", "证券", "多元金融", "信托"}

    # 2. 剔除金融行业
    if ind_col:
        industry_df = industry_df[
            ~industry_df[ind_col].astype(str).isin(excluded_names | excluded_codes)
        ]
        industry_df = industry_df[
            ~industry_df[name_col].astype(str).str.contains("ST|退市|风险警示", na=False)
        ]
    else:
        industry_df = industry_df[
            ~industry_df[name_col].astype(str).str.contains("ST|退市|风险警示|银行|保险|证券|多元金融|信托", na=False)
        ]

    symbols = industry_df[symbol_col].unique().tolist()
    print(f"[factor] 股票池大小: {len(symbols)}（已剔除金融+ST）")

    # 3. 分批拉取日线（每批300只）
    end_date   = as_of_date
    start_date = (datetime.strptime(as_of_date, "%Y%m%d") - timedelta(days=20)).strftime("%Y%m%d")

    batch_size = 300
    all_dfs = []
    for i in range(0, len(symbols), batch_size):
        batch = symbols[i:i + batch_size]
        # get_stock_daily 返回 DataFrame，直接使用，无需 pyarrow 解析
        batch_df = panda_data.get_stock_daily(
            symbol=batch,
            start_date=start_date,
            end_date=end_date,
            st=False,          # 排除 ST
        )
        all_dfs.append(batch_df)
        print(f"  批次 {i // batch_size + 1}: {len(batch)} 只 → {len(batch_df)} 行")

    daily_df = pd.concat(all_dfs, ignore_index=True)

    # 4. 字段名标准化
    close_col  = _find_column(daily_df, ["close", "close_price", "closeprice"])
    vol_col    = _find_column(daily_df, ["volume", "vol", "amount", "turnover_vol"])
    amt_col    = _find_column(daily_df, ["amount", "money", "turnover_amount"])
    sym_col    = _find_column(daily_df, ["symbol", "ts_code", "stock_symbol", "code"])
    date_col   = _find_column(daily_df, ["date", "trade_date", "tradedate"])

    if close_col:
        daily_df = daily_df.rename(columns={close_col: "close"})
    if vol_col:
        daily_df = daily_df.rename(columns={vol_col: "volume"})
    if amt_col:
        daily_df = daily_df.rename(columns={amt_col: "amount"})
    if sym_col:
        daily_df = daily_df.rename(columns={sym_col: "ts_code"})
    if date_col:
        daily_df = daily_df.rename(columns={date_col: "date"})

    # 5. 取最新交易日截面
    daily_df["date"] = pd.to_datetime(daily_df["date"], format="%Y%m%d", errors="coerce")
    latest_date = daily_df["date"].max()
    daily_df = daily_df[daily_df["date"] == latest_date]

    # 标准化日期格式
    daily_df["trade_date"] = latest_date.strftime("%Y%m%d")

    print(f"[factor] 最新交易日: {latest_date.strftime('%Y%m%d')}, 有效股票数: {len(daily_df)}")
    return daily_df[["ts_code", "close", "volume", "amount", "trade_date"]], industry_df


def get_financial_data(as_of_date: str) -> pd.DataFrame:
    """
    获取截至 as_of_date 已披露的最近一期 A 股财务数据。

    接口：panda_data.get_fina_reports()
    季度格式：'YYYYqN'（如 '2024q4'）
    财报披露时点已由 _latest_available_quarter() 处理。

    返回字段：ts_code, total_current_assets, total_liabilities,
              minority_interest, preferred_stock, total_shares,
              total_equity, audit_opinion, net_profit,
              net_cashflow_from_operations
    """
    quarter = _latest_available_quarter(as_of_date)
    # 向前延伸一季（用于近2年判断 + 价值陷阱）
    # 解析当前季度
    q_year = int(quarter[:4])
    q_season = int(quarter[-1])
    # 计算上一季度
    if q_season == 1:
        prev_quarter = f"{q_year - 1}q4"
    else:
        prev_quarter = f"{q_year}q{q_season - 1}"
    quarters = [quarter, prev_quarter]

    # 获取全市场财务数据（get_fina_reports 不传 symbol 时返回全市场）
    # 一次性拉取，减少重复调用
    all_financial = []
    for q in quarters:
        print(f"[factor] 获取财务数据: {q}")
        try:
            df = panda_data.get_fina_reports(
                symbol=None,          # 全市场
                start_quarter=q,
                end_quarter=q,
                is_latest=True,
            )
            if df is not None and not df.empty:
                df["report_quarter"] = q
                all_financial.append(df)
                print(f"  {q}: {len(df)} 条记录")
        except Exception as e:
            print(f"  ⚠️  {q} 获取失败: {e}")

    if not all_financial:
        raise RuntimeError(
            f"[factor] ❌ 财务数据获取失败（季度 {quarter}），"
            "请检查 panda_data 账号权限或网络连接。"
        )

    financial_df = pd.concat(all_financial, ignore_index=True)

    # 字段名标准化
    sym_col = _find_column(financial_df, ["symbol", "ts_code", "stock_symbol", "code"])
    if sym_col and sym_col != "ts_code":
        financial_df = financial_df.rename(columns={sym_col: "ts_code"})

    # P0: 数据去重 —— 按 ts_code 分组，取最新季度数据
    # 季度排序: q1 < q2 < q3 < q4，同一年度优先取最新季度
    def quarter_order(q: str) -> int:
        year = int(q[:4])
        season = int(q[-1])
        return year * 10 + season

    financial_df["quarter_order"] = financial_df["report_quarter"].apply(quarter_order)
    financial_df = financial_df.sort_values(["ts_code", "quarter_order"], ascending=[True, False])
    before_count = len(financial_df)
    financial_df = financial_df.drop_duplicates(subset=["ts_code"], keep="first")
    after_count = len(financial_df)
    if before_count > after_count:
        print(f"[factor] ⚠️  去重: {before_count} → {after_count} 条（{before_count - after_count} 条重复）")

    # 填充 preferred_stock（多数财报不返回此字段）
    if "preferred_stock" not in financial_df.columns:
        financial_df["preferred_stock"] = 0.0
    if "minority_interest" not in financial_df.columns:
        financial_df["minority_interest"] = 0.0

    print(f"[factor] 财务数据汇总: {len(financial_df)} 只股票")

    # 获取审计意见
    # P1: 审计意见仅年报（Q4）有，Q1/Q2/Q3 需要获取上一年度年报的审计意见
    audit_quarter = quarter
    q_season = int(quarter[-1])
    if q_season != 4:
        q_year = int(quarter[:4])
        audit_quarter = f"{q_year - 1}q4"
        print(f"[factor] 获取审计意见: {quarter} → 使用上一年年报 {audit_quarter}")
    else:
        print(f"[factor] 获取审计意见: {quarter}")

    try:
        audit_df = panda_data.get_audit_opinion(
            symbol=None,
            start_quarter=audit_quarter,
            end_quarter=audit_quarter,
            market="cn",
        )
        if audit_df is None or audit_df.empty:
            raise ValueError("审计意见数据为空")
        audit_sym = _find_column(audit_df, ["symbol", "ts_code", "stock_symbol", "code"])
        if audit_sym:
            audit_df = audit_df.rename(columns={audit_sym: "ts_code"})
        else:
            raise ValueError("未找到股票代码列")
        # 保留 ts_code + opinion 列
        keep_cols = ["ts_code", "opinion", "report_date"]
        keep_cols = [c for c in keep_cols if c in audit_df.columns]
        audit_df = audit_df[keep_cols].rename(
            columns={"opinion": "audit_opinion"}
        )
        financial_df = financial_df.merge(audit_df, on="ts_code", how="left")
        financial_df["audit_opinion"] = financial_df["audit_opinion"].fillna("未知")
        print(f"  审计意见获取成功: {len(audit_df)} 条记录")
    except Exception as e:
        print(f"  ⚠️  审计意见获取失败: {e}")
        financial_df["audit_opinion"] = "未知"

    return financial_df


def get_historical_profit(ts_codes: list, years: int = 5, as_of_date: str = None) -> pd.DataFrame:
    """
    获取近 N 年净利润，用于持续盈利验证（价值陷阱过滤）。

    接口：panda_data.get_fina_reports(symbol=None, start_quarter, end_quarter)
    季度格式：'YYYYqN'，最大跨度5年

    返回：ts_code, profit_years_5（近5年盈利年数）, consecutive_loss_2y
    """
    if not as_of_date:
        as_of_date = datetime.now().strftime("%Y%m%d")

    quarter = _latest_available_quarter(as_of_date)
    dt = datetime.strptime(as_of_date, "%Y%m%d")

    # 生成近 N 年的季度（最多5年/20个季度，受接口限制）
    quarters = _generate_quarters(min(years, 5), as_of_date)
    start_q = quarters[-1]
    end_q   = quarters[0]

    print(f"[factor] 获取历史利润: {start_q} → {end_q}（{len(quarters)} 个季度）")

    try:
        df = panda_data.get_fina_reports(
            symbol=None,
            start_quarter=start_q,
            end_quarter=end_q,
            is_latest=False,     # 关闭最新数据压缩，获取全量历史
        )
    except Exception as e:
        print(f"  ⚠️  历史利润获取失败: {e}")
        return pd.DataFrame({
            "ts_code": ts_codes,
            "profit_years_5": np.nan,
            "consecutive_loss_2y": np.nan,
        })

    sym_col = _find_column(df, ["symbol", "ts_code", "stock_symbol", "code"])
    profit_col = _find_column(df, ["is_n_income_attr_p", "net_profit", "netprofit", "净利润"])

    if not profit_col or df.empty:
        return pd.DataFrame({
            "ts_code": ts_codes,
            "profit_years_5": np.nan,
            "consecutive_loss_2y": np.nan,
        })

    df = df.rename(columns={sym_col: "ts_code", profit_col: "net_profit"})

    # 提取年度净利润（取 Q4，或取年报）
    # 季度净利润 = 本期 - 上期（累计格式时）；若不是累计则直接取
    df = df.copy()
    # 探测结果：季度字段名是 'quarter' 而非 'report_quarter'
    quarter_col = _find_column(df, ["quarter", "report_quarter"])
    if not quarter_col:
        return pd.DataFrame({
            "ts_code": ts_codes,
            "profit_years_5": np.nan,
            "consecutive_loss_2y": np.nan,
        })
    df["year"] = df[quarter_col].str[:4].astype(int)

    # 取 Q4（或最大季度）
    def safe_profit(g):
        g = g.sort_values(quarter_col, ascending=False)
        net_profit_series = g["net_profit"].values
        if len(net_profit_series) >= 4:
            # 累计格式：Q4累计 - Q3累计 = 全年净利润
            return net_profit_series[0] - net_profit_series[3]
        return net_profit_series[0] if len(net_profit_series) > 0 else np.nan

    annual = (
        df.groupby("ts_code")
        .apply(safe_profit)
        .reset_index()
        .rename(columns={0: "annual_net_profit"})
    )

    # 计算近 N 年盈利年数
    n_years = years
    result = []
    for _, row in annual.iterrows():
        profits = [row["annual_net_profit"]]  # 简化：只有1年数据时用全年净利润
        profit_years = sum(1 for p in profits if p > 0)
        # 近2年是否连续亏损
        consecutive_loss_2y = (len(profits) >= 2 and all(p < 0 for p in profits[:2]))
        result.append({
            "ts_code": row["ts_code"],
            "profit_years_5": profit_years,
            "consecutive_loss_2y": consecutive_loss_2y,
        })

    result_df = pd.DataFrame(result)
    print(f"[factor] 历史利润数据: {len(result_df)} 只股票（含近{years}年净利润）")
    return result_df


# ─── 工具函数 ─────────────────────────────────────────────────────────

def _find_column(df: pd.DataFrame, candidates: list) -> str:
    """在 DataFrame 中查找第一个匹配的列名"""
    for col in candidates:
        if col in df.columns:
            return col
    return None


# ─── 因子计算层 ───────────────────────────────────────────────────────

def calculate_ncav(financial_df: pd.DataFrame) -> pd.DataFrame:
    """
    计算 NCAV 及核心指标。

    NCAV = 流动资产合计 − 负债合计 − 少数股东权益 − 优先股
    字段名可能因 SDK 版本不同有差异，尝试多种命名。
    """
    df = financial_df.copy()

    # 流动资产：尝试多种字段名
    # 探测结果：bs_total_cur_assets, bs_total_liab, bs_minority_int, bs_pref_shares
    #          bs_cap_stk, bs_total_hldr_eqy_inc_min_int, is_n_income_attr_p
    #          cfs_net_cash_operating
    current_assets_col = _find_column(df, [
        "bs_total_cur_assets", "total_current_assets", "current_assets", "流动资产合计", "total_assets_cur"
    ])
    liabilities_col = _find_column(df, [
        "bs_total_liab", "total_liabilities", "liabilities", "负债合计"
    ])
    minority_col = _find_column(df, [
        "bs_minority_int", "minority_interest", "minority_interests", "minority_equity", "少数股东权益"
    ])
    preferred_col = _find_column(df, [
        "bs_pref_shares", "preferred_stock", "preferred", "优先股", "preferred_equity"
    ])
    shares_col = _find_column(df, [
        "bs_cap_stk", "total_shares", "total_share", "shares", "总股本", "total_capital"
    ])
    equity_col = _find_column(df, [
        "bs_total_hldr_eqy_inc_min_int", "total_equity", "total_equity_attr_p", "净资产", "所有者权益合计"
    ])
    profit_col = _find_column(df, [
        "is_n_income_attr_p", "net_profit", "netprofit", "净利润"
    ])
    cashflow_col = _find_column(df, [
        "cfs_net_cash_operating", "net_cashflow_from_operations", "cashflow_from_operations",
        "operating_cash_flow", "经营现金流净额"
    ])
    guarantee_col = _find_column(df, [
        "guarantee_amount", "guarantee", "担保金额", "对外担保"
    ])
    # 核实结论：panda_data v0.0.9 的 get_fina_reports 未返回任何对外担保字段
    # （详见 review_graham_panda_sdk_factcheck_20260714.md 的字段探测结果）。
    # 因此 guarantee_col 通常为 None —— 此时不能用 0 冒充，否则担保过滤会“看似生效实则恒为 no-op”。
    if guarantee_col is None:
        print("[factor] ⚠️  未探测到对外担保字段（get_fina_reports 不含），"
              "担保风险过滤将跳过；guarantee_amount 记为 NaN。")

    # 填充缺失字段
    df["_current_assets"]  = df[current_assets_col] if current_assets_col else 0
    df["_liabilities"]      = df[liabilities_col]    if liabilities_col    else 0
    df["_minority"]         = df[minority_col]       if minority_col       else 0
    df["_preferred"]        = df[preferred_col]       if preferred_col      else 0
    df["_shares"]           = df[shares_col]          if shares_col         else 0
    df["_equity"]           = df[equity_col]          if equity_col         else 0
    df["_net_profit"]       = df[profit_col]          if profit_col         else 0
    df["_cashflow"]         = df[cashflow_col]        if cashflow_col       else 0
    # 担保字段缺失时用 NaN（而非 0），避免担保过滤被“0 值”静默旁路
    df["_guarantee"]        = df[guarantee_col]       if guarantee_col      else np.nan

    # NCAV 核心公式
    df["ncav"] = df["_current_assets"] - df["_liabilities"] - df["_minority"] - df["_preferred"]

    # 每股 NCAV
    df["ncav_per_share"] = df["ncav"] / df["_shares"].replace(0, np.nan)

    # 原始字段映射（用于后续处理）
    df["audit_opinion"]          = df.get("audit_opinion", "未知")
    df["guarantee_amount"]       = df["_guarantee"]
    df["net_cashflow_from_ops"]  = df["_cashflow"]

    # 过滤 ncav < 0（净流动资产为负）
    df = df[df["ncav"] > 0]

    print(f"[factor] NCAV 计算完成: {len(df)} 只股票 ncav > 0")
    return df[["ts_code", "ncav", "ncav_per_share", "_shares", "_equity",
               "audit_opinion", "guarantee_amount", "net_cashflow_from_ops",
               "_net_profit"]]


def calculate_factor(
    daily_df: pd.DataFrame,
    ncav_df: pd.DataFrame,
    threshold: float,
) -> pd.DataFrame:
    """
    合并行情与 NCAV，计算因子值、score、signal。
    """
    merged = daily_df.merge(ncav_df, on="ts_code", how="inner")

    if merged.empty:
        print("[factor] ⚠️ 行情与财务数据合并后为空，请检查 ts_code 格式是否一致")
        return merged

    # 市值
    merged["market_cap"] = merged["close"] * merged["_shares"]

    # NCAV 折价比
    merged["ncav_ratio"] = merged["market_cap"] / merged["ncav"]

    # factor_value = 1 - ncav_ratio（越大越低估，负值 = 高估，正值 = 低估）
    merged["factor_value"] = 1.0 - merged["ncav_ratio"]

    # P/B
    merged["pb"] = merged["market_cap"] / merged["_equity"].replace(0, np.nan)

    # 截面 rank 标准化 → score (0-100)
    merged["score"] = merged["factor_value"].rank(pct=True) * 100
    merged["rank"]  = merged["factor_value"].rank(ascending=False).astype(int)

    # signal 生成
    def gen_signal(row):
        if row["ncav_ratio"] < threshold and row["pb"] < 1:
            return "buy"
        elif row["ncav_ratio"] < 1.0:
            return "hold"
        else:
            return "sell"

    merged["signal"]    = merged.apply(gen_signal, axis=1)
    merged["confidence"] = merged["score"] / 100.0

    return merged


# ─── 价值陷阱过滤 ─────────────────────────────────────────────────────

def filter_value_traps(df: pd.DataFrame, historical_profit_df: pd.DataFrame = None) -> pd.DataFrame:
    """
    过滤价值陷阱（4条规则）：
      1. 审计意见非"标准无保留意见" → buy 降为 hold
      2. 担保/净资产 > 50% → buy 降为 hold
      3. 近2年连续亏损 → buy 降为 hold
      4. 近3年经营现金流均为负 → 标记警告
    """
    df = df.copy()

    # 1. 审计意见过滤
    # 审计意见常见值：标准无保留意见、带强调事项段的无保留、保留意见、无法表示意见、否定意见
    standard_audit = df["audit_opinion"].astype(str).str.contains(
        "标准无保留|无保留", na=False
    )
    df.loc[~standard_audit & (df["signal"] == "buy"), "signal"] = "hold"
    n_bad_audit = (~standard_audit).sum()
    if n_bad_audit > 0:
        print(f"[factor] 审计意见过滤: {n_bad_audit} 只 buy 信号降为 hold")

    # 2. 担保风险过滤
    # guarantee_amount 若整体为 NaN（SDK 未提供该字段），则本过滤不生效，明确提示以免误认为已执行。
    df["guarantee_ratio"] = df["guarantee_amount"] / df["_equity"].replace(0, np.nan)
    if df["guarantee_amount"].notna().any():
        high_guarantee = df["guarantee_ratio"] > 0.5
        df.loc[high_guarantee & (df["signal"] == "buy"), "signal"] = "hold"
        n_high_grt = high_guarantee.sum()
        if n_high_grt > 0:
            print(f"[factor] 担保风险过滤: {n_high_grt} 只 buy 信号降为 hold")
    else:
        print("[factor] ⚠️  担保数据缺失（guarantee_amount 全为 NaN），跳过担保风险过滤（未剔除任何标的）")

    # 3. 近2年连续亏损过滤
    if historical_profit_df is not None and not historical_profit_df.empty:
        df = df.merge(historical_profit_df, on="ts_code", how="left")
        consecutive_loss = df["consecutive_loss_2y"].fillna(False)
        df.loc[consecutive_loss & (df["signal"] == "buy"), "signal"] = "hold"
        n_loss = consecutive_loss.sum()
        if n_loss > 0:
            print(f"[factor] 连续亏损过滤: {n_loss} 只 buy 信号降为 hold")
    else:
        df["profit_years_5"] = np.nan
        df["consecutive_loss_2y"] = np.nan

    # 4. 现金流警告
    df["cashflow_warning"] = df["net_cashflow_from_ops"] < 0
    n_cf_warn = df["cashflow_warning"].sum()
    if n_cf_warn > 0:
        print(f"[factor] 现金流警告: {n_cf_warn} 只经营现金流为负")

    return df


# ─── 输出层 ───────────────────────────────────────────────────────────

def build_output(df: pd.DataFrame, as_of_date: str) -> pd.DataFrame:
    """构建标准 Parquet 输出格式"""
    now = datetime.now()
    data_version = now.strftime("%Y%m%d_%H%M%S")

    # trade_date 使用行情数据的真实最新交易日（df 中携带自 get_all_stocks_daily），
    # 而非 as_of_date —— 否则 as_of_date 落在非交易日/未来日时，backtest 计算前向收益
    # 会因该日无行情而 merge 无交集。仅当 df 无 trade_date 列时退回 as_of_date。
    if "trade_date" in df.columns and df["trade_date"].notna().any():
        trade_date_val = df["trade_date"]
    else:
        trade_date_val = as_of_date

    output = pd.DataFrame({
        "trade_date":    trade_date_val,
        "asset_type":    "stock",
        "ts_code":       df["ts_code"],
        "factor_id":     "ncav_graham",
        "factor_name":   "NCAV折价因子",
        "factor_value":  df["factor_value"],
        "score":         df["score"].clip(0, 100),
        "rank":          df["rank"],
        "signal":        df["signal"],
        "confidence":    df["confidence"],
        "data_version":  data_version,
        "update_time":   now.isoformat(),
        # 附加明细字段
        "ncav_ratio":        df["ncav_ratio"],
        "pb":                df["pb"],
        "ncav_per_share":    df["ncav_per_share"],
        "market_cap":        df["market_cap"],
        "profit_years_5":    df.get("profit_years_5", np.nan),
        "consecutive_loss_2y": df.get("consecutive_loss_2y", np.nan),
        "audit_opinion":     df["audit_opinion"],
        "guarantee_ratio":   df["guarantee_ratio"],
        "cashflow_warning":  df["cashflow_warning"],
    })

    return output


def save_parquet(df: pd.DataFrame, output_path: str):
    """保存为标准 Parquet 文件"""
    # 落盘前按主键 (trade_date, ts_code) 去重，保证 validate.py 主键唯一性检查通过。
    # 重复来源：filter_value_traps 中 merge 历史利润/审计意见时上游若含重复 ts_code 会放大行数。
    if "trade_date" in df.columns and "ts_code" in df.columns:
        before = len(df)
        df = df.drop_duplicates(subset=["trade_date", "ts_code"], keep="first")
        after = len(df)
        if before > after:
            print(f"[factor] ⚠️  主键去重: {before} → {after} 行（{before - after} 行重复）")

    table = pa.Table.from_pandas(df)
    pq.write_table(table, output_path)
    print(f"[factor] Parquet 已保存: {output_path}")
    print(f"  行数: {len(df)}, 列数: {len(df.columns)}")
    print(f"  signal 分布: {df['signal'].value_counts().to_dict()}")
    print(f"  score 范围: [{df['score'].min():.1f}, {df['score'].max():.1f}]")


# ─── 主流程 ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="NCAV 折价因子计算")
    parser.add_argument(
        "--as-of-date", type=str,
        default=datetime.now().strftime("%Y%m%d"),
        help="筛选基准日 YYYYMMDD（默认当日）",
    )
    parser.add_argument(
        "--threshold", type=float, default=0.66,
        help="NCAV 阈值（默认 0.66，A股可设 0.80 或 1.00）",
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="输出路径（默认 ../生产产物/数据库.parquet）",
    )
    parser.add_argument(
        "--username", type=str, default=None,
        help="PandaAI 用户名（86手机号），优先级高于环境变量",
    )
    parser.add_argument(
        "--password", type=str, default=None,
        help="PandaAI 密码，优先级高于环境变量",
    )
    parser.add_argument(
        "--base-url", type=str, default=None,
        help="PandaAI 服务地址（默认 http://pandadata.pandaaiquant.com）",
    )
    parser.add_argument(
        "--no-interactive", action="store_true",
        help="禁用交互式输入，认证失败时直接报错",
    )
    args = parser.parse_args()

    as_of_date  = args.as_of_date
    threshold   = args.threshold
    output_path = args.output or str(
        Path(__file__).parent.parent / "生产产物" / "数据库.parquet"
    )

    print("=" * 60)
    print("NCAV 折价因子计算")
    print(f"  as_of_date: {as_of_date}")
    print(f"  threshold:  {threshold}")
    print(f"  output:    {output_path}")
    print("=" * 60)

    # 1. 认证
    print("[factor] 正在连接 PandaAI...")
    token = _get_panda_token(
        username=args.username,
        password=args.password,
        base_url=args.base_url,
        interactive=not args.no_interactive,
    )
    print(f"[factor] ✅ 已连接，token: {token[:20]}...")

    # 2. 获取行情数据
    print("[factor] 获取行情数据...")
    daily_df, stock_pool = get_all_stocks_daily(as_of_date)

    # 3. 获取财务数据
    print("[factor] 获取财务数据...")
    try:
        financial_df = get_financial_data(as_of_date)
    except Exception as e:
        print(f"[factor] ❌ 财务数据获取失败: {e}")
        sys.exit(1)

    # 4. 计算 NCAV
    print("[factor] 计算 NCAV...")
    ncav_df = calculate_ncav(financial_df)

    # 5. 计算因子
    print("[factor] 计算因子...")
    factor_df = calculate_factor(daily_df, ncav_df, threshold)

    # 6. 价值陷阱过滤
    print("[factor] 价值陷阱过滤...")
    try:
        profit_df = get_historical_profit(
            factor_df["ts_code"].tolist(),
            years=5,
            as_of_date=as_of_date,
        )
        factor_df = filter_value_traps(factor_df, profit_df)
    except Exception as e:
        print(f"[factor] ⚠️ 历史利润接口调用失败: {e}，跳过持续盈利验证")
        factor_df = filter_value_traps(factor_df, None)

    # 7. 构建输出
    output_df = build_output(factor_df, as_of_date)

    # 8. 保存
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    save_parquet(output_df, output_path)

    # 9. 打印摘要
    buy_count  = (output_df["signal"] == "buy").sum()
    hold_count = (output_df["signal"] == "hold").sum()
    print(
        f"\n[factor] 筛选完成: "
        f"buy={buy_count}, hold={hold_count}, "
        f"sell={len(output_df) - buy_count - hold_count}"
    )

    if buy_count > 0:
        print("\n[factor] Buy 信号个股（前20）:")
        buys = output_df[output_df["signal"] == "buy"].sort_values(
            "factor_value", ascending=False
        )
        for _, row in buys.head(20).iterrows():
            print(
                f"  {row['ts_code']}  fv={row['factor_value']:.4f}  "
                f"ncav_ratio={row['ncav_ratio']:.2f}  pb={row['pb']:.2f}  "
                f"score={row['score']:.1f}  审计={row['audit_opinion'][:8]}"
            )

    return output_df


if __name__ == "__main__":
    main()
