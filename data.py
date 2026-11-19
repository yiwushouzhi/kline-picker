"""股票数据获取与本地缓存（东方财富公开接口）。"""
from __future__ import annotations

import hashlib
import json
import os
import pickle
import random
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import pandas as pd
import requests

def app_data_dir() -> str:
    """可写数据目录：打包安装后放用户本地目录，源码运行时保持项目目录。"""
    if getattr(sys, "frozen", False):
        base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
        base = os.path.join(base, "KLinePicker")
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    os.makedirs(base, exist_ok=True)
    return base


CACHE_DIR = os.path.join(app_data_dir(), "cache")
os.makedirs(CACHE_DIR, exist_ok=True)

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/120.0 Safari/537.36",
    "Referer": "https://quote.eastmoney.com/",
}

_PUSH2_HOSTS = ["push2.eastmoney.com", "20.push2.eastmoney.com", "80.push2.eastmoney.com"]
_HIS_HOSTS = ["push2his.eastmoney.com", "21.push2his.eastmoney.com", "46.push2his.eastmoney.com"]

KLINE_FIELDS = ["date", "open", "close", "high", "low", "volume",
                "amount", "amplitude", "pct", "change", "turnover"]

# 每线程复用一个连接（keep-alive），避免每次都做 TLS 握手：
# 实测并发吞吐从 1.7 次/秒提升到 70 次/秒，且不再大面积超时。
_thread_local = threading.local()
_source_lock = threading.Lock()
_source_state = {}  # name -> [连续失败数, 熔断截止时间]


def _source_available(name: str) -> bool:
    with _source_lock:
        _fails, until = _source_state.get(name, (0, 0.0))
        return time.monotonic() >= until


def _source_result(name: str, success: bool):
    """连续故障后熔断 30 秒，避免每只股票都重复等待失效接口。"""
    with _source_lock:
        fails, until = _source_state.get(name, (0, 0.0))
        if success:
            # 已在熔断期内的迟到成功不提前解除熔断，避免状态反复抖动。
            _source_state[name] = (0, until if until > time.monotonic()
                                   else 0.0)
            return
        fails += 1
        if fails >= 6:
            fails, until = 0, time.monotonic() + 30
        _source_state[name] = (fails, until)


def _session() -> requests.Session:
    s = getattr(_thread_local, "s", None)
    if s is None:
        s = requests.Session()
        s.headers.update(_HEADERS)
        adapter = requests.adapters.HTTPAdapter(
            pool_connections=8, pool_maxsize=32, max_retries=0)
        s.mount("https://", adapter)
        s.mount("http://", adapter)
        _thread_local.s = s
    return s


def _get_json(url: str, params: dict, timeout: float = 12.0) -> dict:
    resp = _session().get(url, params=params, timeout=(5.0, timeout))
    resp.raise_for_status()
    return resp.json()


def _fetch_clist_page(page: int, page_size: int = 100,
                      fields: str = "f12,f14") -> dict:
    """取一页股票列表；多主机 + 多轮重试，抵御偶发断连/限流。"""
    params = {"pn": page, "pz": page_size, "po": 1, "np": 1,
              "fltt": 2, "invt": 2, "fid": "f20",
              "fs": "m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23",
              "fields": fields}
    last_err = None
    for attempt in range(3):
        for host in _PUSH2_HOSTS:
            try:
                return _get_json(f"https://{host}/api/qt/clist/get", params)
            except Exception as e:  # noqa: BLE001
                last_err = e
        if attempt < 2:
            time.sleep(0.2 + 0.4 * attempt)
    raise RuntimeError(str(last_err))


def secid_of(code: str) -> str:
    """6 开头为上海(1)，其余为深圳(0)。"""
    return ("1." if code.startswith("6") else "0.") + code


def fetch_names(codes: list) -> dict:
    """批量查询代码->名称。"""
    out = {}
    for i in range(0, len(codes), 80):
        batch = codes[i:i + 80]
        params = {"fltt": 2, "invt": 2, "fields": "f12,f14",
                  "secids": ",".join(secid_of(c) for c in batch)}
        data = None
        for host in _PUSH2_HOSTS:
            try:
                data = _get_json(f"https://{host}/api/qt/ulist.np/get", params)
                break
            except Exception:  # noqa: BLE001
                continue
        if data is None:
            continue
        for item in (data.get("data") or {}).get("diff") or []:
            out[str(item.get("f12", ""))] = str(item.get("f14", ""))
    return out


