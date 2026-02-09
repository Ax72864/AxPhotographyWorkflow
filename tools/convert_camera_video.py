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


def convert(src,format = "265"):
    if src.lower().endswith(f'_h{format}.mp4'):
        print(f'{src} is converted video, skip')
        return
    duration = get_video_duration(src)
    print(f"=====开始转换{src},总长{format_duration(duration)}=====")
    dir_path = os.path.dirname(src)
    ext = os.path.splitext(src)[1]
    print(ext)
    dst = os.path.join(dir_path, os.path.basename(src).replace(ext, f'_h{format}.mp4'))
    print(dst)
    if os.path.exists(dst):
        print(f'{dst} has been converted, skip')
        return
    
    video_info = get_video_info(src)
    bit_rate = video_info['bit_rate']
    pix_fmt = video_info['pix_fmt']
    print("video info => ",video_info['codec_name'], pix_fmt, bit_rate)
    
    if format == "265":
        if video_info['codec_name'] == 'hevc' and 'yuv420p' in pix_fmt:
            option = "-tag:v hvc1 -pix_fmt yuv420p10le"
        else:
            option = "-tag:v hvc1 -pix_fmt yuv422p10le"

        if bit_rate < 200000:
            maxrate = f'-maxrate {bit_rate} -bufsize {bit_rate // 2}'
        else:
            maxrate = '-maxrate 20M -bufsize 10M'

        cmd = f'ffmpeg -i "{src}" -vcodec libx265 {option} -crf 20 {maxrate} -preset fast -acodec aac -ab 192k -threads 10 -map_metadata 0 "{dst}"'
    # if format == "265":
    #     option = "-tag:v hvc1 -pix_fmt yuv422p10le"
    #     # cmd = f'ffmpeg -hwaccel qsv -i "{src}" -vcodec libx{format} {option} -b:v 200k -crf 20 -maxrate 20M -bufsize 16M -preset fast -acodec aac -ab 192k -threads 10 "{dst}"'
    #     cmd = f'ffmpeg -i "{src}" -vcodec libx265 {option} -crf 20 -maxrate 20M -bufsize 16M -preset fast -acodec aac -ab 192k -threads 10 "{dst}"'
    elif format == "264":
        option = "-pix_fmt yuv420p"
        cmd = f'ffmpeg -hwaccel cuda -i "{src}" -vcodec h264_nvenc {option} -b:v 200k -maxrate 20M -bufsize 16M -preset fast -acodec aac -ab 192k -threads 10 "{dst}"'
    print(cmd)
    os.system(cmd)
    
    a_ctime = os.path.getctime(src)
    a_mtime = os.path.getmtime(src)
    # os.utime(dst, (a_ctime, a_mtime))
    modifyFileTime(dst, Time(a_ctime), Time(a_mtime), Time(a_mtime), 0)
    print(f'{src} converted to {dst}')

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

    if len(sys.argv) > 3 and sys.argv[3] == "-c":
        cal_total_duration(src)
        exit(0)
    if len(sys.argv) > 3 and sys.argv[3] == "-a":
        extract_audio(src)
        exit(0)
        
    if os.path.isdir(src):
        cal_total_duration(src)
        for root, dirs, files in os.walk(src):
            for file in files:
                if file.lower().endswith(('.mp4', '.avi', '.mkv', '.mov', '.flv', '.wmv')):
                    if validate(file):
                        convert(os.path.join(root, file),format)
        # process_directory(root, format)
    elif validate(src):
        convert(src,format)
