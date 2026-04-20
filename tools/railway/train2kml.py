"""车次→KML 主入口。

用法：
    # 自动从 12306 拉时刻表 → 生成 KML
    uv run python train2kml.py G1 2026-04-21 -o output/G1_20260421.kml

    # 用本地已有 schedule JSON 文件
    uv run python train2kml.py --from-json data/cache/G1_2026-04-21.json -o output/G1.kml

    # 指定每段插值密度（默认 30，越大越平滑）
    uv run python train2kml.py G1 2026-04-21 --density 60

    # 阶段 2：启用铁路线吸附（需要先准备好 OSM 铁路 GeoJSON）
    uv run python train2kml.py G1 2026-04-21 --snap

    # snap 模式下指定相邻轨迹点的目标间距（米），越小越紧密（默认 200）
    uv run python train2kml.py G1 2026-04-21 --snap --snap-spacing 100

    # 只生成某段（按站名 / 序号截取）
    uv run python train2kml.py G1 2026-04-21 --from 沧州西 --to 南京南
    uv run python train2kml.py G1 2026-04-21 --from 2 --to 5
    # 起止站匹配不上时会列出全部经停站

    # 仅导出 schedule JSON（含坐标），供 kmlTrackEditor.html "按车次导入" 使用
    uv run python train2kml.py G1 2026-04-21 --schedule-only -o output/G1.json

参数说明：
    --density N         相邻站之间的插值点数（默认 30，snap 模式下作为下限）
    --no-dwell          不输出停站期间的"停留点"
    --snap              启用 OSM 铁路线吸附（需安装 [snap] 依赖）
    --snap-spacing M    snap 模式下相邻轨迹点的目标间距（米，默认 200，越小越紧密）
    --from S            起始站：站名或 1-based 序号（与 --to 配套使用）
    --to S              终点站：站名或 1-based 序号
    --schedule-only     只输出 schedule JSON（含坐标），不生成 KML
    --output / -o       输出 KML（或 JSON）路径
    --from-json         直接读取 schedule JSON（跳过 12306 抓取）
"""
from __future__ import annotations

import argparse
import copy
import json
import os
import re
import sys
from datetime import datetime
from typing import Optional

from schedule_fetcher import fetch_schedule, attach_coords
from track_builder import build_track_points, station_anchor_time
from kml_writer import write_kml
from stations_db import load_stations


def _normalize_station_name(name: str) -> str:
    """规范化站名以便匹配：去掉首尾空白、末尾的 "站"、英文括号内容。"""
    if not name:
        return ""
    s = name.strip()
    # 去掉括号内的内容（如 "上海(沪)"）
    s = re.sub(r"\s*[（(].*?[)）]\s*", "", s)
    # 去末尾的 "站"
    if s.endswith("站"):
        s = s[:-1]
    return s.strip()


def _match_station_in_schedule(
    schedule: dict,
    query: str,
) -> list[tuple[int, dict]]:
    """在 schedule.stations 中查找匹配项。

    匹配规则：
        - query 是纯整数 → 按 1-based 数组下标取（越界返回空）
        - 否则按规范化站名先精确匹配；若无结果则做包含匹配
    返回 [(idx_0_based, station), ...]，长度 0=无、1=唯一、>1=候选多个
    """
    stations = schedule.get("stations", [])
    if not stations:
        return []
    q = (query or "").strip()
    if not q:
        return []
    # 纯数字 → 按 1-based 下标
    if re.fullmatch(r"\d+", q):
        idx = int(q) - 1
        if 0 <= idx < len(stations):
            return [(idx, stations[idx])]
        return []
    # 站名匹配：先精确，后包含
    qn = _normalize_station_name(q)
    exact: list[tuple[int, dict]] = []
    contains: list[tuple[int, dict]] = []
    for i, st in enumerate(stations):
        nn = _normalize_station_name(st.get("name", ""))
        if not nn:
            continue
        if nn == qn:
            exact.append((i, st))
        elif qn and qn in nn:
            contains.append((i, st))
    if exact:
        return exact
    return contains


def _format_station_row(idx_1based: int, st: dict) -> str:
    arr = st.get("arrive") or "  -  "
    dep = st.get("start") or "  -  "
    day = st.get("day", 0)
    suffix = f" (+{day} day)" if day else ""
    name = st.get("name", "")
    return f"  [{idx_1based:>2d}] {name:<10s} 到 {arr:>5s}  发 {dep:>5s}{suffix}"


def _print_station_list(schedule: dict, *, header: str = "经停站列表：", file=sys.stderr) -> None:
    print(header, file=file)
    for i, st in enumerate(schedule.get("stations", []), start=1):
        print(_format_station_row(i, st), file=file)