def fetch_stock_list(top_n: int = 300) -> list[dict]:
    """按总市值降序取前 top_n 只沪深 A 股，返回 [{code, name}]。"""
    result = []
    page, page_size = 1, 100
    while len(result) < top_n:
        data = _fetch_clist_page(page, min(page_size, top_n - len(result)))
        diff = (data.get("data") or {}).get("diff") or []
        if not diff:
            break
        for item in diff:
            code, name = str(item.get("f12", "")), str(item.get("f14", ""))
            if code and code[0] in "036":
                result.append({"code": code, "name": name})
        page += 1
    return result[:top_n]


last_universe_stale = False      # True 表示上次取全A名单失败、用的是旧缓存


_SINA_LIST_URL = ("https://vip.stock.finance.sina.com.cn/quotes_service/api/"
                  "json_v2.php/Market_Center.getHQNodeData")


def _fetch_all_ashare_sina() -> list[dict]:
    """备用源：新浪行情中心全部A股列表（东财不可用时）。"""
    out, page = [], 1
    while page <= 80:
        params = {"page": page, "num": 100, "sort": "symbol", "asc": "1",
                  "node": "hs_a", "symbol": "", "_s_r_a": "page"}
        text, last_err = None, None
        for _attempt in range(2):
            try:
                r = requests.get(_SINA_LIST_URL, params=params,
                                 headers=_HEADERS, timeout=10)
                r.raise_for_status()
                text = r.text
                break
            except Exception as e:  # noqa: BLE001
                last_err = e
                time.sleep(0.4)
        if text is None:
            raise RuntimeError(str(last_err))
        codes = re.findall(r'"code"\s*:\s*"(\d{6})"', text)
        names = re.findall(r'"name"\s*:\s*"([^"]*)"', text)
        if not codes:
            break
        for code, name in zip(codes, names):
            if code[0] in "034689":
                try:
                    name = json.loads(f'"{name}"')
                except Exception:  # noqa: BLE001
                    pass
                out.append({"code": code, "name": name})
        if len(codes) < 100:
            break
        page += 1
        time.sleep(0.05)
    return out


def fetch_all_ashare(force: bool = False) -> list[dict]:
    """全部沪深A股列表，按日缓存（每天自动更新一次）。

    取数失败时退回本地旧名单（可能不是最新），并置 last_universe_stale=True。
    """
    global last_universe_stale
    last_universe_stale = False
    path = os.path.join(CACHE_DIR, "all_ashare_list.pkl")
    if not force and os.path.exists(path):
        try:
            mtime = datetime.fromtimestamp(os.path.getmtime(path))
            if mtime.date() == datetime.now().date():
                with open(path, "rb") as f:
                    return pickle.load(f)
        except Exception:  # noqa: BLE001
            pass
    result = []
    try:
        first = _fetch_clist_page(1)
        pages = [first]
        try:
            total = int((first.get("data") or {}).get("total") or 0)
        except (TypeError, ValueError):
            total = 0
        if total > 100:
            pages_n = (total + 99) // 100
            with ThreadPoolExecutor(max_workers=8) as pool:
                pages.extend(pool.map(_fetch_clist_page,
                                      range(2, pages_n + 1)))
        for data in pages:
            for item in (data.get("data") or {}).get("diff") or []:
                code, name = str(item.get("f12", "")), str(item.get("f14", ""))
                if code and code[0] in "034689":
                    result.append({"code": code, "name": name})
    except Exception as e:  # noqa: BLE001
        # 东财不可用 → 先用备用源（新浪）拉一份新鲜的
        try:
            alt = _fetch_all_ashare_sina()
            if len(alt) > 1000:
                try:
                    with open(path, "wb") as f:
                        pickle.dump(alt, f)
                except Exception:  # noqa: BLE001
                    pass
                return alt
        except Exception:  # noqa: BLE001
            pass
        # 备用源也失败 → 退回本地旧名单（可能不是最新）
        if os.path.exists(path):
            try:
                with open(path, "rb") as f:
                    cached = pickle.load(f)
                if cached:
                    last_universe_stale = True
                    return cached
            except Exception:  # noqa: BLE001
                pass
        raise RuntimeError(f"获取全部A股列表失败: {e}")
    try:
        with open(path, "wb") as f:
            pickle.dump(result, f)
    except Exception:  # noqa: BLE001
        pass
    return result


