import os
import io
import re
import base64
import argparse
import numpy as np
from PIL import Image
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

DASHSCOPE_API_KEY = "sk-403a18586feb4c8faa7cae7a45836771"
DASHSCOPE_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
ORIENTATION_MODEL = "qwen3-vl-plus"

# 半格底片最大长宽比 (24mm / 17mm)
MAX_HALF_FRAME_RATIO = 24.0 / 17.0  # ≈ 1.412

# LLM 客户端延迟初始化，避免 import 失败影响基础功能
_llm_client = None
_llm_available = None


def _get_llm_client():
    """延迟初始化 LLM 客户端，返回 (client, available) 元组。"""
    global _llm_client, _llm_available
    if _llm_available is not None:
        return _llm_client, _llm_available
    try:
        from openai import OpenAI
        _llm_client = OpenAI(
            api_key=DASHSCOPE_API_KEY,
            base_url=DASHSCOPE_BASE_URL,
        )
        _llm_available = True
    except Exception as e:
        print(f"[警告] LLM 初始化失败 ({e})，将跳过朝向检测")
        _llm_client = None
        _llm_available = False
    return _llm_client, _llm_available


def _make_orientation_grid(img):
    """
    将图片的 4 种旋转版本拼成 2x2 网格，每个格子标注 A/B/C/D。
    A=原图(0°), B=顺时针90°, C=180°, D=顺时针270°
    返回拼接后的 PIL Image。
    """
    from PIL import ImageDraw, ImageFont

    thumb = img.copy()
    thumb.thumbnail((768, 768), Image.LANCZOS)
    if thumb.mode != 'RGB':
        thumb = thumb.convert('RGB')

    # 生成4种旋转（PIL rotate 是逆时针，所以顺时针90° = rotate(-90) = rotate(270)）
    rot_0 = thumb
    rot_90 = thumb.rotate(-90, expand=True)   # 顺时针90°
    rot_180 = thumb.rotate(180, expand=True)    # 180°
    rot_270 = thumb.rotate(90, expand=True)     # 顺时针270°

    # 统一尺寸为4张中的最大尺寸
    sizes = [rot_0.size, rot_90.size, rot_180.size, rot_270.size]
    cell_w = max(s[0] for s in sizes)
    cell_h = max(s[1] for s in sizes)

    label_height = 36
    padding = 4
    grid_w = cell_w * 2 + padding
    grid_h = (cell_h + label_height) * 2 + padding

    grid = Image.new('RGB', (grid_w, grid_h), (40, 40, 40))
    draw = ImageDraw.Draw(grid)

    rotations = [
        (rot_0, "A"),
        (rot_90, "B"),
        (rot_180, "C"),
        (rot_270, "D"),
    ]

    for idx, (r_img, label) in enumerate(rotations):
        col = idx % 2
        row = idx // 2
        x = col * (cell_w + padding)
        y = row * (cell_h + label_height + padding)

        # 在标签区域绘制标签
        draw.rectangle([x, y, x + cell_w, y + label_height], fill=(60, 60, 60))
        draw.text((x + 10, y + 4), label, fill=(255, 255, 255))

        # 居中放置旋转图片
        px = x + (cell_w - r_img.size[0]) // 2
        py = y + label_height + (cell_h - r_img.size[1]) // 2
        grid.paste(r_img, (px, py))

    return grid


_ORIENT_PROMPT = (
    "This 2x2 grid shows the SAME photograph in 4 orientations: A, B, C, D.\n"
    "A = original, B = CW 90, C = 180, D = CW 270.\n\n"
    "Pick the ONE orientation where the photo looks CORRECT and NATURAL:\n"
    "1) People stand upright (heads UP, feet DOWN)\n"
    "2) Text/signs read normally left-to-right (not sideways or flipped)\n"
    "3) Buildings/trees/poles point UPWARD\n"
    "4) Sky is at TOP, ground at BOTTOM\n"
    "5) In sideways orientations (90/270), vertical things appear horizontal - "
    "that is WRONG\n\n"
    "CRITICAL: Only ONE of A/B/C/D can be correct. If two look similar, the one "
    "where people stand upright and text reads correctly is the answer.\n\n"
    "Reply with ONLY one letter: A, B, C, or D"
)

