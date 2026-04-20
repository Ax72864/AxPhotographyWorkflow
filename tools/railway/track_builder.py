"""把车次时刻表（schedule JSON）转换为带时间戳的轨迹点序列。

核心策略（用户选定的 schedule 模式）：
- 每个经停站有一个明确的时刻表时间（始发用 start，中间用 arrive，到达用 arrive）
- 相邻两个经停站之间，按线段距离均匀切若干个插值点
- 沿线插值点的时间戳：按"距离比例 × 站间运行时长"计算，从前一站发车时刻起
- 输出统一为 WGS84 经纬度 + UTC 感知 datetime
"""
from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone
from typing import Optional


# 北京时区（与 12306 一致）
TZ_BEIJING = timezone(timedelta(hours=8))


def _hav_distance_m(lon1: float, lat1: float, lon2: float, lat2: float) -> float:
    """WGS84 大圆距离（米），Haversine。"""
    R = 6371008.8
    rlat1, rlat2 = math.radians(lat1), math.radians(lat2)
    dlat = rlat2 - rlat1
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat / 2) ** 2 + math.cos(rlat1) * math.cos(rlat2) * math.sin(dlon / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def parse_schedule_time(
    depart_date: str,
    hhmm: Optional[str],
    day_diff: int,
) -> Optional[datetime]:
    """把 12306 时刻表的 HH:MM + 始发日期 + 跨天差 → 北京时区 datetime。

    返回 None 表示该字段缺失（如始发站无 arrive，终到站某些查询无 start）。
    """
    if not hhmm or hhmm in {"----", ""}:
        return None
    h, m = hhmm.split(":")
    base = datetime.fromisoformat(depart_date).replace(tzinfo=TZ_BEIJING)
    return base + timedelta(days=int(day_diff or 0), hours=int(h), minutes=int(m))


def station_anchor_time(
    depart_date: str,
    station: dict,
    role: str = "auto",
) -> Optional[datetime]:
    """选择一个站点的代表时刻：

    - role="auto"：始发站取 start，中间/终到站取 arrive；arrive 缺失则退回 start。
    - role="start"：取 start
    - role="arrive"：取 arrive
    """
    if role == "start":
        return parse_schedule_time(depart_date, station.get("start"), station.get("day", 0))
    if role == "arrive":
        return parse_schedule_time(depart_date, station.get("arrive"), station.get("day", 0))
    arr = parse_schedule_time(depart_date, station.get("arrive"), station.get("day", 0))
    sta = parse_schedule_time(depart_date, station.get("start"), station.get("day", 0))
    return arr or sta


def _segment_endpoints(
    depart_date: str,
    a: dict,
    b: dict,
) -> tuple[Optional[datetime], Optional[datetime]]:
    """计算 A → B 段的起止时间：
    - 起点：A 的 start（始发出站）
    - 终点：B 的 arrive（到站）
    缺失则用 anchor_time 兜底。
    """
    t0 = parse_schedule_time(depart_date, a.get("start"), a.get("day", 0)) or station_anchor_time(depart_date, a)
    t1 = parse_schedule_time(depart_date, b.get("arrive"), b.get("day", 0)) or station_anchor_time(depart_date, b)
    return t0, t1


def build_track_points(
    schedule: dict,
    points_per_segment: int = 30,
    include_dwell: bool = True,
) -> list[dict]:
    """根据 schedule（含 stations[*].lon/lat）生成轨迹点列表。

    Args:
        schedule: 来自 schedule_fetcher.fetch_schedule + attach_coords 的输出。
        points_per_segment: 每两个相邻站之间的插值点数（不含起终点本身）。
            如果某段距离 < 5km 会自动减少；> 200km 会自动加倍，避免视觉断裂。
        include_dwell: 是否在站台停留期间补打"停留点"（开始停-结束停）。

    Returns: list of {time: datetime(UTC感知), lon, lat, name, kind}
        kind in {"station", "interp", "dwell_start", "dwell_end"}
    """
    depart_date = schedule["depart_date"]
    stations = schedule["stations"]
    out: list[dict] = []

    valid: list[dict] = []
    for st in stations:
        if st.get("lon") is None or st.get("lat") is None:
            continue
        valid.append(st)
    if len(valid) < 2:
        return out

    for idx, st in enumerate(valid):
        anchor = station_anchor_time(depart_date, st)
        out.append(
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
        if include_dwell:
            t_arr = parse_schedule_time(depart_date, st.get("arrive"), st.get("day", 0))
            t_dep = parse_schedule_time(depart_date, st.get("start"), st.get("day", 0))
            if t_arr and t_dep and t_dep > t_arr + timedelta(seconds=30):
                # 替换 anchor 为到达时间，再加一个发车时间（停留过程）
                out[-1]["time"] = t_arr
                out[-1]["kind"] = "dwell_start"
                out.append(
                    {
                        "time": t_dep,
                        "lon": float(st["lon"]),
                        "lat": float(st["lat"]),
                        "alt": 0.0,
                        "name": st["name"],
                        "kind": "dwell_end",
                        "no": st.get("no"),
                    }
                )

        if idx + 1 >= len(valid):
            break
        nxt = valid[idx + 1]
        t0, t1 = _segment_endpoints(depart_date, st, nxt)
        if not t0 or not t1 or t1 <= t0:
            continue
        dist = _hav_distance_m(st["lon"], st["lat"], nxt["lon"], nxt["lat"])
        n = max(1, points_per_segment)
        if dist < 5_000:
            n = max(1, int(n * 0.3))
        elif dist > 200_000:
            n = int(n * 2)
        n = max(1, n)
        # 在 t0..t1 时间区间和 (st)..(nxt) 空间区间之间均匀插值
        for k in range(1, n + 1):
            r = k / (n + 1)
            t = t0 + (t1 - t0) * r
            lon = st["lon"] + (nxt["lon"] - st["lon"]) * r
            lat = st["lat"] + (nxt["lat"] - st["lat"]) * r
            out.append(
                {
                    "time": t,
                    "lon": lon,
                    "lat": lat,
                    "alt": 0.0,
                    "name": "",
                    "kind": "interp",
                }
            )
    # 按时间排序保险一下
    out.sort(key=lambda p: (p["time"] is None, p["time"] or datetime.min.replace(tzinfo=TZ_BEIJING)))
    return out
