"""筛选引擎：下载数据 + 逐股逐日匹配，在线程中运行。"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from PySide6.QtCore import QThread, Signal

import data
from dsl import (CompiledPattern, date_positions, df_to_arrays, match_at,
                 trading_days_in_range)


class ScreenerThread(QThread):
    pool_ready = Signal(int)                # 股票池数量确定
    progress = Signal(int, int, str)        # 完成数, 总数, 当前股票
    match_found = Signal(dict)              # {code,name,date}
    status = Signal(str)
    finished_ok = Signal(int)               # 命中总数
    failed = Signal(str)

    def __init__(self, pool_spec: dict, start: str, end: str,
                 pattern: CompiledPattern, force_refresh: bool = False,
                 max_matches: int = 5000, parent=None):
        super().__init__(parent)
        self.pool_spec = pool_spec          # {"mode":"top","n":300} | {"mode":"custom","codes":[...]}
        self.start_date = start             # YYYY-MM-DD（注意：不能用 self.start，会遮蔽 QThread.start）
        self.end = end
        self.pattern = pattern
        self.force_refresh = force_refresh
        self.max_matches = max_matches
        self._stop = False
        self._match_count = 0
        self.result_note = ""

    def stop(self):
        self._stop = True

    def _resolve_pool(self) -> list:
        if self.pool_spec.get("mode") == "custom":
            codes = self.pool_spec["codes"]
            names = data.fetch_names(codes)
            return [{"code": c, "name": names.get(c, c)} for c in codes]
        if self.pool_spec.get("mode") == "all":
            return data.filter_universe(data.fetch_all_ashare(),
                                        self.pool_spec.get("exclude") or {})
        return data.fetch_stock_list(int(self.pool_spec.get("n", 300)))

    def _fetch(self, stock: dict, beg: str, end: str):
        df = data.fetch_kline(stock["code"], beg, end, force=self.force_refresh)
        # 数据源还没生成当日K线时，用实时行情补一根，避免“当日”筛选全部为空
        df = data.ensure_today_bar(df, stock["code"], end)
        return stock, data.add_ma(df)

    def run(self):
        try:
            self._run()
        except Exception as e:  # noqa: BLE001
            self.failed.emit(str(e))

    def _run(self):
        self.status.emit("正在获取股票池…")
        stocks = self._resolve_pool()
        if (self.pool_spec.get("mode") == "all"
                and getattr(data, "last_universe_stale", False)):
            self.status.emit("网络异常，已改用上次缓存的股票列表（可能不是最新）")
        if not stocks:
            self.failed.emit("股票池为空")
            return
        total = len(stocks)
        self.pool_ready.emit(total)

        beg = data.default_beg_for(self.start_date, self.pattern.lookback_bars())
        fetch_end = datetime.now().strftime("%Y%m%d")  # 取到今天，便于空格查看后续
        done = fail = fallback = no_today = 0
        self.status.emit(f"开始筛选 {total} 只股票（数据起始 {beg}）…")

        # 8 并发兼顾速度与稳定性，避免高峰期限流造成时长大幅波动。
        pool = ThreadPoolExecutor(max_workers=8)
        futures = {pool.submit(self._fetch, s, beg, fetch_end): s for s in stocks}
        for fut in as_completed(futures):
            if self._stop:
                break
            stock = futures[fut]
            try:
                _s, df = fut.result()
            except Exception:  # noqa: BLE001
                fail += 1
                done += 1
                self.progress.emit(done, total,
                                   f"{stock['name']}（K线取数失败，已跳过）")
                continue
            if df.attrs.get("source") in ("tencent", "sina"):
                fallback += 1
            done += 1
            self.progress.emit(done, total, stock["name"])
            if df.empty:
                continue
            arrays = df_to_arrays(df)
            pos = date_positions(arrays["dates"])
            if (self.end == datetime.now().strftime("%Y-%m-%d")
                    and self.end not in pos):
                no_today += 1
            for d in trading_days_in_range(arrays["dates"], self.start_date, self.end):
                if self._stop:
                    break
                try:
                    hit = match_at(arrays, pos, d, self.pattern)
                except Exception as e:  # noqa: BLE001
                    pool.shutdown(wait=False, cancel_futures=True)
                    raise RuntimeError(f"条件表达式错误: {e}")
                if hit:
                    self._match_count += 1
                    self.match_found.emit({"code": stock["code"],
                                           "name": stock["name"], "date": d})
                    if self._match_count >= self.max_matches:
                        self._stop = True
                        self.status.emit(f"已达上限 {self.max_matches} 条，提前结束")
                        break
        if self._stop:
            # 立即停止：不再等待挂起的请求
            pool.shutdown(wait=False, cancel_futures=True)
            if self._match_count < self.max_matches:
                self.status.emit("已停止")
        else:
            pool.shutdown(wait=True)
            notes = []
            if (self.end == datetime.now().strftime("%Y-%m-%d")
                    and data.market_is_open()):
                notes.append("当日未收盘，命中为盘中数据，收盘后可能变化")
            if no_today:
                notes.append(f"{no_today} 只缺少当日K线")
            if fallback:
                notes.append(f"{fallback} 只使用备用源")
            if fail:
                notes.append(f"{fail} 只取数失败已跳过")
            self.result_note = ("（" + "，".join(notes) + "）"
                                if notes else "")
            self.status.emit("筛选完成" + self.result_note)
        self.finished_ok.emit(self._match_count)
