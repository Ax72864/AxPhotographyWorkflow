#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
图片序列转视频工具
将目录下的图片（含RAW格式）按文件名排序后，使用ffmpeg合成为视频。

依赖:
    - ffmpeg (需要安装并加入PATH)
    - Pillow (pip install Pillow)
    - rawpy  (pip install rawpy，用于处理RAW文件)

用法:
    python images_to_video.py <目录路径>
    python images_to_video.py <目录路径> --fps 30 --resolution 1920x1080
    python images_to_video.py <目录路径> --fps 24 --resolution 3840x2160 --codec h265
"""

import os
import sys
import argparse
import subprocess
import shutil
import tempfile
import re
from typing import List, Tuple, Optional

# ================= 支持的格式 =================
# 常规图片格式
STANDARD_IMAGE_EXTS = {
    '.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.tif', '.webp'
}

# RAW格式
RAW_IMAGE_EXTS = {
    '.cr2', '.cr3',   # Canon
    '.nef', '.nrw',   # Nikon
    '.arw', '.srf',   # Sony
    '.orf',           # Olympus
    '.rw2',           # Panasonic
    '.dng',           # Adobe DNG
    '.raf',           # Fujifilm
    '.pef',           # Pentax
    '.srw',           # Samsung
    '.x3f',           # Sigma
    '.iiq',           # Phase One
    '.3fr',           # Hasselblad
    '.rwl',           # Leica
    '.kdc', '.dcr',   # Kodak
    '.erf',           # Epson
}

ALL_SUPPORTED_EXTS = STANDARD_IMAGE_EXTS | RAW_IMAGE_EXTS
# =============================================


def natural_sort_key(text: str) -> List:
    """
    自然排序的key函数，正确处理数字排序。
    例如: image1.jpg, image2.jpg, image10.jpg 会按正确顺序排列。
    """
    def convert(t):
        return int(t) if t.isdigit() else t.lower()
    return [convert(c) for c in re.split(r'([0-9]+)', text)]


def get_image_files(directory: str) -> List[str]:
    """
    获取目录下所有支持的图片文件，按文件名自然排序。

    Args:
        directory: 目录路径

    Returns:
        排序后的图片文件完整路径列表
    """
    if not os.path.isdir(directory):
        print(f"错误: '{directory}' 不是有效目录。")
        sys.exit(1)

    image_files = []
    for f in os.listdir(directory):
        file_path = os.path.join(directory, f)
        if os.path.isfile(file_path):
            _, ext = os.path.splitext(f)
            if ext.lower() in ALL_SUPPORTED_EXTS:
                image_files.append(file_path)

    if not image_files:
        print(f"错误: 在 '{directory}' 中未找到支持的图片文件。")
        sys.exit(1)

    image_files.sort(key=lambda x: natural_sort_key(os.path.basename(x)))
    return image_files


def is_raw_file(filepath: str) -> bool:
    """判断文件是否为RAW格式。"""
    _, ext = os.path.splitext(filepath)
    return ext.lower() in RAW_IMAGE_EXTS


def extract_raw_thumbnail(raw_path: str):
    """
    从RAW文件中提取内嵌的JPEG预览图。
    相机在拍摄时会将处理好色彩的JPEG预览嵌入RAW文件中，
    色彩效果远优于简单的rawpy解码。

    Args:
        raw_path: RAW文件路径

    Returns:
        PIL Image对象，提取失败返回None
    """
    try:
        import rawpy
        from PIL import Image
        import io

        with rawpy.imread(raw_path) as raw:
            thumb = raw.extract_thumb()

        if thumb.format == rawpy.ThumbFormat.JPEG:
            # 内嵌的是JPEG数据，直接加载
            img = Image.open(io.BytesIO(thumb.data))
        elif thumb.format == rawpy.ThumbFormat.BITMAP:
            # 内嵌的是位图数据
            img = Image.fromarray(thumb.data)
        else:
            return None

        # 确保RGB模式
        if img.mode != 'RGB':
            img = img.convert('RGB')

        return img

    except Exception:
        return None


def raw_postprocess(raw_path: str):
    """
    使用rawpy解码RAW文件（备用方案，当无法提取内嵌预览时使用）。

    Args:
        raw_path: RAW文件路径

    Returns:
        PIL Image对象，解码失败返回None
    """
    try:
        import rawpy
        from PIL import Image

        with rawpy.imread(raw_path) as raw:
            rgb = raw.postprocess(
                use_camera_wb=True,
                half_size=False,
                no_auto_bright=False,
                output_bps=8
            )

        img = Image.fromarray(rgb)
        return img

    except Exception:
        return None


def convert_raw_to_jpg(raw_path: str, output_path: str, resolution: Optional[Tuple[int, int]] = None) -> bool:
    """
    将RAW文件转换为JPG。
    优先提取RAW内嵌的JPEG预览图（相机处理的色彩更准确），
    若提取失败则回退到rawpy解码。

    Args:
        raw_path: RAW文件路径
        output_path: 输出JPG路径
        resolution: 目标分辨率 (width, height)，None则保持原始尺寸

    Returns:
        是否转换成功
    """
    try:
        import rawpy  # noqa: F401 - 确保rawpy已安装
    except ImportError:
        print("错误: 处理RAW文件需要安装 rawpy 库。")
        print("请运行: pip install rawpy")
        sys.exit(1)

    basename = os.path.basename(raw_path)

    # 优先提取内嵌预览图
    img = extract_raw_thumbnail(raw_path)
    if img is not None:
        source = "内嵌预览"
    else:
        # 回退到rawpy解码
        img = raw_postprocess(raw_path)
        source = "rawpy解码"

    if img is None:
        print(f"警告: 无法处理RAW文件 {basename}")
        return False

    print(f"({source})", end=" ")

    if resolution:
        img = resize_image_fit(img, resolution)

    img.save(output_path, 'JPEG', quality=95)
    return True


def convert_standard_to_jpg(image_path: str, output_path: str, resolution: Optional[Tuple[int, int]] = None) -> bool:
    """
    将常规图片转换为JPG（统一格式和分辨率）。

    Args:
        image_path: 图片文件路径
        output_path: 输出JPG路径
        resolution: 目标分辨率 (width, height)，None则保持原始尺寸

    Returns:
        是否转换成功
    """
    try:
        from PIL import Image

        with Image.open(image_path) as img:
            # 转为RGB
            if img.mode != 'RGB':
                img = img.convert('RGB')

            if resolution:
                img = resize_image_fit(img, resolution)

            img.save(output_path, 'JPEG', quality=95)
        return True

    except Exception as e:
        print(f"警告: 无法处理图片 {os.path.basename(image_path)}: {e}")
        return False


def resize_image_fit(img, target_size: Tuple[int, int]):
    """
    以中心对齐方式裁切/补黑，使图片适配目标分辨率。
    - 图片某维度大于目标：从中心裁切多余部分
    - 图片某维度小于目标：在两侧补黑
    确保输出视频的所有帧尺寸一致。

    Args:
        img: PIL Image对象
        target_size: (width, height)

    Returns:
        调整后的PIL Image对象
    """
    from PIL import Image

    target_w, target_h = target_size
    orig_w, orig_h = img.size

    # 如果尺寸完全一致，直接返回
    if orig_w == target_w and orig_h == target_h:
        return img

    # 计算裁切区域（针对原图中大于目标的维度）
    crop_left = max(0, (orig_w - target_w) // 2)
    crop_top = max(0, (orig_h - target_h) // 2)
    crop_right = min(orig_w, crop_left + target_w)
    crop_bottom = min(orig_h, crop_top + target_h)

    # 先从原图中心裁切
    img_cropped = img.crop((crop_left, crop_top, crop_right, crop_bottom))

    cropped_w, cropped_h = img_cropped.size

    # 如果裁切后已经等于目标尺寸，直接返回
    if cropped_w == target_w and cropped_h == target_h:
        return img_cropped

    # 否则需要补黑（原图某维度小于目标）
    canvas = Image.new('RGB', target_size, (0, 0, 0))
    paste_x = (target_w - cropped_w) // 2
    paste_y = (target_h - cropped_h) // 2
    canvas.paste(img_cropped, (paste_x, paste_y))

    return canvas


def get_first_image_resolution(filepath: str) -> Tuple[int, int]:
    """
    获取第一张图片的分辨率，支持常规图片和RAW格式。

    Args:
        filepath: 图片文件路径

    Returns:
        (width, height) 元组
    """
    if is_raw_file(filepath):
        # 优先从内嵌预览获取分辨率
        img = extract_raw_thumbnail(filepath)
        if img is None:
            img = raw_postprocess(filepath)
        if img is None:
            print(f"错误: 无法读取第一张RAW图片的分辨率")
            sys.exit(1)
        w, h = img.size
    else:
        try:
            from PIL import Image
            with Image.open(filepath) as img:
                w, h = img.size
        except Exception as e:
            print(f"错误: 无法读取第一张图片的分辨率: {e}")
            sys.exit(1)

    # 确保宽高为偶数（视频编码器要求）
    w = w if w % 2 == 0 else w + 1
    h = h if h % 2 == 0 else h + 1
    return (w, h)


def check_ffmpeg() -> str:
    """
    检查ffmpeg是否可用，返回ffmpeg路径。
    """
    ffmpeg_path = shutil.which('ffmpeg')
    if ffmpeg_path is None:
        print("错误: 未找到ffmpeg，请确保已安装并加入PATH。")
        print("下载地址: https://ffmpeg.org/download.html")
        sys.exit(1)
    return ffmpeg_path


def prepare_frames(image_files: List[str],
                   temp_dir: str,
                   resolution: Optional[Tuple[int, int]] = None) -> int:
    """
    将所有图片（含RAW）转换为编号的JPG帧，存入临时目录。

    Args:
        image_files: 图片文件列表
        temp_dir: 临时目录路径
        resolution: 目标分辨率

    Returns:
        成功处理的帧数
    """
    frame_count = 0
    total = len(image_files)

    for i, filepath in enumerate(image_files):
        basename = os.path.basename(filepath)
        output_path = os.path.join(temp_dir, f"frame_{frame_count:06d}.jpg")

        print(f"  [{i + 1}/{total}] 处理: {basename}", end=" ... ")

        if is_raw_file(filepath):
            success = convert_raw_to_jpg(filepath, output_path, resolution)
        else:
            success = convert_standard_to_jpg(filepath, output_path, resolution)

        if success:
            frame_count += 1
            print("完成")
        else:
            print("跳过")

    return frame_count


def run_ffmpeg(ffmpeg_path: str,
               temp_dir: str,
               output_path: str,
               fps: float,
               resolution: Optional[Tuple[int, int]],
               codec: str = "h264",
               crf: int = 18) -> None:
    """
    调用ffmpeg将帧序列合成为视频。

    Args:
        ffmpeg_path: ffmpeg可执行文件路径
        temp_dir: 帧图片所在的临时目录
        output_path: 输出视频路径
        fps: 帧率
        resolution: 分辨率 (width, height)，如果帧已预处理则可为None
        codec: 编码器 (h264 / h265)
        crf: 质量参数 (越小质量越高，推荐18-23)
    """
    input_pattern = os.path.join(temp_dir, "frame_%06d.jpg")

    cmd = [
        ffmpeg_path,
        '-y',                          # 覆盖输出文件
        '-framerate', str(fps),        # 输入帧率
        '-i', input_pattern,           # 输入帧序列
        '-c:v',                        # 视频编码器
    ]

    # 选择编码器
    if codec.lower() == 'h265' or codec.lower() == 'hevc':
        cmd.append('libx265')
        cmd.extend(['-tag:v', 'hvc1'])  # 兼容Apple设备播放
    else:
        cmd.append('libx264')

    cmd.extend([
        '-crf', str(crf),             # 质量
        '-pix_fmt', 'yuv420p',         # 像素格式（兼容性最好）
    ])

    # 如果帧未预处理分辨率，在这里通过ffmpeg缩放
    if resolution:
        w, h = resolution
        # 确保宽高为偶数（编码器要求）
        w = w if w % 2 == 0 else w + 1
        h = h if h % 2 == 0 else h + 1
        cmd.extend(['-vf', f'scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:black'])

    cmd.append(output_path)

    print(f"\n正在合成视频...")
    print(f"  命令: {' '.join(cmd)}")

    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        print(f"错误: ffmpeg执行失败:")
        print(result.stderr)
        sys.exit(1)


def parse_resolution(resolution_str: str) -> Tuple[int, int]:
    """
    解析分辨率字符串，格式: WIDTHxHEIGHT

    Args:
        resolution_str: 如 "1920x1080"

    Returns:
        (width, height) 元组
    """
    try:
        parts = resolution_str.lower().split('x')
        if len(parts) != 2:
            raise ValueError()
        w, h = int(parts[0]), int(parts[1])
        if w <= 0 or h <= 0:
            raise ValueError()
        # 确保宽高为偶数
        w = w if w % 2 == 0 else w + 1
        h = h if h % 2 == 0 else h + 1
        return (w, h)
    except ValueError:
        print(f"错误: 无效的分辨率格式 '{resolution_str}'，请使用格式如 '1920x1080'")
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(
        description='将目录下的图片（含RAW格式）按文件名排序合成为视频',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python images_to_video.py ./photos/
  python images_to_video.py ./photos/ --fps 30
  python images_to_video.py ./photos/ --fps 24 --resolution 1920x1080
  python images_to_video.py ./photos/ --fps 24 --resolution 3840x2160 --codec h265
  python images_to_video.py ./photos/ --fps 30 --crf 20 --output my_video.mp4

支持的图片格式:
  常规: jpg, jpeg, png, bmp, tiff, tif, webp
  RAW:  cr2, cr3, nef, arw, orf, rw2, dng, raf, pef, srw 等
        """
    )

    parser.add_argument(
        'directory',
        help='包含图片的目录路径'
    )

    parser.add_argument(
        '--fps',
        type=float,
        default=24.0,
        help='帧率 (默认: 24)'
    )

    parser.add_argument(
        '--resolution',
        type=str,
        default=None,
        help='输出视频分辨率，格式如 "1920x1080"（默认: 使用第一张图片的分辨率）'
    )

    parser.add_argument(
        '--codec',
        type=str,
        choices=['h264', 'h265', 'hevc'],
        default='h264',
        help='视频编码器 (默认: h264)'
    )

    parser.add_argument(
        '--crf',
        type=int,
        default=18,
        help='视频质量CRF值，越小越好 (默认: 18，推荐范围: 15-28)'
    )

    parser.add_argument(
        '--output',
        type=str,
        default=None,
        help='输出视频文件名 (默认: output.mp4，保存到输入目录下)'
    )

    args = parser.parse_args()

    # 检查ffmpeg
    ffmpeg_path = check_ffmpeg()
    print(f"使用ffmpeg: {ffmpeg_path}")

    # 验证参数
    if args.fps <= 0:
        print("错误: 帧率必须大于0")
        sys.exit(1)

    if args.crf < 0 or args.crf > 51:
        print("错误: CRF值范围为0-51")
        sys.exit(1)

    # 获取图片列表
    directory = os.path.abspath(args.directory)
    image_files = get_image_files(directory)
    print(f"\n找到 {len(image_files)} 张图片")

    # 检查是否包含RAW文件
    raw_count = sum(1 for f in image_files if is_raw_file(f))
    std_count = len(image_files) - raw_count
    if raw_count > 0:
        print(f"  其中常规图片: {std_count} 张, RAW文件: {raw_count} 张")

    # 解析分辨率：指定则使用指定值，否则自动取第一张图片的分辨率
    if args.resolution:
        resolution = parse_resolution(args.resolution)
        print(f"\n使用指定分辨率: {resolution[0]}x{resolution[1]}")
    else:
        resolution = get_first_image_resolution(image_files[0])
        print(f"\n未指定分辨率，使用第一张图片的分辨率: {resolution[0]}x{resolution[1]}")
        print(f"  (来源: {os.path.basename(image_files[0])})")

    # 确定输出路径
    if args.output:
        output_filename = args.output
    else:
        output_filename = "output.mp4"

    output_path = os.path.join(directory, output_filename)

    # 显示参数
    print(f"\n生成参数:")
    print(f"  帧率:     {args.fps} fps")
    print(f"  分辨率:   {resolution[0]}x{resolution[1]}")
    print(f"  编码器:   {args.codec}")
    print(f"  质量CRF:  {args.crf}")
    print(f"  输出文件: {output_path}")

    # 创建临时目录
    temp_dir = tempfile.mkdtemp(prefix="img2video_")
    print(f"\n临时目录: {temp_dir}")

    try:
        # 步骤1: 预处理所有图片为统一JPG帧
        print(f"\n[步骤1/2] 预处理图片帧...")
        frame_count = prepare_frames(image_files, temp_dir, resolution)

        if frame_count == 0:
            print("错误: 没有成功处理任何图片。")
            sys.exit(1)

        print(f"\n成功预处理 {frame_count} 帧")

        # 步骤2: 使用ffmpeg合成视频
        print(f"\n[步骤2/2] 合成视频...")
        # 帧已经预处理好分辨率了，不再需要ffmpeg缩放
        run_ffmpeg(
            ffmpeg_path=ffmpeg_path,
            temp_dir=temp_dir,
            output_path=output_path,
            fps=args.fps,
            resolution=None,  # 帧已预处理
            codec=args.codec,
            crf=args.crf
        )

        # 显示结果
        if os.path.exists(output_path):
            file_size_mb = os.path.getsize(output_path) / (1024 * 1024)
            duration = frame_count / args.fps
            print(f"\n视频生成成功!")
            print(f"  文件: {output_path}")
            print(f"  大小: {file_size_mb:.2f} MB")
            print(f"  时长: {duration:.1f} 秒 ({frame_count} 帧 @ {args.fps} fps)")
        else:
            print("错误: 视频文件未生成。")
            sys.exit(1)

    finally:
        # 清理临时目录
        print(f"\n清理临时文件...")
        shutil.rmtree(temp_dir, ignore_errors=True)

    print("完成!")


if __name__ == "__main__":
    main()
