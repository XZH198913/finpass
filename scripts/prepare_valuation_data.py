#!/usr/bin/env python
"""把 A 股估值 xlsx 转成 FinPass 可读的 UTF-8 CSV（数据准备，不进 src/）。

为什么这个脚本不在 ``src/finpass/`` 里：读 xlsx、拆多级表头、丢表尾说明行，
都是「这份导出文件长什么样」的问题，不是清洗规则。FinPass 只吃 CSV，
形态差异在这里抹平，引擎不认 xlsx、也不认 iFinD 的表头元数据。

脚本只做四件事，**不改任何值**：

1. 读第 1 个 sheet，全部按字符串读（保留 ``--``，不猜类型）；
2. 表头归一：原列名首行（指标名）→ ASCII 短名，``[交易日期]`` / ``[单位]`` 等
   元数据不进列名（它们进 ``out/valuation-check/header_mapping.md``）；
3. 丢尾部非数据行：``证券代码`` 不匹配 ``6位.交易所`` 的行（本文件恰为 2 行：
   全空行 + 「数据来源」行）；
4. 丢全空列（除 ``--`` 外无值），并在终端列出丢了哪几列。

值层面一个字都不动：``--`` 原样留着，交给 FinPass 的 ``missing_token`` 规则处理。
「缺失怎么填」是清洗决策，不是数据准备。

用法：
    ./.venv/Scripts/python.exe scripts/prepare_valuation_data.py
"""

from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
SRC = REPO / "data" / "a_stock_valuation_raw.xlsx.xlsx"
OUT_DIR = REPO / "out" / "valuation-check"
OUT_CSV = OUT_DIR / "a_stock_valuation_clean.csv"
OUT_MAP = OUT_DIR / "header_mapping.md"

#: 主键列的格式：``600000.SH`` 这种「6 位数字.交易所」。
CODE_PATTERN = re.compile(r"^\d{6}\.(SH|SZ|BJ)$")

