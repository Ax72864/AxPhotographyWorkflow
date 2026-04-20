"""车站坐标补全：从 OSM Overpass API（首选）/ 高德地理编码（备选）补全 stations.json 中的经纬度。

设计目标：
- 默认零依赖（只用 requests），用 OSM Overpass 全国一次性拉取车站点 + 中文名匹配
- 高德 API 作为补充/兜底（用户可选）
- 坐标系统一为 WGS84
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from typing import Iterable, Optional

import requests
import urllib3

from stations_db import (
    DATA_DIR,
    STATIONS_PATH,
    load_stations,
    save_stations,
    make_session,
)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.fr/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
]
OVERPASS_UA = "ax-railway-tool/0.1 (https://github.com/-)"

# 中国大陆铁路车站 Overpass QL（含 halt/yard/junction，排除地铁等）
OVERPASS_QL_CN_STATIONS = """
[out:json][timeout:180];
(
  node["railway"~"^(station|halt)$"](18.0,73.5,53.6,135.1);
);
out body;
"""

# OSM 节点中可能携带中文名的字段优先级
NAME_FIELDS = ("name:zh", "name:zh-Hans", "name", "official_name", "alt_name")


# ===== 名称归一化 =====

_SUFFIX_RE = re.compile(r"[站場场]+$")
_BRACKETS_RE = re.compile(r"[（(].*?[)）]")


def normalize_station_name(name: str) -> str:
    """归一化站名以便和 12306 名字匹配。

    - 去掉末尾的 "站"/"場"/"场"
    - 去掉括号注释
    - 去掉空白
    """
    if not name:
        return ""
    n = _BRACKETS_RE.sub("", name)
    n = n.replace(" ", "").strip()
    n = _SUFFIX_RE.sub("", n)
    return n


def pick_osm_name(tags: dict) -> str:
    """从 OSM tags 中挑选最可能的中文名。"""
    for k in NAME_FIELDS:
        v = tags.get(k)
        if v and re.search(r"[\u4e00-\u9fff]", v):
            return v
    # 没有中文字符，退回到 name
    return tags.get("name", "")


def is_subway_like(tags: dict) -> bool:
    """判断 OSM 节点是否是地铁/有轨电车，需要排除。"""
    if tags.get("station") in {"subway", "light_rail", "monorail", "tram"}:
        return True
    if tags.get("subway") == "yes":
        return True
    if tags.get("light_rail") == "yes":
        return True
    if tags.get("railway") == "tram_stop":
        return True
    if tags.get("operator", "").find("地铁") >= 0:
        return True
    return False


# ===== Overpass 拉取 =====


def fetch_china_stations_via_overpass(
    timeout: int = 240,
    endpoints: Iterable[str] = OVERPASS_ENDPOINTS,
) -> list[dict]:
    """通过 Overpass API 拉取中国大陆所有铁路车站点。

    返回原始 OSM elements（list of dict），每个 element 含 lat/lon/tags。
    """
    headers = {"User-Agent": OVERPASS_UA}
    last_err: Optional[Exception] = None
    for url in endpoints:
        try:
            print(f"   尝试 Overpass 端点: {url}", file=sys.stderr)
            r = requests.post(
                url,
                data={"data": OVERPASS_QL_CN_STATIONS},
                headers=headers,
                timeout=timeout,
            )
            if r.status_code != 200:
                print(f"     ⚠️  status={r.status_code}", file=sys.stderr)
                continue
            data = r.json()
            elements = data.get("elements", [])
            print(f"     ✅ 获取 {len(elements)} 个节点", file=sys.stderr)
            return elements
        except Exception as e:
            print(f"     ⚠️  失败: {e}", file=sys.stderr)
            last_err = e
            continue
    raise RuntimeError(f"所有 Overpass 端点不可用，最后错误: {last_err}")


def build_osm_name_index(elements: list[dict]) -> dict[str, list[dict]]:
    """从 OSM elements 构建按归一化中文名到节点列表的索引。"""
    idx: dict[str, list[dict]] = {}
    for e in elements:
        if e.get("type") != "node":
            continue
        tags = e.get("tags", {})
        if is_subway_like(tags):
            continue
        name = pick_osm_name(tags)
        key = normalize_station_name(name)
        if not key:
            continue
        idx.setdefault(key, []).append(e)
    return idx


def update_coords_via_overpass(
    path: str = STATIONS_PATH,
    overwrite: bool = False,
    cache_path: Optional[str] = None,
) -> tuple[int, int, int]:
    """用 Overpass API 补全 stations.json 中的坐标。

    Args:
        path: stations.json 路径。
        overwrite: 是否覆盖已有坐标（默认仅补缺）。
        cache_path: OSM 数据缓存路径（默认 data/osm_stations_cn.json）。

    Returns: (匹配补全, 跳过, 仍未匹配)
    """
    cache_path = cache_path or os.path.join(DATA_DIR, "osm_stations_cn.json")
    elements: list[dict]
    if os.path.exists(cache_path) and not overwrite:
        print(f"📂 使用缓存的 OSM 车站数据: {cache_path}", file=sys.stderr)
        with open(cache_path, "r", encoding="utf-8") as f:
            elements = json.load(f)
    else:
        print(f"🌍 通过 Overpass API 拉取全国铁路车站点 ...", file=sys.stderr)
        elements = fetch_china_stations_via_overpass()
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        with open(cache_path, "w", encoding="utf-8") as f:
            json.dump(elements, f, ensure_ascii=False)
        print(f"   缓存到 {cache_path}", file=sys.stderr)

    name_idx = build_osm_name_index(elements)
    print(f"   OSM 索引含 {len(name_idx)} 个唯一站名", file=sys.stderr)

    stations = load_stations(path)
    matched, skipped, missing = 0, 0, 0
    ambiguous: list[tuple[str, str, int]] = []
    for code, info in stations.items():
        if info.get("lon") is not None and not overwrite:
            skipped += 1
            continue
        name_norm = normalize_station_name(info.get("name", ""))
        if not name_norm:
            missing += 1
            continue
        cands = name_idx.get(name_norm)
        if not cands:
            missing += 1
            continue
        # 多个同名节点：按城市过滤；仍多个则取第一个
        chosen = cands[0]
        if len(cands) > 1 and info.get("city"):
            city_norm = normalize_station_name(info["city"])
            for c in cands:
                addr_city = c.get("tags", {}).get("addr:city", "")
                if city_norm and city_norm in normalize_station_name(addr_city):
                    chosen = c
                    break
            else:
                ambiguous.append((code, info["name"], len(cands)))
        info["lon"] = float(chosen["lon"])
        info["lat"] = float(chosen["lat"])
        info["geo_source"] = "osm"
        matched += 1
    save_stations(stations, path)
    print(f"   ✅ 新匹配 {matched} / 跳过 {skipped} / 仍缺失 {missing}", file=sys.stderr)
    if ambiguous:
        print(f"   ⚠️  有 {len(ambiguous)} 个站名有多个 OSM 候选，已取第一项", file=sys.stderr)
        for c, n, k in ambiguous[:5]:
            print(f"     {c} {n} ({k} 个候选)", file=sys.stderr)
    return matched, skipped, missing


# ===== 高德地理编码（兜底） =====

URL_AMAP_GEO = "https://restapi.amap.com/v3/geocode/geo"


def amap_geocode(address: str, key: str, city: str = "", session: Optional[requests.Session] = None) -> Optional[tuple[float, float]]:
    """调用高德地理编码 API，返回 WGS84 坐标 (lon, lat)。

    注意：高德返回的是 GCJ02，需转 WGS84。
    """
    s = session or make_session()
    params = {"key": key, "address": address, "city": city, "output": "json"}
    try:
        r = s.get(URL_AMAP_GEO, params=params, timeout=15)
        data = r.json()
        if data.get("status") != "1":
            return None
        geos = data.get("geocodes") or []
        if not geos:
            return None
        loc = geos[0].get("location", "")
        if not loc:
            return None
        lon_s, lat_s = loc.split(",")
        gcj_lon, gcj_lat = float(lon_s), float(lat_s)
        return gcj02_to_wgs84(gcj_lon, gcj_lat)
    except Exception:
        return None


def update_coords_via_amap(
    key: str,
    path: str = STATIONS_PATH,
    overwrite: bool = False,
    delay: float = 0.05,
    limit: Optional[int] = None,
) -> tuple[int, int]:
    """用高德 API 补全 stations.json 缺失的坐标。

    Args:
        key: 高德 web 服务 key。
        path: stations.json 路径。
        overwrite: 是否覆盖已有坐标（默认仅补缺）。
        delay: 每次请求间隔秒数。
        limit: 最多处理的车站数（用于试跑）。
    """
    s = make_session()
    stations = load_stations(path)
    added, failed = 0, 0
    cnt = 0
    for code, info in stations.items():
        if info.get("lon") is not None and not overwrite:
            continue
        addr = info["name"] + "站"
        loc = amap_geocode(addr, key, city=info.get("city", ""), session=s)
        if loc is None and info.get("city"):
            loc = amap_geocode(info["name"], key, city=info["city"], session=s)
        if loc is None:
            failed += 1
            print(f"   ❌ {code} {info['name']} 未地理编码到结果", file=sys.stderr)
        else:
            info["lon"], info["lat"] = loc[0], loc[1]
            info["geo_source"] = "amap"
            added += 1
        cnt += 1
        if limit and cnt >= limit:
            break
        time.sleep(delay)
    save_stations(stations, path)
    print(f"   ✅ 高德补全 {added} / 失败 {failed}", file=sys.stderr)
    return added, failed


# ===== 坐标系转换 =====

def gcj02_to_wgs84(lon: float, lat: float) -> tuple[float, float]:
    """GCJ02（高德/腾讯）→ WGS84，与项目中其他工具保持一致。"""
    import math
    pi = 3.14159265358979324
    a = 6378245.0
    ee = 0.00669342162296594323

    def out_of_china(lo, la):
        return not (73.66 < lo < 135.05 and 3.86 < la < 53.55)

    def transform_lat(x, y):
        ret = -100.0 + 2.0 * x + 3.0 * y + 0.2 * y * y + 0.1 * x * y + 0.2 * math.sqrt(abs(x))
        ret += (20.0 * math.sin(6.0 * x * pi) + 20.0 * math.sin(2.0 * x * pi)) * 2.0 / 3.0
        ret += (20.0 * math.sin(y * pi) + 40.0 * math.sin(y / 3.0 * pi)) * 2.0 / 3.0
        ret += (160.0 * math.sin(y / 12.0 * pi) + 320 * math.sin(y * pi / 30.0)) * 2.0 / 3.0
        return ret

    def transform_lon(x, y):
        ret = 300.0 + x + 2.0 * y + 0.1 * x * x + 0.1 * x * y + 0.1 * math.sqrt(abs(x))
        ret += (20.0 * math.sin(6.0 * x * pi) + 20.0 * math.sin(2.0 * x * pi)) * 2.0 / 3.0
        ret += (20.0 * math.sin(x * pi) + 40.0 * math.sin(x / 3.0 * pi)) * 2.0 / 3.0
        ret += (150.0 * math.sin(x / 12.0 * pi) + 300.0 * math.sin(x / 30.0 * pi)) * 2.0 / 3.0
        return ret

    if out_of_china(lon, lat):
        return lon, lat
    dlat = transform_lat(lon - 105.0, lat - 35.0)
    dlon = transform_lon(lon - 105.0, lat - 35.0)
    radlat = lat / 180.0 * pi
    magic = 1 - ee * math.sin(radlat) ** 2
    sqrtmagic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((a * (1 - ee)) / (magic * sqrtmagic) * pi)
    dlon = (dlon * 180.0) / (a / sqrtmagic * math.cos(radlat) * pi)
    return lon - dlon, lat - dlat


# ===== CLI =====

def _cli():
    import argparse
    parser = argparse.ArgumentParser(description="车站坐标补全工具")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_osm = sub.add_parser("osm", help="用 OSM Overpass API 全国一次性补全坐标（推荐）")
    p_osm.add_argument("--overwrite", action="store_true", help="覆盖已有坐标")
    p_osm.add_argument("--refresh-cache", action="store_true", help="忽略 OSM 缓存重新拉取")

    p_amap = sub.add_parser("amap", help="用高德 API 补全坐标（需 key）")
    p_amap.add_argument("--key", required=True, help="高德 web 服务 key")
    p_amap.add_argument("--overwrite", action="store_true")
    p_amap.add_argument("--limit", type=int, help="只处理前 N 个，用于试跑")

    args = parser.parse_args()
    if args.cmd == "osm":
        cache_path = os.path.join(DATA_DIR, "osm_stations_cn.json")
        if args.refresh_cache and os.path.exists(cache_path):
            os.remove(cache_path)
        update_coords_via_overpass(overwrite=args.overwrite)
    elif args.cmd == "amap":
        update_coords_via_amap(key=args.key, overwrite=args.overwrite, limit=args.limit)


if __name__ == "__main__":
    _cli()