def filter_universe(stocks: list[dict], exclude: dict) -> list[dict]:
    """按类别剔除股票。exclude 键: st/star(科创)/chinext(创业)/bse(北证)/sh/sz。"""
    out = []
    for s in stocks:
        code, name = s["code"], s["name"]
        upper = name.upper()
        if exclude.get("st") and ("ST" in upper or "退" in name):
            continue
        if exclude.get("star") and code.startswith(("688", "689")):
            continue
        if exclude.get("chinext") and code.startswith(("300", "301", "302")):
            continue
        if exclude.get("bse") and code[0] in "489":
            continue
        if exclude.get("sh") and code.startswith("6"):
            continue
        if exclude.get("sz") and code.startswith("0"):
            continue
        out.append(s)
    return out


def _cache_path(code: str, beg: str, end: str) -> str:
    """旧版按日期区间命名的缓存路径（用于兼容已有缓存）。"""
    key = hashlib.md5(f"{code}_{beg}_{end}_qfq".encode()).hexdigest()
    return os.path.join(CACHE_DIR, f"{code}_{key}.pkl")


def _day_cache_path(code: str) -> str:
    return os.path.join(CACHE_DIR, f"{code}_day.pkl")


def _write_day_cache(path: str, df: pd.DataFrame):
    """原子写入，避免图表与筛选同时读写造成缓存损坏。"""
    tmp = f"{path}.{threading.get_ident()}.tmp"
    try:
        with open(tmp, "wb") as f:
            pickle.dump(df, f)
        os.replace(tmp, path)
    except Exception:  # noqa: BLE001
        try:
            os.remove(tmp)
        except OSError:
            pass


def read_day_cache(code: str) -> pd.DataFrame | None:
    """读取本地日K缓存（不校验日期），用于点击个股时先秒显已有数据。"""
    try:
        with open(_day_cache_path(code), "rb") as f:
            cached = pickle.load(f)
        if isinstance(cached, pd.DataFrame) and not cached.empty:
            return cached
    except (OSError, ValueError, EOFError, pickle.PickleError):
        pass
    return None


_SESSION_OPEN = (9, 10)     # 含集合竞价
_SESSION_CLOSE = (15, 5)    # 收盘后再留 5 分钟缓冲


def market_is_open(now: datetime | None = None) -> bool:
    """是否处于 A 股交易时段（简化：工作日 09:10-15:05）。

    未考虑法定节假日；节假日按“开市”处理只会导致多取几次数据，不影响正确性。
    """
    now = now or datetime.now()
    if now.weekday() >= 5:
        return False
    return _SESSION_OPEN <= (now.hour, now.minute) < _SESSION_CLOSE


def _today_bar_cache_stale(cached: pd.DataFrame, mtime: datetime,
                           now: datetime | None = None) -> bool:
    """当日 K 线未收盘时，缓存会把早盘的半根 K 线冻结一整天，必须判为过期。

    - 盘中（09:10-15:05）：缓存超过 60 秒即过期；缓存里没有当天 K 线也过期。
    - 收盘后：若缓存是当天收盘前写入的，里面的当日 K 线是半根，过期。
    """
    now = now or datetime.now()
    if now.weekday() >= 5 or cached is None or getattr(cached, "empty", True):
        return False
    today = now.strftime("%Y-%m-%d")
    try:
        last = str(cached["date"].iloc[-1])[:10]
    except Exception:  # noqa: BLE001
        return False
    open_t = now.replace(hour=_SESSION_OPEN[0], minute=_SESSION_OPEN[1],
                         second=0, microsecond=0)
    close_t = now.replace(hour=_SESSION_CLOSE[0], minute=_SESSION_CLOSE[1],
                          second=0, microsecond=0)
    if open_t <= now < close_t:
        if last != today:
            return True                       # 盘前写入，还没有当天K线
        return (now - mtime).total_seconds() > 60
    if now >= close_t and last == today and mtime < close_t:
        return True                           # 拿着盘中半根K线
    return False