_CHOICE_TO_CW = {"A": 0, "B": 90, "C": 180, "D": 270}


def _single_orientation_call(client, b64_data):
    """发送单次朝向检测请求，返回选择字母 (A/B/C/D) 或 None。"""
    try:
        response = client.chat.completions.create(
            model=ORIENTATION_MODEL,
            messages=[{
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/jpeg;base64,{b64_data}"
                        }
                    },
                    {"type": "text", "text": _ORIENT_PROMPT}
                ]
            }],
            max_tokens=10,
            extra_body={"enable_thinking": False},
        )
        answer = response.choices[0].message.content.strip().upper()
        match = re.search(r'[ABCD]', answer)
        return match.group(0) if match else None
    except Exception:
        return None


def detect_orientation_llm(img, votes=3):
    """
    使用 LLM 视觉模型检测照片朝向（四格对比 + 多数投票）。

    将图片的 4 种旋转版本拼成 2x2 网格发送给模型，让模型对比选择
    哪个方向是正确的。发送 votes 次请求并取多数投票结果，
    提高判断一致性。

    返回旋转角度（PIL.Image.rotate 的逆时针角度：0/90/180/270），
    如果 LLM 不可用则返回 None。
    """
    client, available = _get_llm_client()
    if not available:
        return None

    grid = _make_orientation_grid(img)

    buf = io.BytesIO()
    grid.save(buf, format='JPEG', quality=80)
    b64_data = base64.b64encode(buf.getvalue()).decode('utf-8')

    # 并行发送多次请求进行多数投票
    results = []
    with ThreadPoolExecutor(max_workers=votes) as pool:
        futures = [pool.submit(_single_orientation_call, client, b64_data)
                   for _ in range(votes)]
        for f in as_completed(futures):
            r = f.result()
            if r is not None:
                results.append(r)

    if not results:
        return None

    # 多数投票
    choice = Counter(results).most_common(1)[0][0]
    cw_angle = _CHOICE_TO_CW[choice]
    pil_angle = (360 - cw_angle) % 360
    return pil_angle


def auto_orient(img, label=""):
    """
    自动检测并纠正图片朝向。LLM 不可用时原样返回。
    返回 (corrected_img, rotation_angle)。
    """
    rotation = detect_orientation_llm(img)
    if rotation is None or rotation == 0:
        if label:
            print(f"  {label}: {img.size[0]}x{img.size[1]} (无需旋转)")
        return img, 0

    corrected = img.rotate(rotation, expand=True)
    if label:
        print(f"  {label}: {img.size[0]}x{img.size[1]} -> 旋转 {rotation}° -> {corrected.size[0]}x{corrected.size[1]}")
    return corrected, rotation


def _extract_exif(img):
    """
    从 PIL Image 中提取 EXIF 数据（bytes 形式），用于 save() 时传入。
    如果无 EXIF 则返回 None。
    """
    exif_data = img.info.get("exif")
    if exif_data:
        return exif_data

    try:
        exif_obj = img.getexif()
        if exif_obj:
            return exif_obj.tobytes()
    except Exception:
        pass
    return None


def _reset_orientation_in_exif(exif_bytes):
    """
    将 EXIF 中的 Orientation 标签重置为 1（正常），
    避免查看器对已旋转的图片再次旋转。
    返回修改后的 exif bytes；如果解析失败则返回原始 bytes。
    """
    if not exif_bytes:
        return exif_bytes
    try:
        from PIL.Image import Exif
        exif_obj = Exif()
        exif_obj.load(exif_bytes)
        ORIENTATION_TAG = 0x0112
        if ORIENTATION_TAG in exif_obj:
            exif_obj[ORIENTATION_TAG] = 1
        return exif_obj.tobytes()
    except Exception:
        return exif_bytes


def _save_with_exif(img, path, quality, exif_bytes=None):
    """保存 JPG，附带 EXIF（如果有）。"""
    kwargs = {'quality': quality}
    if exif_bytes:
        kwargs['exif'] = exif_bytes
    img.save(path, 'JPEG', **kwargs)


