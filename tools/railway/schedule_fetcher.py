"""12306 车次时刻表抓取与本地缓存。

实现路径（参考 Joooook/12306-mcp）：
    1. https://search.12306.cn/search/v1/train/search?keyword=G1&date=20260408
       → 返回 { train_no: "24000000G10L", from_station: "北京南", to_station: "上海虹桥", ... }
    2. 拉取 cookie：访问 https://kyfw.12306.cn/otn/leftTicket/init
    3. https://kyfw.12306.cn/otn/queryTrainInfo/query?leftTicketDTO.train_no=...&leftTicketDTO.train_date=...&rand_code=
       → 返回每站 station_name / arrive_time / start_time / arrive_day_diff / running_time

输出格式（schedule JSON，本工具内部约定）：
{
  "train_code": "G1",
  "train_no":   "24000000G10L",
  "depart_date":"2026-04-08",       # 始发日期（本地）
  "from":       "北京南",
  "to":         "上海虹桥",
  "fetched_at": "2026-04-20T12:30:00+08:00",
  "stations": [
     {
       "no":1, "name":"北京南",
       "arrive": null,    # 始发站无到达
       "start":  "06:30",
       "day":    0,        # 相对始发日期的天数差
       "running":"00:00"   # 累计运行时间
     },
     ...
  ]
}
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests
import urllib3

from stations_db import (
    DATA_DIR,
    make_session,
    load_stations,
    find_station_by_name,
)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


CACHE_DIR = os.path.join(DATA_DIR, "cache")

URL_INDEX = "https://www.12306.cn/index/"
URL_LEFT_TICKET_INIT = "https://kyfw.12306.cn/otn/leftTicket/init"
URL_TRAIN_SEARCH = "https://search.12306.cn/search/v1/train/search"
URL_QUERY_TRAIN_INFO = "https://kyfw.12306.cn/otn/queryTrainInfo/query"


def _local_now_str() -> str:
    """带本地时区的 ISO 时间字符串。"""
    return datetime.now().astimezone().isoformat(timespec="seconds")


def warmup_session(session: Optional[requests.Session] = None) -> requests.Session:
    """预访问 12306 入口拿到必要的 cookie。"""
    s = session or make_session()
    s.get(URL_INDEX, timeout=15)
    s.get(URL_LEFT_TICKET_INIT, timeout=15)
    return s


def search_train(
    train_code: str,
    depart_date: str,
    session: Optional[requests.Session] = None,
) -> Optional[dict]:
    """按车次代码（G1 / D123 / Z6 等）查询，返回首条匹配。

    Args:
        train_code: 例如 "G1"
        depart_date: 形如 "2026-04-08"
        session: 可选 session
    """
    s = session or make_session()
    date_compact = depart_date.replace("-", "")
    params = {"keyword": train_code, "date": date_compact}
    r = s.get(URL_TRAIN_SEARCH, params=params, timeout=15)
    if r.status_code != 200:
        raise RuntimeError(f"search HTTP {r.status_code}: {r.text[:200]}")
    data = r.json()
    rows = data.get("data") or []
    target = train_code.upper().strip()
    for row in rows:
        if (row.get("station_train_code") or "").upper() == target:
            return row
    return rows[0] if rows else None


def fetch_train_route(
    train_no: str,
    depart_date: str,
    session: Optional[requests.Session] = None,
) -> list[dict]:
    """按 12306 内部 train_no + 日期查询经停站列表。

    返回原始 list[dict]，每项包含 station_name / arrive_time / start_time / arrive_day_diff / running_time / station_no。
    """
    s = session or make_session()
    params = {
        "leftTicketDTO.train_no": train_no,
        "leftTicketDTO.train_date": depart_date,
        "rand_code": "",
    }
    r = s.get(URL_QUERY_TRAIN_INFO, params=params, timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"queryTrainInfo HTTP {r.status_code}")
    body = r.json()
    if not body.get("status"):
        raise RuntimeError(f"queryTrainInfo 失败: {body}")
    return (body.get("data") or {}).get("data") or []


def normalize_route(rows: list[dict]) -> list[dict]:
    """把 12306 原始字段精简为本工具的 schedule 格式。"""
    out: list[dict] = []
    for r in rows:
        out.append(
            {
                "no": int(r.get("station_no") or 0) or len(out) + 1,
                "name": r.get("station_name", "").strip(),
                "arrive": r.get("arrive_time") if r.get("arrive_time") not in {"----", ""} else None,
                "start": r.get("start_time") if r.get("start_time") not in {"----", ""} else None,
                "day": int(r.get("arrive_day_diff") or 0),
                "running": r.get("running_time", ""),
            }
        )
    return out


def cache_path_for(train_code: str, depart_date: str) -> str:
    """生成时刻表缓存文件路径。"""
    safe = re.sub(r"[^A-Za-z0-9]", "_", train_code)
    return os.path.join(CACHE_DIR, f"{safe}_{depart_date}.json")


def fetch_schedule(
    train_code: str,
    depart_date: str,
    use_cache: bool = True,
    save_cache: bool = True,
    session: Optional[requests.Session] = None,
) -> dict:
    """获取一个车次某日的时刻表，结果含本地缓存。

    Args:
        train_code: 车次代码（G1 / D123 / Z6 ...）
        depart_date: yyyy-MM-dd
        use_cache: True 时优先读本地缓存
        save_cache: True 时拉取后保存到本地缓存
    """
    train_code = train_code.upper().strip()
    cache_file = cache_path_for(train_code, depart_date)
    if use_cache and os.path.exists(cache_file):
        with open(cache_file, "r", encoding="utf-8") as f:
            return json.load(f)

    s = session or warmup_session()
    print(f"🔍 12306 搜索 {train_code} ({depart_date}) ...", file=sys.stderr)
    hit = search_train(train_code, depart_date, session=s)
    if not hit:
        raise RuntimeError(f"未在 12306 找到车次 {train_code} ({depart_date})")
    print(f"   命中 train_no={hit.get('train_no')}  {hit.get('from_station')} → {hit.get('to_station')}", file=sys.stderr)

    rows = fetch_train_route(hit["train_no"], depart_date, session=s)
    stations = normalize_route(rows)
    if not stations:
        raise RuntimeError(f"queryTrainInfo 返回空经停站，train_no={hit.get('train_no')}")
    print(f"   经停站 {len(stations)} 个", file=sys.stderr)

    schedule = {
        "train_code": train_code,
        "train_no": hit["train_no"],
        "depart_date": depart_date,
        "from": hit.get("from_station") or stations[0].get("name"),
        "to": hit.get("to_station") or stations[-1].get("name"),
        "fetched_at": _local_now_str(),
        "stations": stations,
    }
    if save_cache:
        os.makedirs(os.path.dirname(cache_file), exist_ok=True)
        with open(cache_file, "w", encoding="utf-8") as f:
            json.dump(schedule, f, ensure_ascii=False, indent=2)
        print(f"   💾 缓存到 {cache_file}", file=sys.stderr)
    return schedule


def attach_coords(schedule: dict, stations_db: Optional[dict[str, dict]] = None) -> tuple[int, list[str]]:
    """根据 stations.json 给 schedule 中每个站点附加 lon/lat。

    返回 (匹配数, 未匹配站名列表)
    """
    db = stations_db if stations_db is not None else load_stations()
    matched, missing = 0, []
    for st in schedule["stations"]:
        cands = find_station_by_name(db, st["name"])
        chosen = None
        for code, info in cands:
            if info.get("lon") is not None:
                chosen = (code, info)
                break
        if chosen:
            code, info = chosen
            st["telecode"] = code
            st["lon"] = info["lon"]
            st["lat"] = info["lat"]
            st["geo_source"] = info.get("geo_source")
            matched += 1
        else:
            missing.append(st["name"])
    return matched, missing


# ===== CLI =====

def _cli():
    import argparse

    parser = argparse.ArgumentParser(description="12306 车次时刻表抓取与缓存")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_fetch = sub.add_parser("fetch", help="按车次号+日期抓取时刻表（带本地缓存）")
    p_fetch.add_argument("train_code", help="车次代码，如 G1 / D123 / Z6")
    p_fetch.add_argument("depart_date", help="始发日期 yyyy-MM-dd")
    p_fetch.add_argument("--no-cache", action="store_true", help="忽略缓存强制重新拉取")
    p_fetch.add_argument("--with-coords", action="store_true", help="附加站点经纬度")
    p_fetch.add_argument("--out", help="额外输出到指定文件路径")

    p_show = sub.add_parser("show", help="查看本地缓存")
    p_show.add_argument("train_code")
    p_show.add_argument("depart_date")

    args = parser.parse_args()

    if args.cmd == "fetch":
        sch = fetch_schedule(
            args.train_code,
            args.depart_date,
            use_cache=not args.no_cache,
        )
        if args.with_coords:
            matched, missing = attach_coords(sch)
            print(f"📍 坐标匹配 {matched}/{len(sch['stations'])}", file=sys.stderr)
            if missing:
                print(f"   未匹配站: {missing}", file=sys.stderr)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as f:
                json.dump(sch, f, ensure_ascii=False, indent=2)
            print(f"💾 已写出 {args.out}", file=sys.stderr)
        print(json.dumps(sch, ensure_ascii=False, indent=2))
    elif args.cmd == "show":
        p = cache_path_for(args.train_code, args.depart_date)
        if not os.path.exists(p):
            print(f"无缓存: {p}", file=sys.stderr)
            sys.exit(1)
        with open(p, "r", encoding="utf-8") as f:
            sch = json.load(f)
        print(json.dumps(sch, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    _cli()
