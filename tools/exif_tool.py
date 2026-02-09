import argparse
import os
import sys
import subprocess
import datetime
import glob

# ================= 配置区域 =================
EXIFTOOL_PATH = r"D:\work\exiftool\exiftool.exe"
# ===========================================

def get_files(path):
    """获取指定路径下的所有 jpg, png, heif 文件"""
    if not os.path.exists(path):
        print(f"错误: 路径 '{path}' 不存在。")
        sys.exit(1)
    
    files = []
    extensions = ['*.jpg', '*.jpeg', '*.png', '*.heif', '*.heic']
    
    if os.path.isfile(path):
        return [path]
        
    for ext in extensions:
        files.extend(glob.glob(os.path.join(path, ext), recursive=False))
        
    return list(set(files))

def run_exiftool(args):
    """运行 exiftool 命令"""
    # -m: 忽略次要错误 (关键)
    # -overwrite_original: 覆盖原文件
    cmd = [EXIFTOOL_PATH, '-overwrite_original', '-m'] + args
    
    try:
        result = subprocess.run(
            cmd, 
            capture_output=True, 
            text=True, 
            encoding='utf-8',
            errors='ignore'
        )
        return result
    except FileNotFoundError:
        print(f"错误: 找不到 exiftool，请检查路径: {EXIFTOOL_PATH}")
        sys.exit(1)

def get_best_time(file_path, sync_mode):
    """
    智能获取基准时间。
    因为我们要重写文件，必须先在内存里把时间定好。
    """
    # 1. 获取文件系统时间
    stat = os.stat(file_path)
    ctime_sys = datetime.datetime.fromtimestamp(stat.st_ctime)
    mtime_sys = datetime.datetime.fromtimestamp(stat.st_mtime)
    
    # 2. 尝试读取 Exif 时间 (即使文件有错误，-m 参数通常也能读出时间)
    cmd = ['-DateTimeOriginal', '-d', '%Y:%m:%d %H:%M:%S', file_path]
    result = run_exiftool(cmd)
    
    stime_exif = None
    if result.stdout:
        parts = result.stdout.split(':', 1)
        if len(parts) > 1:
            try:
                time_str = parts[1].strip()
                stime_exif = datetime.datetime.strptime(time_str, '%Y:%m:%d %H:%M:%S')
            except ValueError:
                pass

    # 3. 根据模式决定使用哪个时间
    target_time = None
    
    if sync_mode == 'm':
        target_time = mtime_sys
    elif sync_mode == 'c':
        target_time = ctime_sys
    elif sync_mode == 's':
        if stime_exif:
            target_time = stime_exif
        else:
            print(f"  [警告] 无法读取EXIF拍摄时间，将回退使用【修改时间】作为基准。")
            target_time = mtime_sys
    else:
        # 如果未指定模式，优先保留原始 Exif 时间，没有则用修改时间
        target_time = stime_exif if stime_exif else mtime_sys
        
    return target_time

def smart_repair_and_modify(file_path, target_time, args):
    """
    核心函数：修复结构 + 保留旧数据 + 写入新数据
    """
    time_str = target_time.strftime('%Y:%m:%d %H:%M:%S')
    
    # 构建复杂的 ExifTool 命令
    cmd_args = []
    
    # -----------------------------------------------------
    # 第一部分：修复与迁移 (Magic Fix)
    # -----------------------------------------------------
    # 1. -all= : 清除现有的元数据结构（解决 Bad Offset）
    # 2. -tagsfromfile @ : 准备从当前文件复制标签
    # 3. -all:all : 复制所有能找到的标签 (ISO, 光圈, 快门...)
    # 4. -unsafe : 甚至复制不安全的标签 (为了最大程度保留)
    # 5. --MakerNotes : (注意是双横杠) 在复制过程中，唯独不要复制 MakerNotes
    cmd_args.extend(['-all=', '-tagsfromfile', '@', '-all:all', '-unsafe', '--MakerNotes'])
    
    # -----------------------------------------------------
    # 第二部分：写入新数据 (覆盖旧数据或新增)
    # -----------------------------------------------------
    # 写入时间 (更新所有时间字段)
    cmd_args.append(f'-AllDates={time_str}')
    
    if args.cameramode:
        cmd_args.append(f'-Model={args.cameramode}')
    
    if args.author:
        cmd_args.append(f'-Artist={args.author}')
        cmd_args.append(f'-XPAuthor={args.author}')
        cmd_args.append(f'-Copyright={args.author}')
    
    if args.lensmode:
        cmd_args.append(f'-LensModel={args.lensmode}')
        cmd_args.append(f'-LensInfo={args.lensmode}')
        cmd_args.append(f'-Lens={args.lensmode}')
        
    cmd_args.append(file_path)
    
    # 执行命令
    result = run_exiftool(cmd_args)
    
    if result.returncode == 0:
        print(f"  Exif 修复与修改成功。")
    else:
        # 即使 returncode 不为 0，只要 stderr 没有严重错误，可能也成功了
        # 我们检查一下 stderr
        errors = [line for line in result.stderr.splitlines() if "Warning" not in line and "image files updated" not in line and line.strip()]
        if not errors:
             print(f"  Exif 修复与修改成功 (存在警告但已忽略)。")
        else:
             print(f"  Exif 修改失败: {errors}")
             return # 如果 Exif 改失败了，就不改系统时间了

    # -----------------------------------------------------
    # 第三部分：同步文件系统时间 (Windows 属性)
    # -----------------------------------------------------
    if args.sync:
        sys_time_cmd = [
            f'-FileCreateDate={time_str}',
            f'-FileModifyDate={time_str}',
            file_path
        ]
        run_exiftool(sys_time_cmd)
        print(f"  时间已同步为: {time_str}")

def main():
    parser = argparse.ArgumentParser(description="批量修改照片Exif信息 (保留原始数据+修复损坏)")
    
    parser.add_argument('-p', '--path', required=True, help="路径")
    parser.add_argument('--sync', choices=['m', 'c', 's'], help="同步模式: m(修改时间), c(创建时间), s(拍摄时间)")
    parser.add_argument('--cameramode', help="相机型号")
    parser.add_argument('--author', help="作者")
    parser.add_argument('--lensmode', help="镜头型号")

    args = parser.parse_args()

    files = get_files(args.path)
    if not files:
        print("未找到文件。")
        return
    
    print(f"找到 {len(files)} 个文件，开始处理...")

    for i, f in enumerate(files):
        filename = os.path.basename(f)
        print(f"[{i+1}/{len(files)}] 正在处理: {filename}")
        
        # 1. 确定时间基准
        best_time = get_best_time(f, args.sync)
        
        # 2. 执行修复并修改
        smart_repair_and_modify(f, best_time, args)

    print("所有操作完成。")

if __name__ == "__main__":
    main()