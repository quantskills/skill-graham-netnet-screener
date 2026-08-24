#!/usr/bin/env python3
"""
NCAV 折价因子回测脚本

输出指标：
  - IC / RankIC 时序
  - ICIR
  - 分层收益（5 组，多空组合）
  - 最大回撤
  - 换手率
  - 样本外 vs 样本内对比

用法：
  python scripts/backtest.py
  python scripts/backtest.py --parquet production/数据库.parquet --period 36
  python scripts/backtest.py --username '86手机号' --password '密码'
"""

import argparse
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
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
                    print(f"[backtest] 从 .env 加载: {key.strip()}")


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
            "  3. .env 文件: 在项目根目录创建 .env 文件\n"
            "  4. 运行时交互式输入\n"
        )

    return panda_data.init_token(username, password, base_url)

# ─── 数据加载 ─────────────────────────────────────────────────────────

def load_factor_data(parquet_path: str) -> pd.DataFrame:
    """加载因子 Parquet"""
    if not os.path.exists(parquet_path):
        print(f"[backtest] 错误: Parquet 文件不存在: {parquet_path}")
        print("[backtest] 请先运行 factor.py 生成因子数据")
        sys.exit(1)
    table = pq.read_table(parquet_path)
    df = table.to_pandas()
    print(f"[backtest] 加载因子数据: {len(df)} 行")
    return df


def load_forward_returns(
    start_date: str = None,
    end_date: str = None,
    period: int = 20,
    st: bool = False,
    username: str = None,
    password: str = None,
    base_url: str = None,
) -> pd.DataFrame:
    """
    加载全市场日线数据并计算未来 N 日收益率。

    接口：panda_data.get_stock_daily(symbol=None, start_date, end_date, st=False)
    IC 计算：IC = corr(factor_value_t, forward_return_{t+period})

    参数：
      start_date: YYYYMMDD，默认往前推60天
      end_date:   YYYYMMDD，默认当日
      period:     持有期（交易日），默认 20（约1个月）
      st:         是否含 ST 股，默认 False
      username:   PandaAI 用户名
      password:   PandaAI 密码
      base_url:   PandaAI 服务地址

    返回：ts_code, trade_date(YYYYMMDD 字符串), close, forward_return
      注：trade_date 与 factor.py 落盘的主键列同名、同类型（字符串），
          以保证 calculate_ic / calculate_quantile_returns 的 merge 能对齐。
    """
    _get_panda_token(username, password, base_url)

    # 日期默认值
    if end_date is None:
        end_date = datetime.now().strftime("%Y%m%d")
    if start_date is None:
        start_date = (datetime.now() - timedelta(days=period * 5)).strftime("%Y%m%d")

    print(f"[backtest] 加载收益率数据: {start_date} → {end_date}, 持有期={period}d")

    df = panda_data.get_stock_daily(
        symbol=None,        # 全市场
        start_date=start_date,
        end_date=end_date,
        st=st,
    )

    if df is None or df.empty:
        raise RuntimeError(f"[backtest] 日线数据为空: {start_date}-{end_date}")

    # 字段标准化
    sym_col   = _backtest_col(df, ["symbol", "ts_code", "stock_symbol", "code"])
    date_col  = _backtest_col(df, ["date", "trade_date"])
    close_col = _backtest_col(df, ["close", "close_price"])

    df = df.rename(columns={sym_col: "ts_code", date_col: "date", close_col: "close"})
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d", errors="coerce")
    df = df.sort_values(["ts_code", "date"])

    # 计算前向收益率（shift(-period) = t+N 日收盘 / t 日收盘 - 1）
    df["forward_return"] = (
        df.groupby("ts_code")["close"].shift(-period) / df["close"] - 1
    )

    # 统一主键列名与类型：trade_date 为 YYYYMMDD 字符串，与 factor.py 落盘一致
    df["trade_date"] = df["date"].dt.strftime("%Y%m%d")

    print(f"[backtest] 收益率数据完成: {len(df)} 行, {df['ts_code'].nunique()} 只股票")
    return df[["ts_code", "trade_date", "close", "forward_return"]]


