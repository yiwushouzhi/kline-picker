"""K线形态 DSL：JSON 条件解析、编译与逐 bar 求值。

DSL 示例（阳包阴）::
    {
      "name": "阳包阴",
      "rules": [
        {"offset": -1, "expr": "close < open"},
        {"offset": 0,  "expr": "close > open and close >= ref(open,-1) and open <= ref(close,-1)"}
      ]
    }

语义：offset=0 为形态最后一根K线（命中日），负数为向前。
表达式中可用字段: open/high/low/close/volume/amount/pct(涨跌幅%)/turn(换手率%)/ma5/ma10/ma20/ma30
可用函数: ref(name,k) 取相对当前bar第k根(k为负向前)的值; min/max/abs
"""
from __future__ import annotations

import ast
import math
import re

import numpy as np

FIELDS = ("open", "high", "low", "close", "volume", "amount",
          "pct", "turn", "ma5", "ma10", "ma20", "ma30")

_REF_RE = re.compile(r"ref\(\s*['\"]?(\w+)['\"]?\s*,\s*(-?\d+)\s*\)")


class _RefRewriter(ast.NodeTransformer):
    """把 ref(close,-1) 中的裸字段名改写成字符串常量，便于运行期查表。"""

    def visit_Call(self, node):  # noqa: N802
        self.generic_visit(node)
        if isinstance(node.func, ast.Name) and node.func.id == "ref" and node.args:
            first = node.args[0]
            if isinstance(first, ast.Name):
                node.args[0] = ast.copy_location(ast.Constant(value=first.id), first)
        return node


def _compile_expr(expr: str, tag: str):
    tree = ast.parse(expr, mode="eval")
    tree = _RefRewriter().visit(tree)
    ast.fix_missing_locations(tree)
    return compile(tree, tag, "eval")


class PatternError(ValueError):
    pass


class CompiledPattern:
    def __init__(self, spec: dict):
        if not isinstance(spec, dict):
            raise PatternError("条件必须是 JSON 对象")
        self.name = str(spec.get("name") or "未命名形态")
        rules = spec.get("rules")
        if not isinstance(rules, list) or not rules:
            raise PatternError("缺少 rules 数组")
        self.rules = []
        for i, r in enumerate(rules):
            try:
                offset = int(r["offset"])
                expr = str(r["expr"]).strip()
            except (KeyError, TypeError, ValueError):
                raise PatternError(f"第 {i+1} 条规则缺少 offset/expr")
            if offset > 0:
                raise PatternError(f"第 {i+1} 条规则 offset 必须 <= 0（0=命中日，负数向前）")
            if not expr:
                raise PatternError(f"第 {i+1} 条规则 expr 为空")
            for fname, _k in _REF_RE.findall(expr):
                if fname not in FIELDS:
                    raise PatternError(f"第 {i+1} 条规则 ref 引用了未知字段: {fname}")
            try:
                code = _compile_expr(expr, f"<rule{i}>")
            except SyntaxError as e:
                raise PatternError(f"第 {i+1} 条规则语法错误: {e}")
            self.rules.append({"offset": offset, "expr": expr, "code": code})

    # ---- 计算所需的历史K线数量 ----
    def lookback_bars(self) -> int:
        max_off = max(-r["offset"] for r in self.rules)
        max_ref = 0
        ma_need = 0
        for r in self.rules:
            for _f, k in _REF_RE.findall(r["expr"]):
                max_ref = max(max_ref, -int(k))
            for w in (5, 10, 20, 30):
                if f"ma{w}" in r["expr"]:
                    ma_need = max(ma_need, w)
        return max_off + max_ref + ma_need + 2


def _eval_at(arrays: dict, p: int, code) -> bool:
    n = len(arrays["close"])

    def ref(name, k):
        j = p + int(k)
        if j < 0 or j >= n:
            return float("nan")
        v = arrays[name][j]
        return float(v) if v is not None else float("nan")

    ctx = {name: float(arrays[name][p]) for name in FIELDS if name in arrays}
    ctx.update({"ref": ref, "min": min, "max": max, "abs": abs,
                "nan": float("nan")})
    try:
        v = eval(code, {"__builtins__": {}}, ctx)  # noqa: S307
    except (NameError, TypeError, KeyError) as e:
        raise PatternError(f"表达式求值失败: {e}")
    except Exception:  # noqa: BLE001 (除零等视为不满足)
        return False
    try:
        return bool(v) and not (isinstance(v, float) and math.isnan(v))
    except TypeError:
        return False


def match_at(arrays: dict, date_pos: dict, date: str, pattern: CompiledPattern) -> bool:
    """判断某股票在 date(YYYY-MM-DD) 是否命中形态。arrays 为列->numpy数组。"""
    p0 = date_pos.get(date)
    if p0 is None:
        return False
    n = len(arrays["close"])
    for r in pattern.rules:
        p = p0 + r["offset"]
        if p < 0 or p >= n:
            return False
        if not _eval_at(arrays, p, r["code"]):
            return False
    return True


def df_to_arrays(df) -> dict:
    out = {}
    for name in FIELDS:
        if name in df.columns:
            out[name] = df[name].to_numpy(dtype=float)
    out["dates"] = list(df["date"])
    return out


def date_positions(dates: list) -> dict:
    return {d: i for i, d in enumerate(dates)}


def trading_days_in_range(dates: list, start: str, end: str) -> list:
    return [d for d in dates if start <= d <= end]


if __name__ == "__main__":
    spec = {
        "name": "阳包阴",
        "rules": [
            {"offset": -1, "expr": "close < open"},
            {"offset": 0, "expr": "close > open and close >= ref(open,-1) and open <= ref(close,-1)"},
        ],
    }
    pat = CompiledPattern(spec)
    print("lookback:", pat.lookback_bars())

    import data
    df = data.add_ma(data.fetch_kline("600000", "20250101", "20500101"))
    arrays = df_to_arrays(df)
    pos = date_positions(arrays["dates"])
    hits = [d for d in trading_days_in_range(arrays["dates"], "2025-06-01", "2026-11-07")
            if match_at(arrays, pos, d, pat)]
    print("浦发银行 阳包阴 命中:", hits[:10], f"共{len(hits)}次")