def slice_schedule(
    schedule: dict,
    from_query: Optional[str],
    to_query: Optional[str],
) -> dict:
    """按起止站截取 schedule 中的 stations 区间，返回新的 schedule（深拷贝）。

    匹配失败 / 多个候选 / 起止顺序错乱时打印站点列表并 sys.exit(3)。
    其中一个为 None 时仅截取另一端（默认起点=第 1 站，终点=末站）。
    """
    if not from_query and not to_query:
        return schedule

    n = len(schedule.get("stations", []))
    if n < 2:
        print("❌ schedule 中站点不足 2 个，无法截取", file=sys.stderr)
        sys.exit(3)

    def _resolve(query: Optional[str], default_idx: int, label: str) -> int:
        if not query:
            return default_idx
        matches = _match_station_in_schedule(schedule, query)
        if len(matches) == 1:
            return matches[0][0]
        if len(matches) == 0:
            print(f"❌ {label}站 '{query}' 未匹配到任何经停站\n", file=sys.stderr)
        else:
            cand = "、".join(f"[{i+1}] {st.get('name','')}" for i, st in matches)
            print(f"❌ {label}站 '{query}' 匹配到多个候选: {cand}\n", file=sys.stderr)
        _print_station_list(schedule)
        print(
            f"\n👉 请用更精确的站名或 1-based 序号重试，例如 --{label.lower()} 1 或 --{label.lower()} '{schedule['stations'][0].get('name','')}'",
            file=sys.stderr,
        )
        sys.exit(3)

    from_idx = _resolve(from_query, 0, "from")
    to_idx = _resolve(to_query, n - 1, "to")

    if from_idx == to_idx:
        print(f"❌ 起点站 (#{from_idx+1}) 与终点站相同，无法截取轨迹", file=sys.stderr)
        _print_station_list(schedule)
        sys.exit(3)
    if from_idx > to_idx:
        print(
            f"❌ 起点站 #{from_idx+1} '{schedule['stations'][from_idx].get('name','')}' "
            f"位于终点站 #{to_idx+1} '{schedule['stations'][to_idx].get('name','')}' 之后，请检查顺序",
            file=sys.stderr,
        )
        _print_station_list(schedule)
        sys.exit(3)

    new_sched = copy.deepcopy(schedule)
    new_sched["stations"] = new_sched["stations"][from_idx : to_idx + 1]
    new_sched["from"] = new_sched["stations"][0].get("name", "")
    new_sched["to"] = new_sched["stations"][-1].get("name", "")
    new_sched["sliced_from_full"] = {
        "original_stations": n,
        "from_idx_1based": from_idx + 1,
        "to_idx_1based": to_idx + 1,
    }
    print(
        f"✂️  截取 [{from_idx+1}] {new_sched['from']}  →  [{to_idx+1}] {new_sched['to']}"
        f"（共 {len(new_sched['stations'])} 站，原 {n} 站）",
        file=sys.stderr,
    )
    return new_sched


def _format_kml_description(schedule: dict) -> str:
    lines = [
        f"车次: {schedule['train_code']}",
        f"始发日期: {schedule['depart_date']}",
        f"始发-终到: {schedule.get('from','')} → {schedule.get('to','')}",
        f"经停站数: {len(schedule['stations'])}",
        f"抓取时间: {schedule.get('fetched_at','')}",
        f"train_no: {schedule.get('train_no','')}",
    ]
    return "\n".join(lines)


def generate_kml_from_schedule(
    schedule: dict,
    out_path: str,
    points_per_segment: int = 30,
    include_dwell: bool = True,
    snap: bool = False,
    snap_spacing_m: Optional[float] = None,
) -> dict:
    """主流程：schedule → coords → 轨迹点 → KML。

    Args:
        snap_spacing_m: snap 模式下相邻轨迹点的目标间距（米）。None 时仅按 points_per_segment 分段固定数。

    Returns: 概要信息 dict（站数 / 点数 / 路径）
    """
    if any(s.get("lon") is None for s in schedule["stations"]):
        matched, missing = attach_coords(schedule, load_stations())
        if missing:
            print(f"⚠️  以下站点无坐标: {missing}", file=sys.stderr)

    if snap:
        try:
            from railway_network import snap_route_to_railway
        except ImportError as e:
            print(f"❌ 启用 --snap 需要安装 snap 扩展依赖: uv sync --extra snap （{e}）", file=sys.stderr)
            sys.exit(2)
        points = snap_route_to_railway(
            schedule,
            points_per_segment=points_per_segment,
            spacing_m=snap_spacing_m,
        )
    else:
        points = build_track_points(
            schedule,
            points_per_segment=points_per_segment,
            include_dwell=include_dwell,
        )

    name = f"{schedule['train_code']} {schedule.get('from','')}→{schedule.get('to','')} ({schedule['depart_date']})"
    description = _format_kml_description(schedule)

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    write_kml(
        points=points,
        out_path=out_path,
        name=name,
        description=description,
        stations=schedule["stations"],
    )
    return {
        "stations": len(schedule["stations"]),
        "points": len(points),
        "path": out_path,
    }


def _default_out_path(schedule: dict) -> str:
    safe_code = schedule["train_code"].replace("/", "_")
    return os.path.join("output", f"{safe_code}_{schedule['depart_date']}.kml")