def _backtest_col(df: pd.DataFrame, candidates: list) -> str:
    for c in candidates:
        if c in df.columns:
            return c
    return df.columns[0]


# ─── IC 分析 ──────────────────────────────────────────────────────────

def calculate_ic(factor_df: pd.DataFrame, returns_df: pd.DataFrame) -> pd.DataFrame:
    """
    计算 IC 时序。

    IC = corr(factor_value_t, forward_return_{t+1})
    RankIC = spearman_corr(factor_value_t, forward_return_{t+1})

    关键：IC 是对未来收益的预测能力，不是因子自相关！
    """
    # 合并因子值与未来收益
    merged = factor_df.merge(returns_df, on=["ts_code", "trade_date"], how="inner")

    if len(merged) == 0:
        print("[backtest] 警告: 因子与收益率数据无交集")
        return pd.DataFrame()

    # 按 trade_date 分组计算截面 IC
    ic_records = []
    for date, group in merged.groupby("trade_date"):
        if len(group) < 30:  # 截面股票数太少跳过
            continue

        fv = group["factor_value"]
        ret = group["forward_return"]

        # 去除 NaN
        valid = fv.notna() & ret.notna()
        if valid.sum() < 30:
            continue

        fv_valid = fv[valid]
        ret_valid = ret[valid]

        # IC: Pearson 相关
        ic = fv_valid.corr(ret_valid)

        # RankIC: Spearman 相关
        rank_ic = fv_valid.rank().corr(ret_valid.rank())

        ic_records.append({
            "trade_date": date,
            "IC": ic,
            "RankIC": rank_ic,
            "n_stocks": valid.sum(),
        })

    ic_df = pd.DataFrame(ic_records)
    return ic_df


def summarize_ic(ic_df: pd.DataFrame) -> dict:
    """汇总 IC 统计指标"""
    if ic_df.empty:
        return {}

    summary = {
        "IC_mean": ic_df["IC"].mean(),
        "IC_std": ic_df["IC"].std(),
        "ICIR": ic_df["IC"].mean() / ic_df["IC"].std() if ic_df["IC"].std() > 0 else 0,
        "IC_positive_rate": (ic_df["IC"] > 0).mean(),
        "RankIC_mean": ic_df["RankIC"].mean(),
        "RankIC_std": ic_df["RankIC"].std(),
        "RankICIR": ic_df["RankIC"].mean() / ic_df["RankIC"].std() if ic_df["RankIC"].std() > 0 else 0,
        "n_periods": len(ic_df),
    }
    return summary


# ─── 分层收益 ─────────────────────────────────────────────────────────

def calculate_quantile_returns(
    factor_df: pd.DataFrame,
    returns_df: pd.DataFrame,
    n_groups: int = 5,
) -> pd.DataFrame:
    """
    按因子值分 N 组，计算每组平均收益。

    返回：trade_date, group, mean_return
    """
    merged = factor_df.merge(returns_df, on=["ts_code", "trade_date"], how="inner")

    if merged.empty:
        return pd.DataFrame()

    records = []
    for date, group in merged.groupby("trade_date"):
        if len(group) < n_groups * 5:
            continue

        valid = group["factor_value"].notna() & group["forward_return"].notna()
        group = group[valid]

        # 按 factor_value 分位分组（值越大越低估）
        group["quantile"] = pd.qcut(
            group["factor_value"], q=n_groups, labels=False, duplicates="drop"
        )

        for q in range(n_groups):
            q_data = group[group["quantile"] == q]
            if len(q_data) > 0:
                records.append({
                    "trade_date": date,
                    "group": q + 1,  # 1=最低估, 5=最高估
                    "mean_return": q_data["forward_return"].mean(),
                    "n_stocks": len(q_data),
                })

    return pd.DataFrame(records)


def calculate_long_short_returns(quantile_df: pd.DataFrame) -> pd.Series:
    """计算多空收益（Top组 - Bottom组）"""
    if quantile_df.empty:
        return pd.Series(dtype=float)

    pivot = quantile_df.pivot_table(
        index="trade_date", columns="group", values="mean_return"
    )

    # 多空 = 最低估组(1) - 最高估组(最大)
    max_group = pivot.columns.max()
    long_short = pivot[1] - pivot[max_group]
    return long_short