#: 原列名首行（指标名）→ 短名。**只映射名字，不改值。**
#: 短名规则：指标_口径_期间[_单位]，全部小写 ASCII，可安全写进 yaml 的键。
NAME_MAP: dict[str, str] = {
    # 主键
    "证券代码": "证券代码",
    "证券名称": "证券名称",
    # 市盈率族
    "市盈率（PE，LYR）": "pe_lyr_3m",
    "市盈率（PE，MRQ）": "pe_mrq_3m",
    "市盈率（PE，TTM）": "pe_ttm_3m",
    "市盈率（PE，扣除非经常性损益TTM）": "pe_ttm_deducted_3m",
    "静态市盈率（中证发布）": "pe_static_csi_3m_pct",
    "滚动市盈率（中证发布）": "pe_rolling_csi_3m_pct",
    "PEG值（LYR）": "peg_lyr_3m",
    "PER（LYR）": "per_lyr_3m",
    "市盈率（PE）": "pe_report_annual_3m",
    "历史PEG值": "peg_hist_3m",
    "市盈率（PE，TTM）分位数": "pe_ttm_percentile",
    # 市净率族
    "市净率（PB，MRQ）": "pb_mrq_3m",
    "市净率（中证发布）": "pb_csi_3m_pct",
    "市净率（PB，最新）": "pb_latest_3m",
    "市净率（PB）": "pb_report_q1_3m",
    "市净率（PB，MRQ）分位数": "pb_mrq_percentile",
    # 市现率族
    "市现率（经营现金流，LYR）": "pcf_ocf_lyr_3m",
    "市现率（PCF，经营现金流TTM）": "pcf_ocf_ttm_3m",
    "市现率（现金净流量，LYR）": "pcf_netcf_lyr_3m",
    "市现率（PCF，现金净流量TTM）": "pcf_netcf_ttm_3m",
    "市现率（PCF，经营现金流）": "pcf_ocf_report_annual_3m",
    "市现率（PCF，现金净流量）": "pcf_netcf_report_annual_3m",
    "市现率（PCF，TTM）分位数": "pcf_ttm_percentile",
    # 市销率 / 市研率
    "市销率（PS，LYR）": "ps_lyr_3m",
    "市销率（PS，MRQ）": "ps_mrq_3m",
    "市销率（PS，TTM）": "ps_ttm_3m",
    "市销率（PS）": "ps_report_annual_3m",
    "市销率（PS，TTM）分位数": "ps_ttm_percentile",
    "市研率（PRR，LYR）": "prr_lyr_3m",
    # 股息率
    "股息率（股票获利率）": "dividend_yield_2026_pct",
    "股息率_近12个月": "dividend_yield_12m_pct",
    "股息率（TTM，包含特别分红）": "dividend_yield_ttm_special_pct",
    "股息率（TTM）": "dividend_yield_ttm_pct",
    # 52 周区间
    "区间最高PE（TTM）": "pe_ttm_52w_high",
    "区间最低PE（TTM）": "pe_ttm_52w_low",
    "区间平均PE（TTM）": "pe_ttm_52w_avg",
    "区间最高PS（TTM）": "ps_ttm_52w_high",
    "区间最低PS（TTM）": "ps_ttm_52w_low",
    "区间平均PS（TTM）": "ps_ttm_52w_avg",
    "区间平均PS（LYR）": "ps_lyr_52w_avg",
    "区间最高PB（最新）": "pb_latest_52w_high",
    "区间最低PB（最新）": "pb_latest_52w_low",
    "区间平均PB（最新）": "pb_latest_52w_avg",
    # 市值与价值
    "总市值": "mktcap_total_3m",
    "总市值（可回测）": "mktcap_total_backtest_3m",
    "总市值（总股本算）": "mktcap_total_by_totalshares_3m",
    "总市值（面向证券）": "mktcap_total_for_security_3m",
    "总市值（证监会算法）": "mktcap_total_csrc_3m",
    "流通市值": "float_mktcap_3m",
    "自由流通市值": "free_float_mktcap_3m",
    "A股市值（含限售股）": "mktcap_a_incl_restricted_3m",
    "A股市值（不含限售股）": "mktcap_a_excl_restricted_3m",
    "B股市值（含限售股，交易币种）": "mktcap_b_incl_restricted_tradeccy_3m",
    "B股市值（含限售股，折人民币）": "mktcap_b_incl_restricted_cny_3m",
    "B股市值（不含限售股，交易币种）": "mktcap_b_excl_restricted_tradeccy_3m",
    "B股市值（不含限售股，折人民币）": "mktcap_b_excl_restricted_cny_3m",
    "股权价值": "equity_value_3m",
    "企业价值（含货币资金）（EV1）": "ev1_incl_cash_3m",
    "企业价值（剔除货币资金）（EV2）": "ev2_excl_cash_3m",
    "企业倍数（EV1/EBITDA）": "ev1_to_ebitda_3m",
    "企业倍数（EV2/EBITDA）": "ev2_to_ebitda_3m",
    "EV/Sales": "ev_to_sales_3m",
    # 预测
    "预测PB": "pb_forecast_2004_3m",
    "预测PB（FY1）": "pb_forecast_fy1_3m",
    "预测PB（FY2）": "pb_forecast_fy2_3m",
    "预测PB（FY3）": "pb_forecast_fy3_3m",
    "预测市盈率（PE，历史预测）": "pe_forecast_hist_2026_3m",
    "预测市盈率（PE，最新预测）": "pe_forecast_latest_2026",
    "预测市盈率（PE，FY1）": "pe_forecast_fy1_3m",
    "预测市盈率（PE，FY2）": "pe_forecast_fy2_3m",
    "预测市盈率（PE，FY3）": "pe_forecast_fy3_3m",
    "预测市盈率（PE，未来12个月）": "pe_forecast_ntm_3m",
    "预测PEG值": "peg_forecast_2026_3m",
    "预测PEG（FY1）": "peg_forecast_fy1_3m",
    "预测PEG（FY2）": "peg_forecast_fy2_3m",
    "预测PEG（未来12个月）": "peg_forecast_ntm_3m",
    "股权激励目标净利润": "equity_incentive_target_np_2025_3m",
}