def ensure_rgb(img):
    """确保图片为 RGB 模式（JPG 不支持 RGBA/P）。"""
    if img.mode in ('RGBA', 'P'):
        return img.convert('RGB')
    return img


def _detect_gap_for_single(arr):
    """
    对单张图片检测中间间隙位置。

    使用中间1/3高度的条带来分析列亮度，在宽度中心30%范围内
    寻找连续的低亮度区域（<15）作为间隙。

    返回 (gap_start, gap_end) 或 None（如果检测失败，通常说明是暗照片）。
    """
    h, w = arr.shape[:2]
    h3 = h // 3
    strip = arr[h3:2 * h3, :, :]
    col_mean = np.mean(strip, axis=(0, 2))
    avg_brightness = np.mean(col_mean)

    # 只对亮度足够的照片尝试检测（暗照片会回退到整卷平均位置）
    if avg_brightness < 60:
        return None

    from scipy.ndimage import gaussian_filter1d
    smoothed = gaussian_filter1d(col_mean, sigma=15)

    # 在中心 35%-65% 范围搜索
    search_start = int(w * 0.35)
    search_end = int(w * 0.65)

    # 寻找连续低亮度区域 (<15)
    in_gap = False
    best_gap = None
    best_width = 0
    gap_s = 0

    for c in range(search_start, search_end):
        if smoothed[c] < 15 and not in_gap:
            gap_s = c
            in_gap = True
        elif (smoothed[c] >= 15 or c == search_end - 1) and in_gap:
            gap_e = c - 1 if smoothed[c] >= 15 else c
            gap_w = gap_e - gap_s + 1
            if gap_w > best_width and gap_w >= 80:
                best_gap = (gap_s, gap_e)
                best_width = gap_w
            in_gap = False

    return best_gap


def detect_gap_positions(tif_files, directory):
    """
    两阶段间隙检测：先从亮照片检测间隙，再为暗照片提供回退位置。

    返回列表 [(gap_start, gap_end), ...] 对应每个文件，
    以及整卷的平均间隙位置。
    """
    detected_gaps = []
    brightnesses = []

    for f in tif_files:
        path = os.path.join(directory, f)
        img = Image.open(path)
        arr = np.array(img)

        gap = _detect_gap_for_single(arr)
        detected_gaps.append(gap)

        h, w = arr.shape[:2]
        h3 = h // 3
        strip = arr[h3:2 * h3, :, :]
        avg = np.mean(strip)
        brightnesses.append(avg)
        img.close()

    # 从成功检测的间隙中计算平均位置
    valid_gaps = [g for g in detected_gaps if g is not None]

    if valid_gaps:
        avg_start = int(np.mean([g[0] for g in valid_gaps]))
        avg_end = int(np.mean([g[1] for g in valid_gaps]))
    else:
        # 所有照片都太暗，使用图片宽度的中点 ± 估计间隙半宽
        w = arr.shape[1]
        avg_start = w // 2 - 120
        avg_end = w // 2 + 120

    avg_gap = (avg_start, avg_end)

    # 为检测失败的暗照片填充平均间隙位置
    final_gaps = []
    for i, gap in enumerate(detected_gaps):
        if gap is not None:
            final_gaps.append(gap)
        else:
            final_gaps.append(avg_gap)

    return final_gaps, avg_gap


def trim_vertical_borders(img_arr, threshold=25, max_trim_ratio=0.08):
    """
    只裁切上下边缘的暗色黑边（胶片扫描产生的黑边），
    不裁切左右边缘（左右边缘是照片有效内容）。

    max_trim_ratio 限制最多裁切的比例，避免暗照片被过度裁切。
    """
    h, w = img_arr.shape[:2]
    max_trim = int(h * max_trim_ratio)

    row_brightness = np.mean(img_arr, axis=(1, 2))

    top = 0
    while top < max_trim and row_brightness[top] < threshold:
        top += 1

    bottom = h - 1
    while bottom > h - 1 - max_trim and row_brightness[bottom] < threshold:
        bottom -= 1

    if bottom <= top:
        return img_arr

    return img_arr[top:bottom + 1, :, :]