# ─── 最大回撤 ─────────────────────────────────────────────────────────

def calculate_max_drawdown(cumulative_returns: pd.Series) -> float:
    """计算最大回撤"""
    if cumulative_returns.empty:
        return 0.0

    cum = (1 + cumulative_returns).cumprod()
    peak = cum.cummax()
    drawdown = (cum - peak) / peak
    return drawdown.min()


# ─── 换手率 ───────────────────────────────────────────────────────────

def calculate_turnover(factor_df: pd.DataFrame, top_n: int = None) -> pd.Series:
    """
    计算相邻截面 Top 组的换手率。

    换手率 = 新进入股票数 / 组内总股票数
    """
    dates = sorted(factor_df["trade_date"].unique())
    if len(dates) < 2:
        return pd.Series(dtype=float)

    turnover_records = []
    prev_stocks = None

    for date in dates:
        day_df = factor_df[factor_df["trade_date"] == date].copy()
        day_df = day_df.sort_values("factor_value", ascending=False)

        if top_n is None:
            top_n = max(1, len(day_df) // 5)  # 默认 Top 20%

        current_stocks = set(day_df.head(top_n)["ts_code"])

        if prev_stocks is not None:
            new_stocks = len(current_stocks - prev_stocks)
            turnover = new_stocks / max(len(current_stocks), 1)
            turnover_records.append({
                "trade_date": date,
                "turnover": turnover,
            })

        prev_stocks = current_stocks

    return pd.DataFrame(turnover_records)


# ─── 样本外对比 ───────────────────────────────────────────────────────

def split_in_out_sample(ic_df: pd.DataFrame, oos_months: int = 12) -> tuple:
    """
    将 IC 时序分为样本内和样本外。

    最后 oos_months 个月作为样本外。
    """
    if ic_df.empty:
        return pd.DataFrame(), pd.DataFrame()

    ic_df = ic_df.copy()
    # trade_date 为 YYYYMMDD 字符串，转为 datetime 以支持按月切分
    ic_df["_dt"] = pd.to_datetime(ic_df["trade_date"], format="%Y%m%d", errors="coerce")
    ic_df = ic_df.sort_values("_dt")
    cutoff = ic_df["_dt"].max() - pd.DateOffset(months=oos_months)

    in_sample = ic_df[ic_df["_dt"] <= cutoff].drop(columns=["_dt"])
    out_sample = ic_df[ic_df["_dt"] > cutoff].drop(columns=["_dt"])

    return in_sample, out_sample


# ─── 主流程 ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="NCAV 因子回测")
    parser.add_argument(
        "--parquet",
        type=str,
        default=str(Path(__file__).parent.parent / "生产产物" / "数据库.parquet"),
        help="因子 Parquet 文件路径",
    )
    parser.add_argument(
        "--period",
        type=int,
        default=20,
        help="持有期（交易日），默认 20（约1个月）",
    )
    parser.add_argument(
        "--oos-months",
        type=int,
        default=12,
        help="样本外月数（默认 12）",
    )
    parser.add_argument(
        "--username", type=str, default=None,
        help="PandaAI 用户名（86手机号）",
    )
    parser.add_argument(
        "--password", type=str, default=None,
        help="PandaAI 密码",
    )
    parser.add_argument(
        "--base-url", type=str, default=None,
        help="PandaAI 服务地址",
    )
    parser.add_argument(
        "--no-interactive", action="store_true",
        help="禁用交互式输入",
    )
    args = parser.parse_args()

    print("=" * 60)
    print("NCAV 折价因子 —— 回测报告")
    print(f"  持有期: {args.period} 交易日")
    print(f"  样本外: {args.oos_months} 个月")
    print("=" * 60)

    # 1. 加载数据
    factor_df = load_factor_data(args.parquet)

    # 2. 加载未来收益率
    try:
        returns_df = load_forward_returns(
            period=args.period,
            username=args.username,
            password=args.password,
            base_url=args.base_url,
        )
    except Exception as e:
        print(f"\n[backtest] ❌ 收益率数据加载失败: {e}")
        print("[backtest] 回测终止。请确认 panda_data 认证和环境变量。")
        sys.exit(1)

    # 3. IC 分析
    print("\n--- IC 分析 ---")
    ic_df = calculate_ic(factor_df, returns_df)
    ic_summary = summarize_ic(ic_df)

    if ic_summary:
        print(f"  IC 均值:    {ic_summary['IC_mean']:.4f}")
        print(f"  IC 标准差:  {ic_summary['IC_std']:.4f}")
        print(f"  ICIR:       {ic_summary['ICIR']:.4f}")
        print(f"  IC > 0 比例: {ic_summary['IC_positive_rate']:.1%}")
        print(f"  RankIC 均值: {ic_summary['RankIC_mean']:.4f}")
        print(f"  RankICIR:   {ic_summary['RankICIR']:.4f}")
        print(f"  有效期数:   {ic_summary['n_periods']}")

        # 达标判断
        if abs(ic_summary["IC_mean"]) > 0.03:
            print("  ✅ |IC| > 0.03 达标")
        else:
            print(f"  ❌ |IC| = {abs(ic_summary['IC_mean']):.4f} < 0.03 未达标")

        if abs(ic_summary["ICIR"]) > 0.5:
            print("  ✅ |ICIR| > 0.5 达标")
        else:
            print(f"  ❌ |ICIR| = {abs(ic_summary['ICIR']):.4f} < 0.5 未达标")

    # 4. 分层收益
    print("\n--- 分层收益 ---")
    quantile_df = calculate_quantile_returns(factor_df, returns_df)
    if not quantile_df.empty:
        group_avg = quantile_df.groupby("group")["mean_return"].mean()
        print("  各组平均收益:")
        for g, ret in group_avg.items():
            print(f"    第{g}组: {ret:.4%}")

        long_short = calculate_long_short_returns(quantile_df)
        if not long_short.empty:
            print(f"  多空年化收益: {long_short.mean() * 12:.2%}（月度）")

            # 单调性检查
            is_monotonic = all(
                group_avg.iloc[i] >= group_avg.iloc[i + 1]
                for i in range(len(group_avg) - 1)
            )
            if is_monotonic:
                print("  ✅ 分层收益单调递减（低估组 > 高估组）")
            else:
                print("  ⚠️  分层收益非完全单调")

    # 5. 最大回撤
    print("\n--- 最大回撤 ---")
    long_short = calculate_long_short_returns(quantile_df)
    if not long_short.empty:
        mdd = calculate_max_drawdown(long_short)
        print(f"  多空组合最大回撤: {mdd:.2%}")

    # 6. 换手率
    print("\n--- 换手率 ---")
    turnover_df = calculate_turnover(factor_df)
    if not turnover_df.empty:
        avg_turnover = turnover_df["turnover"].mean()
        print(f"  平均换手率: {avg_turnover:.1%}")
        if avg_turnover > 0.5:
            print("  ⚠️  换手率 > 50%，因子稳定性不足或流动性受限")
        else:
            print("  ✅ 换手率合理")

    # 7. 样本外对比
    print("\n--- 样本外对比 ---")
    in_sample, out_sample = split_in_out_sample(ic_df, args.oos_months)
    if not in_sample.empty and not out_sample.empty:
        is_ic = in_sample["IC"].mean()
        oos_ic = out_sample["IC"].mean()
        decay = 1 - abs(oos_ic / is_ic) if abs(is_ic) > 0 else 0

        print(f"  样本内 IC:  {is_ic:.4f} ({len(in_sample)} 期)")
        print(f"  样本外 IC:  {oos_ic:.4f} ({len(out_sample)} 期)")
        print(f"  IC 衰减:    {decay:.1%}")

        if decay < 0.5:
            print("  ✅ IC 衰减 < 50%，样本外表现稳定")
        else:
            print("  ❌ IC 衰减 ≥ 50%，样本外显著恶化")

    print("\n" + "=" * 60)
    print("回测完成")
    print("=" * 60)


if __name__ == "__main__":
    main()
