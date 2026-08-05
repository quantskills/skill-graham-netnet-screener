#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
财务数据字段探测脚本

目的：确认 panda_data.get_fina_reports() 返回的真实字段名，
供 factor.py 的 _find_column() 函数调参使用。

用法：
  python scripts/probe_fina_fields.py
  python scripts/probe_fina_fields.py --quarter 2024q4


"""

import argparse
import os
from datetime import datetime

import pandas as pd
import panda_data


def init_panda():
    username = os.environ.get("PANDA_USERNAME", "")
    password = os.environ.get("PANDA_PASSWORD", "")
    base_url = os.environ.get("PANDA_BASE_URL", "http://pandadata.pandaaiquant.com")

    if not username or not password:
        raise RuntimeError(
            "请设置环境变量：\n"
            "  Linux/Mac:  export PANDA_USERNAME='86手机号'  export PANDA_PASSWORD='密码'\n"
            "  Windows:     $env:PANDA_USERNAME='86手机号'; $env:PANDA_PASSWORD='密码'\n"
        )
    token = panda_data.init_token(username, password, base_url)
    print(f"✅ 已连接 PandaAI, token: {token[:20]}...")
    return token


def probe_industry_constituents():
    print("\n" + "=" * 60)
    print("1. 探测行业成分股接口 get_industry_constituents()")
    print("=" * 60)

    df = panda_data.get_industry_constituents(level="L1")
    print(f"返回行数: {len(df)}")
    print(f"列名: {df.columns.tolist()}")
    print("\n数据样例（前3行）:")
    print(df.head(3).to_string())

    print("\n各列数据类型:")
    for col in df.columns:
        print(f"  {col}: {df[col].dtype}, 非空率: {df[col].notna().mean():.1%}")


def probe_stock_daily():
    print("\n" + "=" * 60)
    print("2. 探测个股日线接口 get_stock_daily()")
    print("=" * 60)

    today = datetime.now().strftime("%Y%m%d")
    df = panda_data.get_stock_daily(
        symbol=["000001.SZ"],
        start_date=today[:4] + "0101",
        end_date=today,
        st=False,
    )
    print(f"返回行数: {len(df)}")
    print(f"列名: {df.columns.tolist()}")
    print("\n数据样例（前3行）:")
    print(df.head(3).to_string())

    print("\n各列数据类型:")
    for col in df.columns:
        print(f"  {col}: {df[col].dtype}, 非空率: {df[col].notna().mean():.1%}")


def probe_fina_reports(quarter: str):
    print("\n" + "=" * 60)
    print(f"3. 探测财务报告接口 get_fina_reports(quarter={quarter})")
    print("=" * 60)

    df = panda_data.get_fina_reports(
        symbol=None,
        start_quarter=quarter,
        end_quarter=quarter,
        is_latest=True,
    )
    print(f"返回行数: {len(df)}")
    print(f"列名: {df.columns.tolist()}")

    print("\n数据样例（前3行）:")
    print(df.head(3).to_string())

    print("\n各列数据类型与非空率:")
    for col in df.columns:
        dtype = df[col].dtype
        non_null_pct = df[col].notna().mean() * 100
        print(f"  {col:30s} | {str(dtype):15s} | 非空率: {non_null_pct:6.1f}%")

    print("\n候选字段匹配情况（用于 factor.py 的 _find_column）:")
    candidate_groups = {
        "股票代码": ["symbol", "ts_code", "stock_symbol", "code"],
        "流动资产合计": ["total_current_assets", "current_assets", "流动资产合计", "total_assets_cur"],
        "负债合计": ["total_liabilities", "liabilities", "total_liab", "负债合计"],
        "少数股东权益": ["minority_interest", "minority_interests", "minority_equity", "少数股东权益"],
        "优先股": ["preferred_stock", "preferred", "优先股", "preferred_equity"],
        "总股本": ["total_shares", "total_share", "shares", "总股本", "total_capital"],
        "净资产": ["total_equity", "total_equity_attr_p", "净资产", "所有者权益合计"],
        "净利润": ["net_profit", "netprofit", "net_profit_attr_p", "净利润"],
        "经营现金流": ["net_cashflow_from_operations", "cashflow_from_operations",
                   "operating_cash_flow", "经营现金流净额"],
        "担保金额": ["guarantee_amount", "guarantee", "担保金额", "对外担保"],
    }

    actual_cols = set(df.columns)
    for group_name, candidates in candidate_groups.items():
        found = [c for c in candidates if c in actual_cols]
        if found:
            print(f"  ✅ {group_name}: {found[0]}（候选: {candidates}）")
        else:
            print(f"  ❌ {group_name}: 未找到匹配字段（候选: {candidates}）")


def probe_audit_opinion(quarter: str):
    print("\n" + "=" * 60)
    print(f"4. 探测审计意见接口 get_audit_opinion(quarter={quarter})")
    print("=" * 60)

    df = panda_data.get_audit_opinion(
        symbol=None,
        start_quarter=quarter,
        end_quarter=quarter,
        market="cn",
    )
    print(f"返回行数: {len(df)}")
    print(f"列名: {df.columns.tolist()}")

    print("\n数据样例（前3行）:")
    print(df.head(3).to_string())

    print("\n审计意见分布:")
    if "opinion" in df.columns:
        opinion_counts = df["opinion"].value_counts()
        for op, cnt in opinion_counts.items():
            print(f"  {op}: {cnt} 条")


def main():
    parser = argparse.ArgumentParser(description="财务数据字段探测")
    parser.add_argument(
        "--quarter", type=str,
        default=datetime.now().strftime("%Y") + "q4",
        help="探测的季度 YYYYqN（默认本年 Q4）",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("PandaAI SDK 字段探测脚本")
    print(f"目标季度: {args.quarter}")
    print("=" * 60)

    init_panda()
    probe_industry_constituents()
    probe_stock_daily()
    probe_fina_reports(args.quarter)
    probe_audit_opinion(args.quarter)

    print("\n" + "=" * 60)
    print("探测完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