def clamp_aspect_ratio(img_arr, max_ratio=MAX_HALF_FRAME_RATIO):
    """
    确保裁切后的图片长宽比不超过半格底片的物理极限 (24/17 ≈ 1.41)。
    如果超出，从长边两侧等量裁切。
    """
    h, w = img_arr.shape[:2]
    ratio = max(h, w) / max(min(h, w), 1)

    if ratio <= max_ratio:
        return img_arr

    if h > w:
        target_h = int(w * max_ratio)
        excess = h - target_h
        top = excess // 2
        return img_arr[top:top + target_h, :, :]
    else:
        target_w = int(h * max_ratio)
        excess = w - target_w
        left = excess // 2
        return img_arr[:, left:left + target_w, :]


def split_half_frame(img, gap_start, gap_end):
    """
    按给定的间隙位置将图片切分为左右两张半格照片。

    只裁切上下方向的黑边，不裁切左右方向。
    确保最终长宽比不超过半格底片极限。

    返回 (left_img, right_img) 两个 PIL Image 对象。
    """
    arr = np.array(img)

    left_arr = arr[:, :gap_start, :]
    right_arr = arr[:, gap_end + 1:, :]

    # 只裁切上下黑边
    left_arr = trim_vertical_borders(left_arr)
    right_arr = trim_vertical_borders(right_arr)

    # 确保比例不超过物理极限
    left_arr = clamp_aspect_ratio(left_arr)
    right_arr = clamp_aspect_ratio(right_arr)

    return Image.fromarray(left_arr), Image.fromarray(right_arr)


def _orient_and_save(img, output_path, quality, label, exif_bytes=None):
    """对单张图片执行朝向检测、旋转并保存。在线程池中调用。"""
    oriented, rotation = auto_orient(img, label=label)
    save_exif = _reset_orientation_in_exif(exif_bytes) if rotation else exif_bytes
    _save_with_exif(oriented, output_path, quality, save_exif)
    return output_path, rotation


