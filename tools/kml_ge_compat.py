"""
KML 转 Google Earth 兼容格式工具

把两步路 APP 等导出的 KML 文件中的 gx:Track 轨迹元素转换为标准 LineString，
让 Google Earth 网页版（earth.google.com）和移动端 App 能够正确显示轨迹。

背景：
  - gx:Track 是 Google KML 扩展元素（命名空间 xmlns:gx）
    优点：可以保留每个轨迹点的时间戳，支持时间轴回放/动画
    缺点：仅 Google Earth Pro 桌面版完整支持，网页版/移动版会报"元素不受支持"
  - LineString 是 KML 标准元素
    优点：所有 Google Earth 版本都支持
    缺点：丢失时间信息，仅作为静态线条显示（无动画回放）

转换规则：
  1. <gx:Track>...</gx:Track>            -> <LineString>...</LineString>
  2. 多个 <gx:coord>经度 纬度 高度</...>  -> 合并为单个 <coordinates>经度,纬度,高度 ...</...>
  3. 轨迹中的 <when>...</when> 时间戳数组 -> 删除（不在 LineString 中使用）
  4. 轨迹中的 GxTrackExtendedData/PauseTimes -> 删除（gx 扩展专用数据）
  5. 标注点上的 TimeStamp 元素            -> 保留（参考能正常显示的 KML 行为一致）

输出：默认在原文件同目录生成 xxx_ge_compat.kml，不修改原文件。
"""

import argparse
import glob
import os
import re
import sys


# Windows 默认 stdout 编码为 GBK，会导致 emoji（✅❌⚠️📊 等）输出报 UnicodeEncodeError
# 在 Python 3.7+ 上将 stdout/stderr 重新配置为 UTF-8，让脚本在所有终端表现一致
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, 'reconfigure'):
        try:
            _stream.reconfigure(encoding='utf-8')
        except Exception:
            pass


GX_TRACK_PATTERN = re.compile(r'<gx:Track\b[^>]*>.*?</gx:Track>', re.DOTALL)
GX_COORD_PATTERN = re.compile(r'<gx:coord\b[^>]*>(.*?)</gx:coord>', re.DOTALL)
TRACK_INDENT_PATTERN = re.compile(r'(^|\n)([ \t]*)<gx:Track\b', re.DOTALL)


def _convert_one_track(track_block: str, indent: str) -> tuple[str, int]:
    """把单个 <gx:Track>...</gx:Track> 块转换成 <LineString>...</LineString>。

    Args:
        track_block: 完整的 gx:Track XML 文本
        indent: gx:Track 标签自身在源文件中所在行的前导缩进（空格/制表符）

    Returns:
        (替换文本, 转换出的坐标点数)
    """
    coords = []
    for raw in GX_COORD_PATTERN.findall(track_block):
        # gx:coord 是空格分隔的"经度 纬度 高度"，LineString 需要逗号分隔
        parts = raw.strip().split()
        if len(parts) >= 2:
            coords.append(','.join(parts))

    if not coords:
        # 异常：没有坐标，保持原样不替换，让用户自行检查
        return track_block, 0

    coords_text = ' '.join(coords)
    inner_indent = indent + '  '
    return (
        f'<LineString>\n'
        f'{inner_indent}<coordinates>{coords_text}</coordinates>\n'
        f'{indent}</LineString>'
    ), len(coords)


def convert_gx_track_to_linestring(kml_text: str) -> tuple[str, int, int]:
    """把整个 KML 文本中所有的 gx:Track 转换为 LineString。

    Returns:
        (新文本, 转换的轨迹数, 累计坐标点数)
    """
    track_count = 0
    coord_total = 0

    def _replace(match: re.Match) -> str:
        nonlocal track_count, coord_total
        track_block = match.group(0)
        # 探测该 gx:Track 标签所在行的缩进，用于美化输出（仅影响新文件可读性）
        start = match.start()
        line_start = kml_text.rfind('\n', 0, start) + 1
        indent = kml_text[line_start:start]
        # 兜底：如果探测出来的内容里掺了非空白字符，则用 8 空格（两步路 KML 的默认缩进）
        if indent.strip() != '':
            indent = ' ' * 8

        new_block, n = _convert_one_track(track_block, indent)
        if n > 0:
            track_count += 1
            coord_total += n
        return new_block

    new_text = GX_TRACK_PATTERN.sub(_replace, kml_text)
    return new_text, track_count, coord_total


def make_default_output_path(input_path: str) -> str:
    """xxx.kml -> xxx_ge_compat.kml"""
    base, ext = os.path.splitext(input_path)
    return f"{base}_ge_compat{ext}"


