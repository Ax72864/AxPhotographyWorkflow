"""阶段 2：基于 OSM 铁路线的轨道吸附。

核心思路：
- 输入：带坐标的 schedule（来自 schedule_fetcher.attach_coords）
- 步骤：
    1. 计算覆盖所有车站的 bbox（含 buffer），按粒度取整以便缓存
    2. 通过 Overpass API 拉取该 bbox 内所有铁路 way（rail/light_rail/...，排除 service/spur 等）
    3. 把每个 way 的节点序列作为一段 LineString，构建无向图
       - 节点 = 端点（按经纬度量化为 hash key）
       - 边权 = 大圆距离
    4. 对每个车站找最近的图节点
    5. 相邻车站之间跑 Dijkstra 最短路径
    6. 沿路径采样轨迹点 + 按时刻表分段插值时间
- 缓存：bbox 取整后存为 data/cache/osm_railway_<key>.json
- 依赖：shapely networkx pyproj rtree（pyproject [snap] extras）
"""
from __future__ import annotations

import json
import math
import os
import sys
import time
from datetime import datetime, timedelta
from typing import Optional

import requests
import urllib3

import networkx as nx

from stations_db import DATA_DIR, make_session
from track_builder import (
    TZ_BEIJING,
    _hav_distance_m,
    parse_schedule_time,
    station_anchor_time,
)

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


CACHE_DIR = os.path.join(DATA_DIR, "cache")

OVERPASS_ENDPOINTS = [
    "https://overpass-api.de/api/interpreter",
    "https://overpass.kumi.systems/api/interpreter",
    "https://overpass.openstreetmap.fr/api/interpreter",
    "https://overpass.osm.ch/api/interpreter",
]
OVERPASS_UA = "ax-railway-tool/0.1 (https://github.com/-)"

# Overpass QL：取所有干线铁路 way，排除 service（站内/支线/编组场等）
# 同时排除 disused / abandoned
OVERPASS_QL_TEMPLATE = """[out:json][timeout:300];
(
  way["railway"~"^(rail|light_rail|narrow_gauge|monorail)$"]
     ["service"!~".*"]
     ["disused"!="yes"]
     ["abandoned"!="yes"]
     ({south},{west},{north},{east});
);
out geom;
"""


# ===== bbox 与缓存 =====

def compute_bbox(stations: list[dict], buffer_deg: float = 0.3) -> tuple[float, float, float, float]:
    """根据站点列表计算 (south, west, north, east) bbox，含缓冲。"""
    lats = [s["lat"] for s in stations if s.get("lat") is not None]
    lons = [s["lon"] for s in stations if s.get("lon") is not None]
    if not lats:
        raise ValueError("站点列表中没有任何坐标")
    return (
        min(lats) - buffer_deg,
        min(lons) - buffer_deg,
        max(lats) + buffer_deg,
        max(lons) + buffer_deg,
    )


def quantize_bbox(bbox: tuple[float, float, float, float], step: float = 0.5) -> tuple[float, float, float, float]:
    """把 bbox 的边界按 step 取整（向外），便于缓存命中。"""
    s, w, n, e = bbox
    return (
        math.floor(s / step) * step,
        math.floor(w / step) * step,
        math.ceil(n / step) * step,
        math.ceil(e / step) * step,
    )


def bbox_cache_path(bbox: tuple[float, float, float, float]) -> str:
    s, w, n, e = bbox
    name = f"osm_railway_{s:.2f}_{w:.2f}_{n:.2f}_{e:.2f}.json"
    return os.path.join(CACHE_DIR, name)


# ===== Overpass 拉取 =====


