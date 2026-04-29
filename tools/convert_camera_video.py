import os
import sys
import time
import subprocess
from win32file import CreateFile, SetFileTime, GetFileTime, CloseHandle
from win32file import GENERIC_READ, GENERIC_WRITE, OPEN_EXISTING
from pywintypes import Time

def get_video_info(src):
    cmd = ['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=codec_name,pix_fmt,bit_rate', '-of', 'default=nokey=1:noprint_wrappers=1', src]
    result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    info = result.stdout.split()
    return {
        'codec_name': info[0],
        'pix_fmt': info[1],
        'bit_rate': int(info[2]) if len(info) > 2 and info[2].isdigit() else None
    }


def is_10bit_pix_fmt(pix_fmt):
    """判断像素格式是否为 10bit 位深"""
    fmt = (pix_fmt or '').lower()
    return '10le' in fmt or '10be' in fmt or 'p010' in fmt


def pick_encoder(pix_fmt, force=None):
    """智能选择 H.265 编码器：NVENC 硬编 或 x265 软编

    决策依据（force 优先）：
    - force='gpu'  -> 强制 NVENC（无视精度损失）
    - force='cpu'  -> 强制 x265 软编（最高画质）
    - 4:2:2 / 4:4:4 / RGB / 12bit+ -> x265 软编（保留色度精度，NVENC 不支持或会下采样）
    - 其余 4:2:0（含 10bit p010le）-> NVENC（速度优先，10-30 倍提速）
    """
    if force == 'gpu':
        return 'nvenc'
    if force == 'cpu':
        return 'cpu'

    fmt = (pix_fmt or '').lower()
    if '422' in fmt or '444' in fmt or 'rgb' in fmt or 'bgr' in fmt or 'gbr' in fmt:
        return 'cpu'
    if '12le' in fmt or '12be' in fmt or '16le' in fmt or '16be' in fmt:
        return 'cpu'
    return 'nvenc'


def pick_software_pix_fmt(pix_fmt):
    """根据源像素格式选择 x265 软编对应的 pix_fmt（保留色度采样率与位深，避免无谓上采样）"""
    fmt = (pix_fmt or '').lower()
    is_high_bit = (
        is_10bit_pix_fmt(fmt)
        or '12le' in fmt or '12be' in fmt
        or '16le' in fmt or '16be' in fmt
    )

    if '444' in fmt:
        return 'yuv444p10le' if is_high_bit else 'yuv444p'
    if '422' in fmt:
        return 'yuv422p10le' if is_high_bit else 'yuv422p'
    return 'yuv420p10le' if is_high_bit else 'yuv420p'


def modifyFileTime(filePath, createTime, modifyTime, accessTime, offset):
    try:    
        fh = CreateFile(filePath, GENERIC_READ | GENERIC_WRITE, 0, None, OPEN_EXISTING, 0, 0)
        # createTimes, accessTimes, modifyTimes = GetFileTime(fh)
        # createTimes = Time(time.mktime(time.localtime(time.mktime(time.strptime(str(createTime), "%Y-%m-%d %H:%M:%S")) + offset)))
        SetFileTime(fh, createTime, modifyTime, accessTime)
        CloseHandle(fh)
        print(f"copy file time {filePath} success")
    except Exception as e:
        print(e)