def convert_tif_to_jpg(directory, quality=92, half_frame=False, output_dir=None,
                       max_workers=4):
    """
    将指定目录下的所有 tif/tiff 文件转换为 jpg。
    当 half_frame=True 时，每张 TIF 会被切分为两张独立的半格照片。
    所有输出图片都会通过 LLM 进行朝向检测并自动纠正。

    处理流程（优化后）：
    1. 先批量完成所有格式转换/切分（CPU 密集，无需等待网络）
    2. 再并行发送 LLM 朝向检测请求（IO 密集，并行提速）
    """
    if not os.path.exists(directory):
        print(f"错误: 目录 '{directory}' 不存在。")
        return

    if output_dir is None:
        output_dir = os.path.join(directory, "out")
    os.makedirs(output_dir, exist_ok=True)
    print(f"输出目录: {os.path.abspath(output_dir)}")

    _, llm_ok = _get_llm_client()
    if llm_ok:
        print(f"朝向检测: 已启用 (模型: {ORIENTATION_MODEL}, 并发数: {max_workers})")
    else:
        print("朝向检测: 已禁用 (LLM 不可用)")

    tif_extensions = ('.tif', '.tiff', '.TIF', '.TIFF')
    files = sorted([f for f in os.listdir(directory) if f.endswith(tif_extensions)])

    if not files:
        print(f"在 '{directory}' 中没有找到 TIF 文件。")
        return

    mode_text = "半格切分模式" if half_frame else "标准模式"
    print(f"找到 {len(files)} 个文件，准备开始转换 ({mode_text}, 质量: {quality})...")
    print("-" * 60)

    # ========== 阶段一：批量格式转换 & 切分 ==========
    print("\n[阶段 1/2] 格式转换" + (" & 半格切分..." if half_frame else "..."))

    if half_frame:
        print("  正在检测整卷间隙位置...")
        gap_positions, avg_gap = detect_gap_positions(files, directory)
        print(f"  整卷平均间隙位置: 列 {avg_gap[0]} ~ {avg_gap[1]} "
              f"(宽度 {avg_gap[1] - avg_gap[0] + 1} 像素)")

    # (image, output_path, label) 待朝向检测的任务列表
    orient_tasks = []
    success_count = 0

    for i, filename in enumerate(files):
        file_path = os.path.join(directory, filename)
        file_name_without_ext = os.path.splitext(filename)[0]

        try:
            with Image.open(file_path) as img:
                exif_bytes = _extract_exif(img)
                img = ensure_rgb(img)

                if half_frame:
                    gap_s, gap_e = gap_positions[i]
                    left_img, right_img = split_half_frame(img, gap_s, gap_e)

                    left_path = os.path.join(output_dir,
                                             file_name_without_ext + "_a.jpg")
                    right_path = os.path.join(output_dir,
                                              file_name_without_ext + "_b.jpg")

                    print(f"  [{i+1}/{len(files)}] {filename} -> "
                          f"切分 (间隙 {gap_s}~{gap_e}) -> "
                          f"左 {left_img.size[0]}x{left_img.size[1]}, "
                          f"右 {right_img.size[0]}x{right_img.size[1]}")

                    orient_tasks.append(
                        (left_img, left_path,
                         f"{file_name_without_ext}_a", exif_bytes))
                    orient_tasks.append(
                        (right_img, right_path,
                         f"{file_name_without_ext}_b", exif_bytes))
                else:
                    output_path = os.path.join(output_dir,
                                               file_name_without_ext + ".jpg")
                    print(f"  [{i+1}/{len(files)}] {filename} "
                          f"({img.size[0]}x{img.size[1]})")

                    # 需要复制一份，因为 with 块退出后 img 关闭
                    img_copy = img.copy()
                    orient_tasks.append(
                        (img_copy, output_path, file_name_without_ext,
                         exif_bytes))

                success_count += 1
        except Exception as e:
            print(f"  [失败] {filename}: {e}")

    print(f"\n  格式转换完成: {success_count}/{len(files)} 个文件, "
          f"共 {len(orient_tasks)} 张待处理图片")

    # ========== 阶段二：并行朝向检测 & 保存 ==========
    if not llm_ok:
        print("\n[阶段 2/2] 保存图片 (朝向检测已跳过)...")
        for img, path, label, exif in orient_tasks:
            _save_with_exif(img, path, quality, exif)
            print(f"  -> {os.path.basename(path)}")
    else:
        print(f"\n[阶段 2/2] 并行朝向检测 & 保存 (并发数: {max_workers})...")
        completed = 0
        total = len(orient_tasks)

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {}
            for img, path, label, exif in orient_tasks:
                future = executor.submit(
                    _orient_and_save, img, path, quality, label, exif)
                futures[future] = label

            for future in as_completed(futures):
                label = futures[future]
                completed += 1
                try:
                    path, rotation = future.result()
                    rot_text = f"旋转{rotation}°" if rotation else "无旋转"
                    print(f"  [{completed}/{total}] {os.path.basename(path)} "
                          f"({rot_text})")
                except Exception as e:
                    print(f"  [{completed}/{total}] {label}: 处理失败 ({e})")

    print("-" * 60)
    print(f"全部完成。成功处理: {success_count}/{len(files)} 个文件，"
          f"输出 {len(orient_tasks)} 张照片")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="将目录中的所有 TIF 文件转换为 JPG。")

    parser.add_argument("directory", help="包含 TIF 文件的目录路径")

    parser.add_argument("-q", "--quality", type=int, default=92,
                        help="JPG 输出质量 (1-95)，默认为 92")

    parser.add_argument("--half-frame", "--split", action="store_true",
                        help="半格照片切分模式：将每张图切分为两张独立照片")

    parser.add_argument("-o", "--output", default=None,
                        help="输出目录路径 (默认: 输入目录/out/)")

    parser.add_argument("-w", "--workers", type=int, default=4,
                        help="LLM 朝向检测并发数 (默认: 4)")

    args = parser.parse_args()

    convert_tif_to_jpg(args.directory, args.quality, args.half_frame,
                       args.output, args.workers)