def process_file(input_path: str, output_path: str, overwrite: bool) -> bool:
    """处理单个 KML 文件，返回是否成功生成新文件。"""
    if not os.path.exists(input_path):
        print(f"❌ 文件不存在: {input_path}")
        return False

    if os.path.abspath(input_path) == os.path.abspath(output_path):
        # 与"另存为新文件"的设计冲突，明确拒绝以避免误删原文件
        print(f"❌ 输出路径与输入路径相同，拒绝执行（本工具默认不覆盖原文件）: {input_path}")
        return False

    if os.path.exists(output_path) and not overwrite:
        print(f"⚠️ 输出文件已存在，跳过（如需覆盖请加 --overwrite）: {output_path}")
        return False

    try:
        with open(input_path, 'r', encoding='utf-8') as f:
            text = f.read()
    except UnicodeDecodeError as e:
        print(f"❌ 读取失败（非 UTF-8 编码）: {input_path}\n   {e}")
        return False

    new_text, track_count, coord_total = convert_gx_track_to_linestring(text)

    if track_count == 0:
        print(f"ℹ️ 未发现可转换的 gx:Track 元素: {os.path.basename(input_path)}")
        return False

    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write(new_text)
    except OSError as e:
        print(f"❌ 写入失败: {output_path}\n   {e}")
        return False

    print(f"✅ 已转换 {track_count} 条轨迹（共 {coord_total} 个坐标点）")
    print(f"   输入: {input_path}")
    print(f"   输出: {output_path}")
    return True


def collect_input_files(args: argparse.Namespace) -> list[str]:
    """根据命令行参数汇总待处理的 KML 文件列表。"""
    files: list[str] = []

    if args.directory:
        if not os.path.isdir(args.directory):
            print(f"❌ 目录不存在: {args.directory}")
            sys.exit(1)
        # 排除已转换过的 _ge_compat.kml，避免二次处理
        for f in sorted(glob.glob(os.path.join(args.directory, '*.kml'))):
            if not f.endswith('_ge_compat.kml'):
                files.append(f)
        if not files:
            print(f"⚠️ 目录中没有可处理的 .kml 文件: {args.directory}")

    # 命令行通配（PowerShell 不会自动展开，由 Python 自己 glob 一次）
    for raw in args.inputs:
        expanded = glob.glob(raw)
        if expanded:
            for f in expanded:
                if not f.endswith('_ge_compat.kml'):
                    files.append(f)
        else:
            files.append(raw)

    # 去重，保持原始顺序
    seen = set()
    unique = []
    for f in files:
        key = os.path.abspath(f)
        if key not in seen:
            seen.add(key)
            unique.append(f)
    return unique


def main():
    parser = argparse.ArgumentParser(
        description='把两步路 KML 中的 gx:Track 转换为标准 LineString，让 Google Earth 网页版/移动端能正常显示轨迹',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""使用示例：
  # 单个文件，自动生成同目录下 xxx_ge_compat.kml
  python kml_ge_compat.py track.kml

  # 单个文件，指定输出路径
  python kml_ge_compat.py track.kml -o D:/output/track_fixed.kml

  # 批量处理目录下所有 .kml（自动跳过已转换的 _ge_compat.kml）
  python kml_ge_compat.py -d D:/kmls

  # 强制覆盖已存在的输出文件
  python kml_ge_compat.py track.kml --overwrite
"""
    )
    parser.add_argument('inputs', nargs='*', help='KML 文件路径（可多个，支持通配符）')
    parser.add_argument('-o', '--output', help='输出文件路径（仅当输入为单个文件时有效）')
    parser.add_argument('-d', '--directory', help='批量处理指定目录下所有 .kml 文件')
    parser.add_argument('--overwrite', action='store_true', help='覆盖已存在的输出文件')

    args = parser.parse_args()

    files = collect_input_files(args)
    if not files:
        parser.print_help()
        sys.exit(1)

    if args.output and len(files) > 1:
        print("❌ -o 仅在处理单个文件时可用；批量场景请改用 -d 让脚本自动命名")
        sys.exit(1)

    success = 0
    skipped = 0
    for input_path in files:
        output_path = args.output if args.output else make_default_output_path(input_path)
        print('\n' + '=' * 60)
        if process_file(input_path, output_path, args.overwrite):
            success += 1
        else:
            skipped += 1

    print('\n' + '=' * 60)
    print(f"📊 处理完成：成功 {success} / 跳过 {skipped} / 总计 {len(files)}")


if __name__ == '__main__':
    main()