def _slice_kline(df: pd.DataFrame, beg: str, end: str) -> pd.DataFrame:
    dates = df["date"].astype(str).str.replace("-", "", regex=False)
    return df[(dates >= beg) & (dates <= end)].reset_index(drop=True)


def _finish_day_df(df: pd.DataFrame, source: str) -> pd.DataFrame:
    """统一备用源字段；成交额/换手率缺失时保留 NaN。"""
    for col in ("open", "close", "high", "low", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna(subset=["open", "close", "high", "low"])
    prev = df["close"].shift(1)
    df["amount"] = float("nan")
    df["amplitude"] = (df["high"] - df["low"]) / prev * 100
    df["change"] = df["close"] - prev
    df["pct"] = df["change"] / prev * 100
    df["turnover"] = float("nan")
    df = df[KLINE_FIELDS].reset_index(drop=True)
    df.attrs["source"] = source
    return df


def _fetch_kline_tencent(code: str, beg: str, end: str) -> pd.DataFrame:
    """腾讯前复权日K备用源；东财断连时自动使用。"""
    symbol = ("sh" if code.startswith("6") else
              "bj" if code.startswith(("4", "8")) else "sz") + code
    start = datetime.strptime(beg, "%Y%m%d")
    finish = datetime.strptime(end, "%Y%m%d")
    all_rows = []
    cursor = finish
    # 腾讯单次最多 640 根；向前分段，保证全历史图也能使用备用源。
    for _page in range(20):
        params = {"param": (f"{symbol},day,{start:%Y-%m-%d},"
                            f"{cursor:%Y-%m-%d},640,qfq")}
        rows, last_err = None, None
        for attempt in range(2):
            try:
                resp = _session().get(
                    "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
                    params=params, timeout=(5, 10))
                resp.raise_for_status()
                node = (resp.json().get("data") or {}).get(symbol) or {}
                rows = node.get("qfqday") or node.get("day") or []
                break
            except Exception as e:  # noqa: BLE001
                last_err = e
                if attempt == 0:
                    time.sleep(0.2)
        if rows is None:
            raise RuntimeError(str(last_err))
        if not rows:
            if all_rows:
                break
            raise RuntimeError("腾讯日K返回空")
        all_rows = rows + all_rows
        if len(rows) < 640:
            break
        first = datetime.strptime(rows[0][0], "%Y-%m-%d")
        cursor = first - timedelta(days=1)
        if cursor < start:
            break

    unique = {r[0]: r for r in all_rows}
    rows = [unique[d] for d in sorted(unique)]
    df = pd.DataFrame([r[:6] for r in rows],
                      columns=["date", "open", "close", "high",
                               "low", "volume"])
    return _finish_day_df(df, "tencent")


def _fetch_kline_sina(code: str, beg: str, end: str) -> pd.DataFrame:
    """新浪日K第三备用源，用于分散腾讯的并发与限流。"""
    start = datetime.strptime(beg, "%Y%m%d")
    finish = datetime.strptime(end, "%Y%m%d")
    count = min(2400, max(320, int((finish - start).days * 0.8) + 20))
    params = {"symbol": _sina_symbol(code), "scale": 240,
              "ma": "no", "datalen": count}
    last_err = None
    for attempt in range(2):
        try:
            resp = _session().get(
                _SINA_MIN_URL, params=params,
                headers={"Referer": "https://finance.sina.com.cn/"},
                timeout=(5, 10))
            resp.raise_for_status()
            rows = resp.json()
            if not isinstance(rows, list) or not rows:
                raise RuntimeError("新浪日K返回空")
            df = pd.DataFrame({
                "date": [r["day"][:10] for r in rows],
                "open": [r["open"] for r in rows],
                "close": [r["close"] for r in rows],
                "high": [r["high"] for r in rows],
                "low": [r["low"] for r in rows],
                # 新浪成交量单位为股，东财/腾讯为手
                "volume": [float(r["volume"]) / 100 for r in rows],
            })
            df = _finish_day_df(df, "sina")
            if len(rows) >= 2400:
                df.attrs["partial"] = True
            return df
        except Exception as e:  # noqa: BLE001
            last_err = e
            if attempt == 0:
                time.sleep(0.2)
    raise RuntimeError(str(last_err))


def fetch_board_list(limit: int = 120) -> list[tuple[str, str]]:
    """获取东财行业板块列表 [(BK代码, 名称)]，失败返回空列表。

    板块K线用 secid = "90.BKxxxx" 获取（fetch_kline 传 secid 参数）。
    """
    params = {
        "pn": 1, "pz": limit, "po": 1, "np": 1, "fltt": 2, "invt": 2,
        "fid": "f3", "fs": "m:90 t:2", "fields": "f12,f14",
    }
    for host in random.sample(_PUSH2_HOSTS, len(_PUSH2_HOSTS)):
        try:
            payload = _get_json(f"https://{host}/api/qt/clist/get",
                                params, timeout=8)
            diff = (payload.get("data") or {}).get("diff") or []
            out = [(str(it.get("f12", "")), str(it.get("f14", "")))
                   for it in diff if it.get("f12")]
            if out:
                return out
        except Exception:  # noqa: BLE001
            continue
    return []


def fetch_kline(code: str, beg: str, end: str, force: bool = False,
                secid: str = "") -> pd.DataFrame:
    """获取前复权日K；东财失败自动切换腾讯，当天缓存可跨区间复用。

    secid 非空时直接使用该 secid（用于指数等代码无法按首位判断市场的品种）。
    """
    path = _day_cache_path(code)
    legacy = _cache_path(code, beg, end)
    if not force:
        for cached_path in (path, legacy):
            try:
                mtime = datetime.fromtimestamp(os.path.getmtime(cached_path))
                if mtime.date() != datetime.now().date():
                    continue
                with open(cached_path, "rb") as f:
                    cached = pickle.load(f)
                # 当日未收盘：早盘写入的半根K线不能复用一整天
                if _today_bar_cache_stale(cached, mtime):
                    continue
                if cached_path == legacy:
                    cached.attrs.update({"beg": beg, "end": end,
                                         "source": "eastmoney"})
                    _write_day_cache(path, cached)
                cached_beg = cached.attrs.get("beg", "")
                cached_end = cached.attrs.get("end", "")
                if cached_beg and beg >= cached_beg and end <= cached_end:
                    return _slice_kline(cached, beg, end)
            except (OSError, ValueError, EOFError, pickle.PickleError):
                continue

    params = {
        "secid": secid or secid_of(code),
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61",
        "klt": 101, "fqt": 1, "beg": beg, "end": end, "lmt": 10000,
    }
    payload, em_err = None, None
    if _source_available("eastmoney"):
        for attempt in range(2):
            for host in random.sample(_HIS_HOSTS, len(_HIS_HOSTS)):
                try:
                    payload = _get_json(
                        f"https://{host}/api/qt/stock/kline/get", params, timeout=8)
                    _source_result("eastmoney", True)
                    break
                except Exception as e:  # noqa: BLE001
                    em_err = e
                    _source_result("eastmoney", False)
                if not _source_available("eastmoney"):
                    break
            if payload is not None or not _source_available("eastmoney"):
                break
            time.sleep(0.2 * (attempt + 1))
    else:
        em_err = RuntimeError("主源暂时熔断")

    if payload is not None:
        klines = ((payload.get("data") or {}).get("klines")) or []
        df = pd.DataFrame([k.split(",") for k in klines], columns=KLINE_FIELDS)
        for col in KLINE_FIELDS[1:]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["open", "close", "high", "low"]
                       ).reset_index(drop=True)
        df.attrs["source"] = "eastmoney"
    else:
        # 按股票代码分散两个备用源，避免主源异常时同时打满单一平台。
        fallbacks = [("腾讯", "tencent", _fetch_kline_tencent),
                     ("新浪", "sina", _fetch_kline_sina)]
        if int(code[-2:]) % 2:
            fallbacks.reverse()
        errors = []
        for label, source_key, fetcher in fallbacks:
            if not _source_available(source_key):
                errors.append(f"{label}暂时熔断")
                continue
            try:
                df = fetcher(code, beg, end)
                _source_result(source_key, True)
                break
            except Exception as e:  # noqa: BLE001
                _source_result(source_key, False)
                errors.append(f"{label} {e}")
        else:
            raise RuntimeError(
                f"获取 {code} K线失败：东财 {em_err}；"
                + "；".join(errors))

    df.attrs.update({"beg": beg, "end": end})
    if not df.empty and not df.attrs.get("partial"):
        _write_day_cache(path, df)
    return _slice_kline(df, beg, end)