def convert(src, format="265", force=None):
    if src.lower().endswith(f'_h{format}.mp4'):
        print(f'{src} is converted video, skip')
        return

    dir_path = os.path.dirname(src)
    ext = os.path.splitext(src)[1]
    dst = os.path.join(dir_path, os.path.basename(src).replace(ext, f'_h{format}.mp4'))
    if os.path.exists(dst):
        print(f'{dst} has been converted, skip')
        return

    duration = get_video_duration(src)
    src_size = os.path.getsize(src)
    src_filename = os.path.basename(src)

    video_info = get_video_info(src)
    bit_rate = video_info['bit_rate']
    pix_fmt = video_info['pix_fmt']
    codec_name = video_info['codec_name']

    scheme = "未知"
    cmd = ""

    if format == "265":
        encoder = pick_encoder(pix_fmt, force)

        if bit_rate and bit_rate < 200000:
            maxrate = f'-maxrate {bit_rate} -bufsize {bit_rate // 2}'
        else:
            maxrate = '-maxrate 20M -bufsize 40M'

        if encoder == 'nvenc':
            # 方案 A: NVENC HEVC 硬编（4:2:0 源走此路，速度优先 10-30 倍提速）
            if is_10bit_pix_fmt(pix_fmt):
                nv_pix_fmt = 'p010le'
                profile = 'main10'
            else:
                nv_pix_fmt = 'yuv420p'
                profile = 'main'
            scheme = f"[265] NVENC 硬编 ({nv_pix_fmt}, {profile})"
            cmd = (
                f'ffmpeg -hwaccel cuda -i "{src}" '
                f'-vcodec hevc_nvenc -tag:v hvc1 '
                f'-pix_fmt {nv_pix_fmt} -profile:v {profile} '
                f'-rc:v vbr -cq 20 -b:v 0 {maxrate} '
                f'-preset p5 -tune hq '
                f'-spatial-aq 1 -temporal-aq 1 -rc-lookahead 32 '
                f'-acodec aac -ab 192k -map_metadata 0 "{dst}"'
            )
        else:
            # 方案 B: x265 软编（4:2:2/4:4:4/12bit+ 源或 force=cpu 时走此路，保留色度精度）
            sw_pix_fmt = pick_software_pix_fmt(pix_fmt)
            scheme = f"[265] x265 软编 ({sw_pix_fmt})"
            cmd = (
                f'ffmpeg -i "{src}" '
                f'-vcodec libx265 -tag:v hvc1 '
                f'-pix_fmt {sw_pix_fmt} -crf 20 {maxrate} '
                f'-preset fast -x265-params "pools=*:frame-threads=4" '
                f'-acodec aac -ab 192k -map_metadata 0 "{dst}"'
            )
    # if format == "265":
    #     option = "-tag:v hvc1 -pix_fmt yuv422p10le"
    #     # cmd = f'ffmpeg -hwaccel qsv -i "{src}" -vcodec libx{format} {option} -b:v 200k -crf 20 -maxrate 20M -bufsize 16M -preset fast -acodec aac -ab 192k -threads 10 "{dst}"'
    #     cmd = f'ffmpeg -i "{src}" -vcodec libx265 {option} -crf 20 -maxrate 20M -bufsize 16M -preset fast -acodec aac -ab 192k -threads 10 "{dst}"'
    elif format == "264":
        option = "-pix_fmt yuv420p"
        if bit_rate and bit_rate < 200000:
            maxrate = f'-maxrate {bit_rate} -bufsize {bit_rate // 2}'
        else:
            maxrate = '-maxrate 20M -bufsize 40M'
        scheme = "[264] NVENC 硬编 (yuv420p)"
        cmd = f'ffmpeg -hwaccel cuda -i "{src}" -vcodec h264_nvenc {option} -rc:v vbr -cq 20 -b:v 0 {maxrate} -preset p7 -tune hq -acodec aac -ab 192k -threads 10 "{dst}"'

    print("=" * 70)
    print(f"开始转换: {src_filename}")
    print(f"  源视频: {codec_name} {pix_fmt} | {format_bitrate(bit_rate)} | {format_duration(duration)} | {format_size(src_size)}")
    print(f"  方案:   {scheme}")
    print(f"  目标:   {os.path.basename(dst)}")
    print(f"  命令:   {cmd}")
    print("=" * 70)

    start_time = time.time()
    ret = os.system(cmd)
    elapsed = time.time() - start_time

    print("=" * 70)
    if ret == 0 and os.path.exists(dst):
        dst_size = os.path.getsize(dst)
        compress_pct = (dst_size / src_size * 100) if src_size else 0
        speed_x = (duration / elapsed) if elapsed else 0
        avg_bitrate = (dst_size * 8 / duration) if duration else 0

        print(f"转换完成: {src_filename}")
        print(f"  方案:   {scheme}")
        print(f"  耗时:   {format_duration(elapsed)}  (实时倍速 {speed_x:.2f}x)")
        print(f"  大小:   {format_size(src_size)} -> {format_size(dst_size)}  (压缩至 {compress_pct:.1f}%)")
        print(f"  码率:   {format_bitrate(bit_rate)} -> {format_bitrate(avg_bitrate)}")
        print(f"  输出:   {dst}")
    else:
        print(f"转换失败: {src_filename}  (退出码 {ret})")
        print(f"  方案:   {scheme}")
        print(f"  耗时:   {format_duration(elapsed)}")
    print("=" * 70)

    a_ctime = os.path.getctime(src)
    a_mtime = os.path.getmtime(src)
    # os.utime(dst, (a_ctime, a_mtime))
    modifyFileTime(dst, Time(a_ctime), Time(a_mtime), Time(a_mtime), 0)

def concatenate_videos(file_list, output):
    print(file_list,output)    
    if os.path.exists(output):
        print(f'{output} exists, skip concatenate')
        return
    with open("file_list.txt", "w") as f:
        for file in file_list:
            f.write(f"file '{file}'\n")
    
    cmd = f'ffmpeg -f concat -safe 0 -i file_list.txt -c copy "{output}"'
    print(cmd)
    subprocess.run(cmd, check=True)
    os.remove("file_list.txt")
    a_ctime = os.path.getctime(file_list[0])
    a_mtime = os.path.getmtime(file_list[0])
    # os.utime(dst, (a_ctime, a_mtime))
    modifyFileTime(output, Time(a_ctime), Time(a_mtime), Time(a_mtime), 0)

    
