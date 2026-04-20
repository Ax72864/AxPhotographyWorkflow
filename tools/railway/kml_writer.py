"""把 build_track_points 输出的轨迹点序列写出为 KML，兼容两步路 / kmlTrackEditor.html。

KML 结构与 kmlTrackEditor.html 期望保持一致：
- gx:Track 形式：当 emit_track=True 时，每个点用 <when> + <gx:coord>
- 经停站作为 Placemark 标记单独列出，便于在地图上看出转折点
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from typing import Iterable, Optional

KML_NS = "http://www.opengis.net/kml/2.2"
GX_NS = "http://www.google.com/kml/ext/2.2"


def _utc_iso(t: datetime) -> str:
    """把 datetime 转为 KML 标准 UTC ISO（结尾 Z）。"""
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_kml(
    points: list[dict],
    out_path: str,
    name: str = "Train Route",
    description: str = "",
    stations: Optional[list[dict]] = None,
    pretty: bool = True,
) -> None:
    """生成 KML 文件。

    Args:
        points: build_track_points 的返回值（含 time/lon/lat/alt/name/kind）
        out_path: KML 输出路径
        name: KML 文档名 (Document.name)
        description: 文档描述
        stations: 要单独标注为 Placemark 的经停站列表（每项至少含 name/lon/lat）
        pretty: 是否缩进格式化
    """
    ET.register_namespace("", KML_NS)
    ET.register_namespace("gx", GX_NS)
    kml = ET.Element(f"{{{KML_NS}}}kml")
    doc = ET.SubElement(kml, f"{{{KML_NS}}}Document")
    ET.SubElement(doc, f"{{{KML_NS}}}name").text = name
    if description:
        ET.SubElement(doc, f"{{{KML_NS}}}description").text = description

    # 样式：轨迹线
    style = ET.SubElement(doc, f"{{{KML_NS}}}Style", id="trackLine")
    ls = ET.SubElement(style, f"{{{KML_NS}}}LineStyle")
    ET.SubElement(ls, f"{{{KML_NS}}}color").text = "ffe07000"
    ET.SubElement(ls, f"{{{KML_NS}}}width").text = "3"
    # 样式：经停站图标
    s_st = ET.SubElement(doc, f"{{{KML_NS}}}Style", id="stationIcon")
    iconstyle = ET.SubElement(s_st, f"{{{KML_NS}}}IconStyle")
    icon = ET.SubElement(iconstyle, f"{{{KML_NS}}}Icon")
    ET.SubElement(icon, f"{{{KML_NS}}}href").text = "http://maps.google.com/mapfiles/kml/shapes/rail.png"
    ET.SubElement(iconstyle, f"{{{KML_NS}}}scale").text = "0.9"

    # 经停站作为单独 Placemark
    if stations:
        folder = ET.SubElement(doc, f"{{{KML_NS}}}Folder")
        ET.SubElement(folder, f"{{{KML_NS}}}name").text = "经停站"
        for st in stations:
            if st.get("lon") is None or st.get("lat") is None:
                continue
            pm = ET.SubElement(folder, f"{{{KML_NS}}}Placemark")
            ET.SubElement(pm, f"{{{KML_NS}}}name").text = st.get("name") or ""
            ET.SubElement(pm, f"{{{KML_NS}}}styleUrl").text = "#stationIcon"
            desc_lines = []
            if st.get("arrive"):
                desc_lines.append(f"到达: {st['arrive']}")
            if st.get("start"):
                desc_lines.append(f"出发: {st['start']}")
            if desc_lines:
                ET.SubElement(pm, f"{{{KML_NS}}}description").text = "\n".join(desc_lines)
            pt = ET.SubElement(pm, f"{{{KML_NS}}}Point")
            ET.SubElement(pt, f"{{{KML_NS}}}coordinates").text = f"{st['lon']:.7f},{st['lat']:.7f},0"

    # 主轨迹：gx:Track
    track_pm = ET.SubElement(doc, f"{{{KML_NS}}}Placemark")
    ET.SubElement(track_pm, f"{{{KML_NS}}}name").text = "Track"
    ET.SubElement(track_pm, f"{{{KML_NS}}}styleUrl").text = "#trackLine"
    track = ET.SubElement(track_pm, f"{{{GX_NS}}}Track")
    # altitudeMode 用 clampToGround 让海拔不影响显示
    ET.SubElement(track, f"{{{KML_NS}}}altitudeMode").text = "clampToGround"
    for p in points:
        if p.get("time") is None or p.get("lon") is None or p.get("lat") is None:
            continue
        ET.SubElement(track, f"{{{KML_NS}}}when").text = _utc_iso(p["time"])
    for p in points:
        if p.get("time") is None or p.get("lon") is None or p.get("lat") is None:
            continue
        coord = ET.SubElement(track, f"{{{GX_NS}}}coord")
        coord.text = f"{p['lon']:.7f} {p['lat']:.7f} {p.get('alt', 0):.1f}"

    # 容错：如果点全都缺时间（理论上不会），退化为 LineString
    has_time = any(p.get("time") is not None for p in points)
    if not has_time and points:
        ls_pm = ET.SubElement(doc, f"{{{KML_NS}}}Placemark")
        ET.SubElement(ls_pm, f"{{{KML_NS}}}name").text = "TrackLine"
        ET.SubElement(ls_pm, f"{{{KML_NS}}}styleUrl").text = "#trackLine"
        line = ET.SubElement(ls_pm, f"{{{KML_NS}}}LineString")
        coords = "\n".join(f"{p['lon']:.7f},{p['lat']:.7f},0" for p in points if p.get("lon") is not None)
        ET.SubElement(line, f"{{{KML_NS}}}coordinates").text = coords

    tree = ET.ElementTree(kml)
    if pretty:
        ET.indent(tree, space="  ")
    tree.write(out_path, encoding="utf-8", xml_declaration=True)