def _num(v):
    try:
        x = float(v)
        return None if x != x else x
    except (TypeError, ValueError):
        return None


def _fetch_today_bar_em(code: str) -> dict | None:
    """东财实时快照，用于补当日未生成的K线。"""
    if not _source_available("em_quote"):
        return None
    params = {"secid": secid_of(code), "fltt": 2, "invt": 2,
              "fields": "f43,f44,f45,f46,f47,f48,f60,f86,f168"}
    d = None
    for host in random.sample(_PUSH2_HOSTS, len(_PUSH2_HOSTS)):
        try:
            d = (_get_json(f"https://{host}/api/qt/stock/get",
                           params, timeout=6).get("data") or {})
            break
        except Exception:  # noqa: BLE001
            continue
    if d is None:
        _source_result("em_quote", False)
        return None
    _source_result("em_quote", True)
    price, prev = _num(d.get("f43")), _num(d.get("f60"))
    if not price or not prev:
        return None
    ts = _num(d.get("f86"))
    if ts:
        if datetime.fromtimestamp(ts).strftime("%Y-%m-%d") != _today_str():
            return None
    elif not market_is_open():
        return None
    return {"open": _num(d.get("f46")) or price,
            "high": _num(d.get("f44")) or price,
            "low": _num(d.get("f45")) or price,
            "close": price, "prev": prev,
            "volume": _num(d.get("f47")) or 0.0,
            "amount": _num(d.get("f48")),
            "turnover": _num(d.get("f168"))}