def process_directory(src_dir, format="265"):
    groups = {}
    for filename in os.listdir(src_dir):
        if filename.startswith("DJI_") and filename.lower().endswith('.mp4') and filename.lower().count("h26") > 0:
            parts = filename.split(".")[0].split("_")
            if len(parts) >= 2:
                key = parts[1]
                if key not in groups:
                    groups[key] = []
                groups[key].append(filename)
    
    for key in groups:
        groups[key].sort(key=lambda x: int(x.split(".")[0].split("_")[2]) if len(x.split("_")) > 2 else 0)
        file_list = []
        for filename in groups[key]:
            file_list.append(os.path.join(src_dir, filename))
        if len(file_list) > 1:
            output = os.path.join(src_dir, f'DJI_{key}_h{format}.mp4')
            concatenate_videos(file_list, output)
            print(f'Merged video saved as {output}')

def get_video_duration(file_path):
    """ 使用ffprobe获取视频文件时长 """
    cmd = [
        'ffprobe', '-v', 'error', '-show_entries', 'format=duration',
        '-of', 'default=noprint_wrappers=1:nokey=1', file_path
    ]
    try:
        result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        return float(result.stdout.strip())
    except Exception as e:
        print(f"Error getting duration for {file_path}: {e}")
        return 0

def get_total_duration(directory):
    """ 获取目录下所有视频文件的总时长 """
    total_duration = 0
    need_convert_duration = 0
    for root, dirs, files in os.walk(directory):
        for file in files:
            if file.lower().endswith(('.mp4', '.avi', '.mkv', '.mov', '.flv', '.wmv')):
                file_path = os.path.join(root, file)
                duration = get_video_duration(file_path)
                print(f"{file}: {duration} seconds")
                total_duration += duration
                if not "_h26" in file.lower():
                    need_convert_duration += duration
    return total_duration,need_convert_duration

def format_duration(duration):
    """ 将秒数格式化为时:分:秒 """
    hrs = int(duration // 3600)
    mins = int((duration % 3600) // 60)
    secs = int(duration % 60)
    return f"{hrs:02}:{mins:02}:{secs:02}"


def format_size(bytes_size):
    """ 将字节数格式化为可读单位 (B/KB/MB/GB/TB) """
    if bytes_size is None:
        return "未知"
    size = float(bytes_size)
    for unit in ['B', 'KB', 'MB', 'GB']:
        if size < 1024:
            return f"{size:.2f} {unit}"
        size /= 1024
    return f"{size:.2f} TB"


def format_bitrate(bps):
    """ 将比特率格式化为可读单位 (bps/kbps/Mbps) """
    if bps is None or bps <= 0:
        return "未知"
    if bps >= 1_000_000:
        return f"{bps / 1_000_000:.2f} Mbps"
    if bps >= 1_000:
        return f"{bps / 1_000:.1f} kbps"
    return f"{int(bps)} bps"


def cal_total_duration(directory):
    """ 计算目录下所有视频文件的总时长 """
    total_duration,need_convert_duration = get_total_duration(directory)
    formatted_duration = format_duration(total_duration)
    need_formatted_duration = format_duration(need_convert_duration)
    print(f"Total Duration: {formatted_duration} (hh:mm:ss)")
    print(f"Need Convert Duration: {need_formatted_duration} (hh:mm:ss)")

def extract_audio(src):
    duration = get_video_duration(src)
    formatted_duration = format_duration(duration)
    print(f"提取{src}音频,总长{formatted_duration}")
    filename = os.path.basename(src)
    dir_path = os.path.dirname(src)
    ext = os.path.splitext(filename)[1].lower()
    output = os.path.join(dir_path, filename.replace(ext, '.m4a').replace(ext, '.m4a'))
    cmd = f"ffmpeg -i {src} -vn -acodec aac {output}"
    print(cmd)
    os.system(cmd)
    print(f"音频提取完成=>{filename}.m4a")
    a_ctime = os.path.getctime(src)
    a_mtime = os.path.getmtime(src)
    modifyFileTime(output, Time(a_ctime), Time(a_mtime), Time(a_mtime), 0)
    print(f"同步文件创建时间完成")

def validate(filename):
    return filename.lower().endswith('.mp4') or filename.lower().endswith('.mov')

if __name__ == '__main__':
    src = sys.argv[1]
    if len(sys.argv) > 2:
        format = sys.argv[2]
    else:
        format = "265"

    third_arg = sys.argv[3] if len(sys.argv) > 3 else None
    force = None

    if third_arg == "-c":
        cal_total_duration(src)
        exit(0)
    if third_arg == "-a":
        extract_audio(src)
        exit(0)
    if third_arg in ('cpu', 'gpu'):
        force = third_arg
        print(f"[强制模式] 使用 {force.upper()} 编码（覆盖自动决策）")

    if os.path.isdir(src):
        cal_total_duration(src)
        for root, dirs, files in os.walk(src):
            for file in files:
                if file.lower().endswith(('.mp4', '.avi', '.mkv', '.mov', '.flv', '.wmv')):
                    if validate(file):
                        convert(os.path.join(root, file), format, force)
        # process_directory(root, format)
    elif validate(src):
        convert(src, format, force)