def fetch_railway_ways(
    bbox: tuple[float, float, float, float],
    timeout: int = 300,
    endpoints=OVERPASS_ENDPOINTS,
) -> list[dict]:
    """通过 Overpass API 获取 bbox 内所有干线铁路 way。"""
    south, west, north, east = bbox
    q = OVERPASS_QL_TEMPLATE.format(south=south, west=west, north=north, east=east)
    headers = {"User-Agent": OVERPASS_UA}
    last_err: Optional[Exception] = None
    for url in endpoints:
        try:
            print(f"   Overpass: {url}", file=sys.stderr)
            t0 = time.time()
            r = requests.post(url, data={"data": q}, headers=headers, timeout=timeout)
            dt = time.time() - t0
            if r.status_code != 200:
                print(f"     status={r.status_code}, head={r.text[:120]}", file=sys.stderr)
                continue
            data = r.json()
            elements = data.get("elements", [])
            print(f"     ✅ {len(elements)} ways  /  {len(r.text)/1024/1024:.1f} MB  /  {dt:.1f}s", file=sys.stderr)
            return elements
        except Exception as e:
            print(f"     失败: {e}", file=sys.stderr)
            last_err = e
            continue
    raise RuntimeError(f"所有 Overpass 端点失败：{last_err}")


def load_or_fetch_railway(
    bbox: tuple[float, float, float, float],
    refresh: bool = False,
) -> list[dict]:
    """加载或拉取铁路 way 数据，结果按 bbox 缓存。"""
    bbox_q = quantize_bbox(bbox)
    cache = bbox_cache_path(bbox_q)
    if not refresh and os.path.exists(cache):
        print(f"📂 使用缓存的 OSM 铁路数据: {cache}", file=sys.stderr)
        with open(cache, "r", encoding="utf-8") as f:
            return json.load(f)
    print(f"🌍 拉取 OSM 铁路数据 bbox={bbox_q} ...", file=sys.stderr)
    ways = fetch_railway_ways(bbox_q)
    os.makedirs(os.path.dirname(cache), exist_ok=True)
    with open(cache, "w", encoding="utf-8") as f:
        json.dump(ways, f, ensure_ascii=False)
    print(f"   💾 缓存到 {cache}", file=sys.stderr)
    return ways


# ===== 图构建 =====

def _qkey(lon: float, lat: float, q: float = 1e-5) -> tuple[int, int]:
    """把 (lon,lat) 量化为整数 key，便于做端点合并。q≈1e-5 度 ≈ 1.1m。"""
    return (round(lon / q), round(lat / q))


def build_graph(ways: list[dict]) -> tuple[nx.Graph, dict[tuple[int, int], tuple[float, float]]]:
    """从 OSM ways 构建无向加权图。

    每个 way 节点都成为图节点（相邻节点之间一条边），这样多条 way 在共享节点处自动连通。
    边 attrs：way_id / line / length_m / weight

    Returns:
        G: networkx.Graph，节点 = qkey，边 attrs 含 way_id / line / length_m
        node_pos: qkey → (lon, lat)
    """
    G = nx.Graph()
    node_pos: dict[tuple[int, int], tuple[float, float]] = {}
    for w in ways:
        geom = w.get("geometry") or []
        if len(geom) < 2:
            continue
        tags = w.get("tags", {})
        line_name = tags.get("name:zh") or tags.get("name") or ""
        way_id = w.get("id")
        prev_key = None
        prev_lon = prev_lat = 0.0
        for p in geom:
            lon = p["lon"]
            lat = p["lat"]
            k = _qkey(lon, lat)
            if k not in node_pos:
                node_pos[k] = (lon, lat)
            if prev_key is not None and k != prev_key:
                seg_len = _hav_distance_m(prev_lon, prev_lat, lon, lat)
                # 同一对节点已有边时，仅当新边更短才替换
                existing = G.get_edge_data(prev_key, k)
                if existing is None:
                    G.add_edge(
                        prev_key,
                        k,
                        way_id=way_id,
                        line=line_name,
                        length_m=seg_len,
                        weight=seg_len,
                    )
                elif seg_len < existing.get("length_m", float("inf")):
                    existing.update(
                        {"way_id": way_id, "line": line_name, "length_m": seg_len, "weight": seg_len}
                    )
            prev_key = k
            prev_lon = lon
            prev_lat = lat
    print(f"📐 图构建完成: 节点 {G.number_of_nodes()} / 边 {G.number_of_edges()}", file=sys.stderr)
    return G, node_pos


def find_nearest_node(
    G: nx.Graph,
    node_pos: dict[tuple[int, int], tuple[float, float]],
    lon: float,
    lat: float,
) -> tuple[Optional[tuple[int, int]], float]:
    """暴力扫描找最近节点（节点数 < 50万 时秒级）。返回 (node_key, distance_m)。"""
    best: Optional[tuple[int, int]] = None
    best_d = float("inf")
    for k, (nl, nt) in node_pos.items():
        d = _hav_distance_m(lon, lat, nl, nt)
        if d < best_d:
            best_d = d
            best = k
    return best, best_d