def _fetch_today_bar_sina(code: str) -> dict | None:
    """新浪实时快照（东财不可用时）。"""
    if not _source_available("sina"):
        return None
    try:
        resp = _session().get(
            f"https://hq.sinajs.cn/list={_sina_symbol(code)}",
            headers={"Referer": "https://finance.sina.com.cn/"},
            timeout=(4, 8))
        resp.raise_for_status()
        text = resp.content.decode("gbk", "ignore")
        body = text.split('="', 1)[1].split('"', 1)[0]
        f = body.split(",")
        if len(f) < 32 or not f[0] or f[30] != _today_str():
            return None
        price, prev = _num(f[3]), _num(f[2])
        if not price or not prev:
            return None
        _source_result("sina", True)
        return {"open": _num(f[1]) or price,
                "high": _num(f[4]) or price,
                "low": _num(f[5]) or price,
                "close": price, "prev": prev,
                "volume": (_num(f[8]) or 0.0) / 100,
                "amount": _num(f[9]), "turnover": None}
    except Exception:  # noqa: BLE001
        _source_result("sina", False)
        return None


def _today_str() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def ensure_today_bar(df: pd.DataFrame, code: str, end: str = "") -> pd.DataFrame:
    """数据源还没给出当日K线时，用实时行情补一根（不写入缓存）。

    仅当筛选/看图的结束日就是今天时生效，避免把今天的数据拼进历史查询。
    """
    if df is None or df.empty or not end:
        return df
    if str(end).replace("-", "")[:8] != datetime.now().strftime("%Y%m%d"):
        return df
    today = _today_str()
    if str(df["date"].iloc[-1])[:10] >= today:
        return df
    bar = _fetch_today_bar_em(code) or _fetch_today_bar_sina(code)
    if not bar:
        return df
    prev = bar["prev"]
    last_close = float(df["close"].iloc[-1])
    # 日K是前复权价，实时价不复权：按最后一根收盘价缩放，保持曲线连续
    ratio = last_close / prev if prev > 0 and last_close > 0 else 1.0
    row = {
        "date": today,
        "open": bar["open"] * ratio,
        "close": bar["close"] * ratio,
        "high": bar["high"] * ratio,
        "low": bar["low"] * ratio,
        "volume": bar["volume"],
        "amount": float("nan") if bar["amount"] is None else bar["amount"],
        "amplitude": (bar["high"] - bar["low"]) / prev * 100,
        "pct": (bar["close"] - prev) / prev * 100,
        "change": (bar["close"] - prev) * ratio,
        "turnover": (float("nan") if bar["turnover"] is None
                     else bar["turnover"]),
    }
    out = pd.concat([df, pd.DataFrame([row], columns=KLINE_FIELDS)],
                    ignore_index=True)
    out.attrs.update(df.attrs)
    out.attrs["partial"] = True
    return out


