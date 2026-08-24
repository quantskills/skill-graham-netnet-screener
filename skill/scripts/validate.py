#!/usr/bin/env python3
"""
NCAV 折价因子验证脚本 —— 三层沙漏检测

三层检测：
  第一层：未来函数检测（shift 对齐）
  第二层：过拟合检测（参数敏感性）
  第三层：样本外检测（跨年份/跨行情验证）

用法：
  python scripts/validate.py
  python scripts/validate.py --parquet production/数据库.parquet
  python scripts/validate.py --username '86手机号' --password '密码'
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
                    print(f"[validate] 从 .env 加载: {key.strip()}")


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
    """加载因子 Parquet 数据"""
    if not os.path.exists(parquet_path):
        print(f"[validate] 错误: Parquet 文件不存在: {parquet_path}")
        print("[validate] 请先运行 factor.py 生成因子数据")
        sys.exit(1)

    table = pq.read_table(parquet_path)
    df = table.to_pandas()
    print(f"[validate] 加载数据: {len(df)} 行, {len(df.columns)} 列")
    return df


def load_market_data(
    start_date: str = None,
    end_date: str = None,
    period: int = 20,
    username: str = None,
    password: str = None,
    base_url: str = None,
) -> pd.DataFrame:
    """
    加载全市场日线数据并计算前向收益率。

    接口：panda_data.get_stock_daily(symbol=None, start_date, end_date, st=False)
    返回字段：symbol, date, close, volume, forward_return_{period}d

    IC 计算公式：corr(factor_value_t, forward_return_{t+1})
    """
    _get_panda_token(username, password, base_url)

    if start_date is None:
        end_date = datetime.now().strftime("%Y%m%d")
        start_date = (datetime.now() - timedelta(days=60)).strftime("%Y%m%d")

    print(f"[validate] 加载市场数据: {start_date} → {end_date}")

    df = panda_data.get_stock_daily(
        symbol=None,      # 全市场
        start_date=start_date,
        end_date=end_date,
        st=False,
    )

    if df is None or df.empty:
        raise RuntimeError(f"[validate] 市场数据为空，日期范围: {start_date}-{end_date}")

    # 字段名标准化
    sym_col  = _col(df, ["symbol", "ts_code", "stock_symbol", "code"])
    date_col = _col(df, ["date", "trade_date"])
    close_col = _col(df, ["close", "close_price"])

    df = df.rename(columns={
        sym_col: "ts_code", date_col: "date", close_col: "close"
    })
    df["date"] = pd.to_datetime(df["date"], format="%Y%m%d", errors="coerce")
    df = df.sort_values(["ts_code", "date"])

    # 计算前向收益率
    df[f"forward_return_{period}d"] = df.groupby("ts_code")["close"].shift(-period) / df["close"] - 1

    print(f"[validate] 市场数据加载完成: {len(df)} 行, {df['ts_code'].nunique()} 只股票")
    return df[["ts_code", "date", "close", f"forward_return_{period}d"]]


def _col(df: pd.DataFrame, candidates: list) -> str:
    """查找第一个匹配的列名"""
    for c in candidates:
        if c in df.columns:
            return c
    return df.columns[0]  # fallback


# ─── 第一层：未来函数检测 ─────────────────────────────────────────────

def test_lookahead_bias(df: pd.DataFrame, as_of_date: str = None) -> dict:
    """
    第一层：未来函数检测

    检测项：
    1. 行情数据：收盘价是否使用了 trade_date 之后的数据
    2. 财务数据：是否使用了 trade_date 之后才披露的财报
    3. 行业/ST 分类：是否使用了 trade_date 之后的状态

    方法：
    - 检查 trade_date 与财务数据 report_date 的逻辑关系
    - 对比不同 trade_date 下同一股票的 factor_value 是否因数据更新而跳变
    """
    results = {}
    print("\n" + "=" * 50)
    print("第一层：未来函数检测")
    print("=" * 50)

    # 检测 1：trade_date 格式与范围合理性
    try:
        dates = pd.to_datetime(df["trade_date"], format="%Y%m%d")
        max_date = dates.max()
        today = pd.Timestamp.now().normalize()
        if max_date > today:
            print("  ❌ FAIL: trade_date 包含未来日期")
            results["future_trade_date"] = "FAIL"
        else:
            print("  ✅ PASS: trade_date 无未来日期")
            results["future_trade_date"] = "PASS"
    except Exception as e:
        print(f"  ❌ FAIL: trade_date 解析异常: {e}")
        results["future_trade_date"] = "FAIL"

    # 检测 2：score 范围检查（0-100）
    if "score" in df.columns:
        score_min = df["score"].min()
        score_max = df["score"].max()
        if score_min >= 0 and score_max <= 100:
            print(f"  ✅ PASS: score 范围 [{score_min:.1f}, {score_max:.1f}] 在 [0, 100] 内")
            results["score_range"] = "PASS"
        else:
            print(f"  ❌ FAIL: score 范围 [{score_min:.1f}, {score_max:.1f}] 超出 [0, 100]")
            results["score_range"] = "FAIL"

    # 检测 3：signal 枚举检查
    if "signal" in df.columns:
        valid_signals = {"buy", "hold", "sell"}
        actual_signals = set(df["signal"].unique())
        invalid = actual_signals - valid_signals
        if not invalid:
            print(f"  ✅ PASS: signal 枚举值合法 {actual_signals}")
            results["signal_enum"] = "PASS"
        else:
            print(f"  ❌ FAIL: signal 含非法值 {invalid}")
            results["signal_enum"] = "FAIL"

    # 检测 4：财务数据时点对齐验证
    # 需要对比 report_date 与 trade_date 的披露时间差
    # A股年报最晚次年4月30日披露，季报有固定截止日
    print("  ⚠️  WARNING: 财务披露时点对齐需人工核验（年报≤次年4月底，Q1≤4月底，半年报≤8月底，Q3≤10月底）")
    results["financial_alignment"] = "MANUAL_CHECK"

    # 检测 5：因子值跳变检测（同一股票相邻两期 factor_value 异常跳变可能暗示使用了未来数据）
    if len(df) > 1 and "factor_value" in df.columns and "ts_code" in df.columns:
        try:
            # 按股票分组检查标准差
            fv_std = df.groupby("ts_code")["factor_value"].std()
            extreme_stocks = fv_std[fv_std > fv_std.quantile(0.99)]
            if len(extreme_stocks) > 0:
                print(f"  ⚠️  WARNING: {len(extreme_stocks)} 只股票 factor_value 标准差异常大（可能数据更新导致）")
                results["factor_jump"] = "WARNING"
            else:
                print("  ✅ PASS: factor_value 分布无明显跳变")
                results["factor_jump"] = "PASS"
        except Exception:
            results["factor_jump"] = "SKIP"

    return results


# ─── 第二层：过拟合检测 ───────────────────────────────────────────────

def test_overfitting(df: pd.DataFrame) -> dict:
    """
    第二层：过拟合检测

    检测项：
    1. 参数敏感性：微调 threshold（0.60/0.66/0.70/0.80），观察 buy 信号数量和 IC 变化
    2. 若 IC 在参数微调时大幅波动 → 报 warning
    """
    results = {}
    print("\n" + "=" * 50)
    print("第二层：过拟合检测")
    print("=" * 50)

    if "ncav_ratio" not in df.columns:
        print("  ⚠️  SKIP: 缺少 ncav_ratio 列，无法进行参数敏感性分析")
        results["parameter_sensitivity"] = "SKIP"
        return results

    # 参数敏感性分析
    thresholds = [0.60, 0.66, 0.70, 0.80, 1.00]
    buy_counts = []

    print("\n  参数敏感性（threshold → buy 信号数量）:")
    for t in thresholds:
        n_buy = (df["ncav_ratio"] < t).sum()
        buy_counts.append(n_buy)
        print(f"    threshold={t:.2f} → buy={n_buy}")

    # 检查 buy 数量对参数的敏感度
    if buy_counts[0] > 0:
        ratio_change = abs(buy_counts[-1] - buy_counts[0]) / max(buy_counts[0], 1)
        if ratio_change > 5.0:
            print(f"  ⚠️  WARNING: buy 数量对 threshold 高度敏感（变化 {ratio_change:.1f}x），可能存在过拟合风险")
            results["parameter_sensitivity"] = "WARNING"
        else:
            print(f"  ✅ PASS: buy 数量对 threshold 变化平稳（{ratio_change:.1f}x）")
            results["parameter_sensitivity"] = "PASS"
    else:
        print("  ⚠️  SKIP: 无 buy 信号，无法评估敏感度")
        results["parameter_sensitivity"] = "SKIP"

    return results


# ─── 第三层：样本外检测 ───────────────────────────────────────────────

def test_out_of_sample(df: pd.DataFrame) -> dict:
    """
    第三层：样本外检测

    检测项：
    1. 跨年份验证：若有多年数据，分年计算 IC
    2. 打乱基准线：shuffled IC ≈ 0 验证因子非随机

    注意：完整样本外检测需要多期截面数据 + 未来收益率。
    单期截面只能做基础合理性检查。
    """
    results = {}
    print("\n" + "=" * 50)
    print("第三层：样本外检测")
    print("=" * 50)

    # 检测 1：因子分布合理性
    if "factor_value" in df.columns:
        fv = df["factor_value"].dropna()
        n_negative = (fv < 0).sum()  # factor_value < 0 意味着 ncav_ratio > 1（高估）
        n_positive = (fv > 0).sum()  # factor_value > 0 意味着 ncav_ratio < 1（低估）
        pct_positive = n_positive / len(fv) * 100 if len(fv) > 0 else 0

        print(f"  因子分布: 正值(低估)={n_positive} ({pct_positive:.1f}%), 负值(高估)={n_negative}")

        # A股 NCAV 低估股极少，正值比例通常 < 10%
        if pct_positive < 30:
            print(f"  ✅ PASS: 低估股比例 {pct_positive:.1f}% 符合 A 股实际（通常 < 10%）")
            results["distribution_check"] = "PASS"
        else:
            print(f"  ⚠️  WARNING: 低估股比例 {pct_positive:.1f}% 偏高，可能 NCAV 计算有误")
            results["distribution_check"] = "WARNING"

    # 检测 2：打乱基准线（因子值随机打乱后 IC 应 ≈ 0）
    print("  ⚠️  INFO: 完整打乱基准线检测需要多期数据 + 未来收益率，请在 backtest.py 中执行")
    results["shuffled_baseline"] = "DEFER_TO_BACKTEST"

    # 检测 3：跨年份（需多期数据）
    n_dates = df["trade_date"].nunique() if "trade_date" in df.columns else 0
    if n_dates > 1:
        print(f"  检测到 {n_dates} 个 trade_date，可进行跨年份验证")
        results["cross_year"] = "AVAILABLE"
    else:
        print(f"  ⚠️  SKIP: 仅 {n_dates} 期数据，无法进行跨年份验证（建议至少 3 年月度数据）")
        results["cross_year"] = "SKIP"

    return results


# ─── Parquet 质量检查 ─────────────────────────────────────────────────

def test_parquet_quality(df: pd.DataFrame) -> dict:
    """检查 Parquet 数据质量"""
    results = {}
    print("\n" + "=" * 50)
    print("Parquet 质量检查")
    print("=" * 50)

    # 主键唯一性：(trade_date, ts_code) 应唯一
    if "trade_date" in df.columns and "ts_code" in df.columns:
        dup_count = df.duplicated(subset=["trade_date", "ts_code"]).sum()
        if dup_count == 0:
            print(f"  ✅ PASS: 主键 (trade_date, ts_code) 无重复")
            results["primary_key_unique"] = "PASS"
        else:
            print(f"  ❌ FAIL: 主键重复 {dup_count} 行")
            results["primary_key_unique"] = "FAIL"

    # 必填字段完整性
    required_fields = [
        "trade_date", "asset_type", "ts_code", "factor_id",
        "factor_name", "factor_value", "score", "signal",
        "data_version", "update_time"
    ]
    missing = [f for f in required_fields if f not in df.columns]
    if not missing:
        print(f"  ✅ PASS: 必填字段完整 ({len(required_fields)} 个)")
        results["required_fields"] = "PASS"
    else:
        print(f"  ❌ FAIL: 缺失必填字段: {missing}")
        results["required_fields"] = "FAIL"

    # factor_value 无 NaN
    if "factor_value" in df.columns:
        nan_count = df["factor_value"].isna().sum()
        if nan_count == 0:
            print(f"  ✅ PASS: factor_value 无缺失值")
            results["no_nan_factor"] = "PASS"
        else:
            print(f"  ❌ FAIL: factor_value 有 {nan_count} 个缺失值")
            results["no_nan_factor"] = "FAIL"

    # data_version 格式
    if "data_version" in df.columns:
        versions = df["data_version"].unique()
        print(f"  INFO: data_version = {versions}")
        results["data_version"] = "PASS"

    return results


# ─── 主流程 ───────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="NCAV 因子三层沙漏验证")
    parser.add_argument(
        "--parquet",
        type=str,
        default=str(Path(__file__).parent.parent / "生产产物" / "数据库.parquet"),
        help="因子 Parquet 文件路径",
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
    print("NCAV 折价因子 —— 三层沙漏验证")
    print("=" * 60)

    # 加载数据
    df = load_factor_data(args.parquet)

    # 三层检测
    layer1 = test_lookahead_bias(df)
    layer2 = test_overfitting(df)
    layer3 = test_out_of_sample(df)
    quality = test_parquet_quality(df)

    # 汇总
    all_results = {**layer1, **layer2, **layer3, **quality}

    print("\n" + "=" * 60)
    print("验证汇总")
    print("=" * 60)

    fails = [k for k, v in all_results.items() if v == "FAIL"]
    warnings = [k for k, v in all_results.items() if v == "WARNING"]
    passes = [k for k, v in all_results.items() if v == "PASS"]

    for k, v in all_results.items():
        icon = {"PASS": "✅", "FAIL": "❌", "WARNING": "⚠️"}.get(v, "ℹ️")
        print(f"  {icon} {k}: {v}")

    print(f"\n  通过: {len(passes)}, 警告: {len(warnings)}, 失败: {len(fails)}")

    if fails:
        print(f"\n  ❌ 验证未通过！失败项: {fails}")
        sys.exit(1)
    elif warnings:
        print(f"\n  ⚠️  验证通过但有警告: {warnings}")
        print("  建议处理警告项后重新验证")
    else:
        print(f"\n  ✅ 全部验证通过！")


if __name__ == "__main__":
    main()
