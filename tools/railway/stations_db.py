"""车站数据管理：从 12306 抓基础信息，并按需补全经纬度。

数据文件：data/stations.json
格式（以电报码为 key）：
{
  "VAP": {
    "name": "北京北",
    "city": "北京",
    "pinyin": "beijingbei",
    "short": "bjb",
    "lon": 116.353, "lat": 39.949,    # WGS84，可选
    "geo_source": "amap" / "osm" / "manual" / null
  },
  ...
}
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from typing import Optional, Iterable

import requests
import urllib3

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


DATA_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
STATIONS_PATH = os.path.join(DATA_DIR, "stations.json")

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"

# 12306 入口与车站数据 URL
URL_INDEX = "https://www.12306.cn/index/"
URL_LEFT_TICKET_INIT = "https://kyfw.12306.cn/otn/leftTicket/init"
URL_STATION_NAME_JS = "https://kyfw.12306.cn/otn/resources/js/framework/station_name.js"


def make_session() -> requests.Session:
    """创建带通用 User-Agent 的 requests Session。"""
    s = requests.Session()
    s.headers.update({"User-Agent": UA})
    s.verify = False
    return s


def fetch_station_name_js(session: Optional[requests.Session] = None) -> str:
    """从 12306 拉取 station_name.js 原始文本。"""
    s = session or make_session()
    r = s.get(URL_STATION_NAME_JS, timeout=20)
    r.raise_for_status()
    return r.text


def parse_station_name_js(text: str) -> dict[str, dict]:
    """解析 12306 的 station_name.js 字符串。

    每条记录格式：@拼音码|站名|电报码|拼音|拼音简码|序号|城市码|城市|...
    """
    m = re.search(r"'([^']+)'", text)
    if not m:
        raise ValueError("未在 station_name.js 中找到 station_names 字符串")
    body = m.group(1)
    out: dict[str, dict] = {}
    for raw in body.split("@"):
        if not raw:
            continue
        parts = raw.split("|")
        # 至少应包含：短码|站名|电报码|拼音|短码|序号|...|城市
        if len(parts) < 8:
            continue
        short, name, code, pinyin = parts[0], parts[1], parts[2], parts[3]
        city = parts[7] if len(parts) > 7 else ""
        if not code or not name:
            continue
        out[code] = {
            "name": name,
            "city": city,
            "pinyin": pinyin,
            "short": short,
            "lon": None,
            "lat": None,
            "geo_source": None,
        }
    return out


def load_stations(path: str = STATIONS_PATH) -> dict[str, dict]:
    """加载本地 stations.json。文件不存在则返回空 dict。"""
    if not os.path.exists(path):
        return {}
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_stations(stations: dict[str, dict], path: str = STATIONS_PATH) -> None:
    """保存 stations.json，按电报码字典序排序，便于 diff。"""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    ordered = {k: stations[k] for k in sorted(stations.keys())}
    with open(path, "w", encoding="utf-8") as f:
        json.dump(ordered, f, ensure_ascii=False, indent=2)


def update_stations_from_12306(
    path: str = STATIONS_PATH,
    keep_coords: bool = True,
    session: Optional[requests.Session] = None,
) -> tuple[int, int, int]:
    """从 12306 拉最新车站基础数据，合并到本地 stations.json。

    Args:
        path: stations.json 路径。
        keep_coords: 是否保留本地已有坐标（推荐 True）。
        session: 可选的 requests.Session。

    Returns: (新增, 更新, 总数)
    """
    print(f"📥 拉取 12306 车站数据 ...", file=sys.stderr)
    text = fetch_station_name_js(session)
    fresh = parse_station_name_js(text)
    print(f"   解析得到 {len(fresh)} 条车站", file=sys.stderr)

    local = load_stations(path)
    added, updated = 0, 0
    for code, info in fresh.items():
        old = local.get(code)
        if not old:
            local[code] = info
            added += 1
            continue
        # 保留本地坐标，仅更新基础字段
        merged = dict(old)
        for k in ("name", "city", "pinyin", "short"):
            if info.get(k):
                merged[k] = info[k]
        if not keep_coords:
            merged["lon"] = info.get("lon")
            merged["lat"] = info.get("lat")
            merged["geo_source"] = info.get("geo_source")
        if merged != old:
            local[code] = merged
            updated += 1
    save_stations(local, path)
    print(f"   ✅ 新增 {added} / 更新 {updated} / 总计 {len(local)}", file=sys.stderr)
    return added, updated, len(local)


# ===== 查找辅助 =====

def find_station_by_name(stations: dict[str, dict], name: str) -> list[tuple[str, dict]]:
    """按站名（精确或模糊）查找车站，返回 [(电报码, info), ...]。

    优先精确匹配，找不到则按"包含"模糊匹配。
    """
    name = (name or "").strip()
    if not name:
        return []
    exact = [(c, s) for c, s in stations.items() if s.get("name") == name]
    if exact:
        return exact
    # 去掉常见后缀再试
    for suffix in ("站",):
        if name.endswith(suffix):
            n2 = name[: -len(suffix)]
            exact = [(c, s) for c, s in stations.items() if s.get("name") == n2]
            if exact:
                return exact
    return [(c, s) for c, s in stations.items() if name in s.get("name", "")]


def find_station_by_code(stations: dict[str, dict], code: str) -> Optional[dict]:
    return stations.get((code or "").upper())


# ===== CLI =====

def _cli():
    import argparse

    parser = argparse.ArgumentParser(description="车站数据管理工具")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_update = sub.add_parser("update", help="从 12306 抓取并更新 stations.json")
    p_update.add_argument(
        "--reset-coords",
        action="store_true",
        help="同时清空本地坐标（默认保留本地坐标）",
    )

    p_show = sub.add_parser("show", help="按电报码或站名查询车站")
    p_show.add_argument("query", help="电报码（如 VNP）或站名（如 北京南）")

    p_stat = sub.add_parser("stat", help="统计 stations.json 概况")

    args = parser.parse_args()

    if args.cmd == "update":
        update_stations_from_12306(keep_coords=not args.reset_coords)
    elif args.cmd == "show":
        st = load_stations()
        # 先按电报码
        info = find_station_by_code(st, args.query)
        if info:
            print(json.dumps({args.query.upper(): info}, ensure_ascii=False, indent=2))
            return
        # 再按站名
        results = find_station_by_name(st, args.query)
        if not results:
            print("（无匹配）")
            return
        print(json.dumps({c: s for c, s in results[:20]}, ensure_ascii=False, indent=2))
        if len(results) > 20:
            print(f"... 共 {len(results)} 条，仅显示前 20 条")
    elif args.cmd == "stat":
        st = load_stations()
        with_coord = sum(1 for s in st.values() if s.get("lon") is not None)
        sources: dict[str, int] = {}
        for s in st.values():
            src = s.get("geo_source") or "unknown" if s.get("lon") is not None else None
            if src:
                sources[src] = sources.get(src, 0) + 1
        print(f"📊 stations.json 概况")
        print(f"   总计: {len(st)}")
        print(f"   含坐标: {with_coord} ({with_coord / max(1, len(st)) * 100:.1f}%)")
        print(f"   坐标来源分布: {sources}")


if __name__ == "__main__":
    _cli()