def add_ma(df: pd.DataFrame, windows=(5, 10, 20, 30)) -> pd.DataFrame:
    for w in windows:
        df[f"ma{w}"] = df["close"].rolling(w).mean()
    return df


def default_beg_for(range_start: str, lookback_bars: int) -> str:
    """根据筛选起始日与所需回看K线数，换算出足够提前量的数据起始日(YYYYMMDD)。"""
    d = datetime.strptime(range_start, "%Y-%m-%d")
    d -= timedelta(days=int((lookback_bars + 15) * 2.2))
    return d.strftime("%Y%m%d")


MIN_FIELDS = ["date", "open", "close", "high", "low", "volume", "amount"]

_SINA_MIN_URL = ("https://money.finance.sina.com.cn/quotes_service/api/"
                 "json_v2.php/CN_MarketData.getKLineData")


def _sina_symbol(code: str) -> str:
    """新浪 symbol：sh/sz/bj 前缀。覆盖沪深北主板+创业板+科创板+ETF。"""
    c = code.strip()
    if c.startswith(("5", "6", "9")):
        return "sh" + c
    if c.startswith(("4", "8")):
        return "bj" + c
    return "sz" + c  # 0/2/3 开头深市（含创业板 300/301/302）


def _fetch_kline_min_sina(code: str, klt: int) -> pd.DataFrame:
    """新浪分钟K线回退源。klt: 1/5。

    新浪分钟能提供比东财更长的历史（5分钟约 10 周 / 2400 条），
    但只有 date/open/close/high/low/volume 六个字段。
    """
    scale = 5 if klt >= 5 else 1
    path = os.path.join(CACHE_DIR, f"{code}_m{klt}_sina.pkl")
    df = None
    try:
        if os.path.exists(path):
            mtime = datetime.fromtimestamp(os.path.getmtime(path))
            if (datetime.now() - mtime).total_seconds() < 6 * 3600:
                with open(path, "rb") as f:
                    df = pickle.load(f)
    except Exception:  # noqa: BLE001
        df = None
    if df is None:
        params = {"symbol": _sina_symbol(code), "scale": scale,
                  "ma": "no", "datalen": "2400"}
        text = None
        for attempt in range(2):
            try:
                text = _session().get(_SINA_MIN_URL, params=params,
                                      headers=_HEADERS, timeout=(5, 12)).text
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.2 + 0.3 * attempt)
        if not text:
            raise RuntimeError("新浪分钟接口无响应")
        try:
            rows = json.loads(text)
        except json.JSONDecodeError as e:
            raise RuntimeError(f"新浪分钟解析失败: {e}") from e
        if not isinstance(rows, list) or not rows:
            raise RuntimeError("新浪分钟返回空")
        out = []
        for r in rows:
            d = str(r.get("day", ""))
            if len(d) == 10:
                d += " 09:30"
            out.append({
                "date": d,
                "open": float(r.get("open", 0)),
                "close": float(r.get("close", 0)),
                "high": float(r.get("high", 0)),
                "low": float(r.get("low", 0)),
                "volume": float(r.get("volume", 0)),
            })
        df = pd.DataFrame(out)
        df = df.dropna(subset=["open", "close", "high", "low"]
                       ).reset_index(drop=True)
        # 新浪缺 amount → 补全 NaN，保证下游 chart.py 的 None 检查
        if "amount" not in df.columns:
            df["amount"] = float("nan")
        if not df.empty:
            try:
                with open(path, "wb") as f:
                    pickle.dump(df, f)
            except Exception:  # noqa: BLE001
                pass
    return df.sort_values("date").reset_index(drop=True)