def _default_schedule_path(schedule: dict) -> str:
    safe_code = schedule["train_code"].replace("/", "_")
    return os.path.join("output", f"{safe_code}_{schedule['depart_date']}_schedule.json")


def export_schedule_json(schedule: dict, out_path: str) -> dict:
    """补全坐标后输出 schedule JSON（不生成 KML）。"""
    matched, missing = attach_coords(schedule, load_stations())
    if missing:
        print(f"⚠️  以下站点无坐标: {missing}", file=sys.stderr)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(schedule, f, ensure_ascii=False, indent=2)
    with_coords = sum(1 for s in schedule["stations"] if s.get("lon") is not None)
    return {
        "stations": len(schedule["stations"]),
        "stations_with_coords": with_coords,
        "path": out_path,
    }


def _cli():
    p = argparse.ArgumentParser(
        description="按车次号生成铁路行程 KML 轨迹（用于 kmlTrackEditor.html）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument("train_code", nargs="?", help="车次代码，如 G1 / D123 / Z6（与 --from-json 二选一）")
    p.add_argument("depart_date", nargs="?", help="始发日期 yyyy-MM-dd（与 train_code 配套）")
    p.add_argument("--from-json", help="直接读已有 schedule JSON 文件")
    p.add_argument("--no-cache", action="store_true", help="忽略本地缓存，强制重新拉 12306")
    p.add_argument("--density", type=int, default=30,
                   help="相邻站之间插值点数 (默认 30；snap 模式下作为下限)")
    p.add_argument("--no-dwell", action="store_true", help="不输出停站期间的停留点")
    p.add_argument("--snap", action="store_true", help="启用 OSM 铁路线吸附（需要 snap 依赖+OSM 数据）")
    p.add_argument("--snap-spacing", dest="snap_spacing", type=float, default=200.0,
                   help="snap 模式下相邻轨迹点的目标间距（米，默认 200，越小越紧密；传 0 则关闭按间距控制）")
    p.add_argument("--from", dest="from_station", metavar="START",
                   help="只输出从该站开始的轨迹；可填站名或 1-based 序号。需配合 --to")
    p.add_argument("--to", dest="to_station", metavar="END",
                   help="只输出到该站结束的轨迹；可填站名或 1-based 序号。需配合 --from")
    p.add_argument("--list-stations", action="store_true",
                   help="只列出该车次的全部经停站后退出（便于查阅站名/序号）")
    p.add_argument("--schedule-only", action="store_true",
                   help="只输出 schedule JSON（含坐标），不生成 KML，可供 kmlTrackEditor.html 直接导入")
    p.add_argument("-o", "--output", help="输出 KML（或 JSON）路径，默认 output/<车次>_<日期>.kml/json")
    args = p.parse_args()

    if args.from_json:
        with open(args.from_json, "r", encoding="utf-8") as f:
            schedule = json.load(f)
    else:
        if not args.train_code or not args.depart_date:
            p.error("必须提供 train_code + depart_date，或使用 --from-json")
        schedule = fetch_schedule(
            args.train_code,
            args.depart_date,
            use_cache=not args.no_cache,
        )

    if args.list_stations:
        header = (
            f"车次 {schedule['train_code']} "
            f"({schedule.get('from','')}→{schedule.get('to','')}) "
            f"{schedule.get('depart_date','')} 共 {len(schedule['stations'])} 站："
        )
        _print_station_list(schedule, header=header, file=sys.stdout)
        return

    if args.from_station or args.to_station:
        schedule = slice_schedule(schedule, args.from_station, args.to_station)

    spacing = args.snap_spacing if args.snap_spacing and args.snap_spacing > 0 else None

    if args.schedule_only:
        out_path = args.output or _default_schedule_path(schedule)
        info = export_schedule_json(schedule, out_path)
        print(f"✅ schedule JSON 已生成", file=sys.stderr)
        print(f"   车次: {schedule['train_code']}  日期: {schedule['depart_date']}", file=sys.stderr)
        print(f"   经停站: {info['stations']}（含坐标 {info['stations_with_coords']}）", file=sys.stderr)
        print(f"   输出: {info['path']}（在 kmlTrackEditor.html 用『按车次导入』加载）", file=sys.stderr)
        print(info["path"])
        return

    out_path = args.output or _default_out_path(schedule)
    info = generate_kml_from_schedule(
        schedule,
        out_path,
        points_per_segment=args.density,
        include_dwell=not args.no_dwell,
        snap=args.snap,
        snap_spacing_m=spacing if args.snap else None,
    )
    print(f"✅ KML 已生成", file=sys.stderr)
    print(f"   车次: {schedule['train_code']}  日期: {schedule['depart_date']}", file=sys.stderr)
    print(f"   经停站: {info['stations']} | 轨迹点: {info['points']}", file=sys.stderr)
    if args.snap and spacing:
        print(f"   snap-spacing: {spacing:.0f} m", file=sys.stderr)
    print(f"   输出: {info['path']}", file=sys.stderr)
    print(info["path"])


if __name__ == "__main__":
    _cli()