# ===== 路径吸附主流程 =====


def _path_coords_along_edges(
    G: nx.Graph,
    node_pos: dict[tuple[int, int], tuple[float, float]],
    path_nodes: list[tuple[int, int]],
) -> list[tuple[float, float]]:
    """把图节点路径转换成 (lon,lat) 列表。

    新版 build_graph 已让每个 way 节点都成为图节点，因此节点序列直接就是几何序列。
    """
    return [node_pos[n] for n in path_nodes if n in node_pos]


def _line_total_length_m(coords: list[tuple[float, float]]) -> float:
    L = 0.0
    for i in range(len(coords) - 1):
        L += _hav_distance_m(coords[i][0], coords[i][1], coords[i + 1][0], coords[i + 1][1])
    return L


def _resample_line(
    coords: list[tuple[float, float]],
    n_samples: int,
) -> list[tuple[float, float, float]]:
    """对一条折线按累计长度等距采样。返回 [(lon, lat, fraction[0..1]), ...]，含首尾。"""
    if not coords:
        return []
    if n_samples < 2:
        return [(coords[0][0], coords[0][1], 0.0)]
    cum = [0.0]
    for i in range(len(coords) - 1):
        cum.append(cum[-1] + _hav_distance_m(coords[i][0], coords[i][1], coords[i + 1][0], coords[i + 1][1]))
    total = cum[-1]
    if total <= 0:
        return [(coords[0][0], coords[0][1], 0.0)]
    out: list[tuple[float, float, float]] = []
    j = 0
    for k in range(n_samples):
        target = total * k / (n_samples - 1)
        while j + 1 < len(cum) and cum[j + 1] < target:
            j += 1
        if j + 1 >= len(coords):
            out.append((coords[-1][0], coords[-1][1], 1.0))
            continue
        seg_len = cum[j + 1] - cum[j]
        r = 0.0 if seg_len <= 0 else (target - cum[j]) / seg_len
        lon = coords[j][0] + (coords[j + 1][0] - coords[j][0]) * r
        lat = coords[j][1] + (coords[j + 1][1] - coords[j][1]) * r
        out.append((lon, lat, target / total))
    return out