def fetch_kline_min(code: str, klt: int = 5, beg: str = "", end: str = "",
                    force: bool = False, extend_hist: bool = False) -> pd.DataFrame:
    """获取分钟K线。klt: 1/5。

    东财分钟接口硬限制：1 分钟约当日、5 分钟约 7 周；
    需要更长历史时传 extend_hist=True，会自动拼接新浪回退源（5 分钟约 10 周）。
    1 分钟两边都只有当日，不做拼接。
    """
    key = hashlib.md5(f"{code}_{klt}_full".encode()).hexdigest()
    path = os.path.join(CACHE_DIR, f"{code}_m{klt}_{key}.pkl")
    df = None
    if not force and os.path.exists(path):
        try:
            # 分钟数据按天缓存：1分钟（当日）短、5分钟（历史）长
            mtime = datetime.fromtimestamp(os.path.getmtime(path))
            days = 1 if klt < 5 else 2
            if (datetime.now() - mtime).total_seconds() < days * 86400:
                with open(path, "rb") as f:
                    df = pickle.load(f)
        except Exception:  # noqa: BLE001
            df = None
    if df is None:
        params = {
            "secid": secid_of(code),
            "fields1": "f1,f2,f3,f4,f5,f6",
            "fields2": "f51,f52,f53,f54,f55,f56,f57",
            "klt": klt, "fqt": 1, "beg": "0", "end": "20500000", "lmt": 100000,
        }
        data, last_err = None, None
        for attempt in range(3):
            for host in random.sample(_HIS_HOSTS, len(_HIS_HOSTS)):
                try:
                    data = _get_json(f"https://{host}/api/qt/stock/kline/get",
                                     params)
                    break
                except Exception as e:  # noqa: BLE001
                    last_err = e
            if data is not None:
                break
            time.sleep(0.25 * (attempt + 1))
        if data is None:
            if extend_hist and klt >= 5:
                # 东财全挂 → 直接回退新浪
                return _fetch_kline_min_sina(code, klt)
            raise RuntimeError(f"获取 {code} {klt}分钟K线失败: {last_err}")
        klines = ((data.get("data") or {}).get("klines")) or []
        rows = [k.split(",") for k in klines]
        df = pd.DataFrame(rows, columns=MIN_FIELDS)
        for col in MIN_FIELDS[1:]:
            df[col] = pd.to_numeric(df[col], errors="coerce")
        df = df.dropna(subset=["open", "close", "high", "low"]
                       ).reset_index(drop=True)

        # 历史深度不足 → 拼接新浪回退源（5分钟有效）
        if (extend_hist and klt >= 5 and not df.empty
                and df.iloc[0]["date"] > (beg[:16] if beg else "0000-00-00")):
            try:
                sina = _fetch_kline_min_sina(code, klt)
                if not sina.empty:
                    merged = pd.concat([sina, df], ignore_index=True,
                                       sort=False)
                    merged = merged.drop_duplicates(subset=["date"],
                                                     keep="last")
                    merged = merged.sort_values("date").reset_index(drop=True)
                    # 新浪缺 amount，东财已有 amount 的行保留原值
                    merged["amount"] = merged["amount"].ffill()
                    df = merged
            except Exception:  # noqa: BLE001
                pass  # 新浪也挂了 → 至少还有东财的近 7 周

        if not df.empty:
            try:
                with open(path, "wb") as f:
                    pickle.dump(df, f)
            except Exception:  # noqa: BLE001
                pass
    # 本地按区间过滤
    if beg:
        df = df[df["date"] >= beg[:16]]
    if end:
        df = df[df["date"] <= end[:16]]
    return df.reset_index(drop=True)


if __name__ == "__main__":
    stocks = fetch_stock_list(10)
    print(stocks)
    df = fetch_kline("600000", "20250101", "20500101")
    print(add_ma(df).tail(3))