#: 缺失占位符：只用于判断「这一列是不是全空」，不做替换。
MISSING_TOKENS = {"--", "-", "", "nan", "None"}


def metric_name(header: str) -> str:
    """列名首行 = 指标名；``[交易日期]`` 这类元数据行不算名字。"""
    return str(header).split("\n")[0].strip()


def read_raw() -> pd.DataFrame:
    """按字符串整表读入：值一个字不改，``--`` 保留。"""
    frame = pd.read_excel(SRC, sheet_name=0, header=0, dtype=str, engine="openpyxl")
    return frame.where(frame.notna(), "")


def drop_non_data_rows(frame: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    """丢掉 ``证券代码`` 不是「6 位.交易所」的行（尾部说明行在这里被识别，不靠行号）。"""
    codes = frame["证券代码"].astype(str).str.strip()
    keep = codes.map(lambda code: bool(CODE_PATTERN.match(code)))
    return frame.loc[keep].reset_index(drop=True), int((~keep).sum())


def rename_headers(frame: pd.DataFrame) -> tuple[pd.DataFrame, list[tuple[str, str]]]:
    """按 ``NAME_MAP`` 归一表头；返回新表与「原文 → 短名」映射表。"""
    pairs: list[tuple[str, str]] = []
    new_columns: list[str] = []
    for header in frame.columns:
        key = metric_name(header)
        if key not in NAME_MAP:
            raise KeyError(f"表头未登记，请先补 scripts/prepare_valuation_data.py 的 NAME_MAP：{key!r}")
        short = NAME_MAP[key]
        if short in new_columns:
            raise ValueError(f"短名重复：{short}（原列名 {header!r}）")
        new_columns.append(short)
        pairs.append((str(header), short))
    renamed = frame.copy()
    renamed.columns = new_columns
    return renamed, pairs


def empty_columns(frame: pd.DataFrame) -> list[str]:
    """除 ``--`` / 空串外没有任何值的列。"""
    empty: list[str] = []
    for column in frame.columns:
        values = frame[column].astype(str).str.strip()
        if values.isin(MISSING_TOKENS).all():
            empty.append(column)
    return empty


def write_mapping(pairs: list[tuple[str, str]], dropped: list[str]) -> None:
    lines = ["# 表头归一映射（原列名 → 短名）", ""]
    lines.append("| # | 原列名（``\\n`` 处为换行） | 短名 | 处置 |")
    lines.append("|---|---|---|---|")
    for index, (original, short) in enumerate(pairs, 1):
        note = "丢弃（整列无值）" if short in dropped else "保留"
        lines.append(f"| {index} | `{original.replace(chr(10), ' ⏎ ')}` | `{short}` | {note} |")
    OUT_MAP.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    raw = read_raw()
    rows_before, cols_before = raw.shape

    data, dropped_rows = drop_non_data_rows(raw)
    renamed, pairs = rename_headers(data)
    dropped_cols = empty_columns(renamed)
    cleaned = renamed.drop(columns=dropped_cols)

    cleaned.to_csv(OUT_CSV, index=False, encoding="utf-8")
    write_mapping(pairs, dropped_cols)

    print(f"源文件      : {SRC.relative_to(REPO)}")
    print(f"清理前      : {rows_before} 行 × {cols_before} 列")
    print(f"丢尾行      : {dropped_rows} 行（证券代码不匹配 6 位.交易所）")
    print(f"丢空列      : {len(dropped_cols)} 列 {dropped_cols}")
    print(f"清理后      : {len(cleaned)} 行 × {cleaned.shape[1]} 列")
    print(f"输出 CSV    : {OUT_CSV.relative_to(REPO)}")
    print(f"映射表      : {OUT_MAP.relative_to(REPO)}")
    print(f"保留 -- 的列: {int((cleaned.astype(str) == '--').any().sum())} 列")


if __name__ == "__main__":
    main()