def snap_route_to_railway(
    schedule: dict,
    points_per_segment: int = 60,
    max_snap_distance_m: float = 5000.0,
    refresh_cache: bool = False,
    spacing_m: Optional[float] = None,
) -> list[dict]:
    """主入口：把 schedule（带 station 坐标）吸附到 OSM 铁路线，输出带时间戳的轨迹点。

    Args:
        schedule: schedule_fetcher.fetch_schedule + attach_coords 的输出
        points_per_segment: 每两个相邻车站之间在铁路线上的采样点数（spacing_m 为 None 时生效）
        max_snap_distance_m: 车站到最近铁路节点的最大允许距离（超过则该段退化为直连）
        refresh_cache: 强制刷新 OSM 数据缓存
        spacing_m: 沿铁路线的目标采样间距（米）。给定时优先使用，按段长自动决定采样点数：
            n = max(points_per_segment, ceil(段长 / spacing_m) + 1)
            这样可保证不同长度的段有一致的"米/点"密度。

    Returns: 与 build_track_points 兼容的 list[dict]
    """
    valid = [s for s in schedule["stations"] if s.get("lon") is not None and s.get("lat") is not None]
    if len(valid) < 2:
        return []
    bbox = compute_bbox(valid, buffer_deg=0.3)
    ways = load_or_fetch_railway(bbox, refresh=refresh_cache)
    G, node_pos = build_graph(ways)

    # 给每个车站找最近图节点
    station_nodes: list[tuple[Optional[tuple[int, int]], float]] = []
    for st in valid:
        node, d = find_nearest_node(G, node_pos, st["lon"], st["lat"])
        station_nodes.append((node, d))
        print(f"   {st['name']:>12s} → 最近铁路节点距 {d:.0f} m", file=sys.stderr)

    depart_date = schedule["depart_date"]
    out_points: list[dict] = []

    for idx in range(len(valid)):
        st = valid[idx]
        anchor = station_anchor_time(depart_date, st)
        # 站点本身：用车站真实坐标（不吸附），保证地图上落在车站位置
        out_points.append(
            {
                "time": anchor,
                "lon": float(st["lon"]),
                "lat": float(st["lat"]),
                "alt": 0.0,
                "name": st["name"],
                "kind": "station",
                "no": st.get("no"),
            }
        )
        if idx + 1 >= len(valid):
            break
        nxt = valid[idx + 1]
        node_a, da = station_nodes[idx]
        node_b, db = station_nodes[idx + 1]
        t0 = parse_schedule_time(depart_date, st.get("start"), st.get("day", 0)) or anchor
        t1 = parse_schedule_time(depart_date, nxt.get("arrive"), nxt.get("day", 0)) or station_anchor_time(depart_date, nxt)
        if not t0 or not t1 or t1 <= t0:
            continue

        path_coords: list[tuple[float, float]] = []
        if (
            node_a is not None
            and node_b is not None
            and da <= max_snap_distance_m
            and db <= max_snap_distance_m
        ):
            try:
                path_nodes = nx.shortest_path(G, node_a, node_b, weight="weight")
                path_coords = _path_coords_along_edges(G, node_pos, path_nodes)
            except (nx.NetworkXNoPath, nx.NodeNotFound):
                path_coords = []

        if path_coords:
            # 在车站坐标和路径首尾之间补一段过渡（避免视觉断裂）
            full = [(st["lon"], st["lat"])] + path_coords + [(nxt["lon"], nxt["lat"])]
            # 决定 n_samples：spacing_m 给定时按目标间距计算
            if spacing_m and spacing_m > 0:
                seg_len = _line_total_length_m(full)
                n_by_spacing = int(math.ceil(seg_len / spacing_m)) + 1
                n_samples = max(2, points_per_segment, n_by_spacing)
            else:
                n_samples = max(2, points_per_segment)
            samples = _resample_line(full, n_samples)
            # 跳过首尾（站点本身已在 out_points 中）
            for lon, lat, r in samples[1:-1]:
                t = t0 + (t1 - t0) * r
                out_points.append({"time": t, "lon": lon, "lat": lat, "alt": 0.0, "name": "", "kind": "interp"})
        else:
            # 退化为直连
            print(f"   ⚠️ {st['name']} → {nxt['name']} 无可用铁路路径，退化为直连", file=sys.stderr)
            # 直连段也按 spacing_m 调整（如指定）
            if spacing_m and spacing_m > 0:
                seg_len = _hav_distance_m(st["lon"], st["lat"], nxt["lon"], nxt["lat"])
                n_inner = max(points_per_segment, int(math.ceil(seg_len / spacing_m)) - 1)
            else:
                n_inner = points_per_segment
            for k in range(1, n_inner + 1):
                r = k / (n_inner + 1)
                t = t0 + (t1 - t0) * r
                lon = st["lon"] + (nxt["lon"] - st["lon"]) * r
                lat = st["lat"] + (nxt["lat"] - st["lat"]) * r
                out_points.append({"time": t, "lon": lon, "lat": lat, "alt": 0.0, "name": "", "kind": "interp"})

    out_points.sort(key=lambda p: (p["time"] is None, p["time"] or datetime.min.replace(tzinfo=TZ_BEIJING)))
    return out_points


# ===== CLI =====


def _cli():
    import argparse

    parser = argparse.ArgumentParser(description="OSM 铁路网吸附测试工具")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_pre = sub.add_parser("preload", help="预先拉取并缓存指定车次的 bbox 内的 OSM 铁路数据")
    p_pre.add_argument("schedule_json", help="schedule JSON 文件路径")
    p_pre.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    if args.cmd == "preload":
        with open(args.schedule_json, "r", encoding="utf-8") as f:
            sch = json.load(f)
        valid = [s for s in sch["stations"] if s.get("lon") is not None]
        bbox = compute_bbox(valid)
        load_or_fetch_railway(bbox, refresh=args.refresh)


if __name__ == "__main__":
    _cli()
