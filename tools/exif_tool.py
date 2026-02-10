import argparse
import os
import sys
import subprocess
import datetime
import glob

# ================= 配置区域 =================
# 请确保此处路径正确
EXIFTOOL_PATH = r"D:\work\exiftool\exiftool.exe"
# ===========================================

def get_files(path):
    """获取指定路径下的所有 jpg, png, heif 文件"""
    if not os.path.exists(path):
        print(f"错误: 路径 '{path}' 不存在。")
        sys.exit(1)
    
    files = []
    extensions = ['*.jpg', '*.jpeg', '*.png', '*.heif', '*.heic', '*.tif', '*.tiff']
    exts = ['jpg', 'jpeg', 'png', 'heif', 'heic', 'tif', 'tiff']
    
    if os.path.isfile(path):
        return [path]
    

    for root, dirs, _files in os.walk(path):
        for f in _files:
            print(f,f.split(".")[1].lower())
            if f.split(".")[1].lower() in exts:
                files.append(os.path.join(root, f))

    # for ext in extensions:
    #     files.extend(glob.glob(os.path.join(path, ext), recursive=False))
        
    return list(set(files))

def run_exiftool(args):
    """运行 exiftool 命令"""
    # -m: 忽略次要错误
    # -overwrite_original: 覆盖原文件
    cmd = [EXIFTOOL_PATH, '-overwrite_original', '-m'] + args
    
    try:
        # 在 Windows 上隐藏控制台窗口 (可选)
        startupinfo = None
        if os.name == 'nt':
            startupinfo = subprocess.STARTUPINFO()
            startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW

        result = subprocess.run(
            cmd, 
            capture_output=True, 
            text=True, 
            encoding='utf-8',
            errors='ignore',
            startupinfo=startupinfo
        )
        return result
    except FileNotFoundError:
        print(f"错误: 找不到 exiftool，请检查路径: {EXIFTOOL_PATH}")
        sys.exit(1)

def get_best_time(file_path, sync_mode):
    """智能获取基准时间"""
    stat = os.stat(file_path)
    ctime_sys = datetime.datetime.fromtimestamp(stat.st_ctime)
    mtime_sys = datetime.datetime.fromtimestamp(stat.st_mtime)
    
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
        target_time = stime_exif if stime_exif else mtime_sys
        
    return target_time

def smart_repair_and_modify(file_path, target_time, args):
    """核心函数：修复结构 + 保留旧数据 + 写入新数据"""
    time_str = target_time.strftime('%Y:%m:%d %H:%M:%S')
    
    # -----------------------------------------------------
    # 1. 基础修复与迁移 (Magic Fix)
    # -----------------------------------------------------
    cmd_args = ['-all=', '-tagsfromfile', '@', '-all:all', '-unsafe', '--MakerNotes']
    
    # -----------------------------------------------------
    # 2. 写入新数据
    # -----------------------------------------------------
    
    # 时间
    cmd_args.append(f'-AllDates={time_str}')
    
    # 相机型号
    if args.cameramode:
        cmd_args.append(f'-Model={args.cameramode}')
    
    # 作者/版权
    if args.author:
        cmd_args.append(f'-Artist={args.author}')
        cmd_args.append(f'-XPAuthor={args.author}')
        cmd_args.append(f'-Copyright={args.author}')
    
    # 镜头型号
    if args.lensmode:
        cmd_args.append(f'-LensModel={args.lensmode}')
        cmd_args.append(f'-LensInfo={args.lensmode}')
        cmd_args.append(f'-Lens={args.lensmode}')

    # --- 新增功能区域 ---

    # 光圈 (F-Number)
    if args.aperture:
        cmd_args.append(f'-FNumber={args.aperture}')
    
    # ISO
    if args.iso:
        cmd_args.append(f'-ISO={args.iso}')

    # 35mm 等效焦距
    if args.focal35:
        cmd_args.append(f'-FocalLengthIn35mmFormat={args.focal35}')

    # 标签 (Keywords)
    if args.tags:
        # 分割输入的标签，例如 "风景|人像" -> ['风景', '人像']
        tag_list = [t.strip() for t in args.tags.split('|') if t.strip()]
        
        if tag_list:
            # 清除可能从原文件复制过来的旧标签，确保只写入新标签（如果不想覆盖旧标签，注释掉下面这行）
            # 注意：ExifTool 命令是从左到右执行的，先复制(-tagsfromfile)，再执行这里的覆盖
            cmd_args.append('-Keywords=')
            cmd_args.append('-Subject=')

            # 针对列表型标签 (IPTC Keywords, XMP Subject)
            # 在 ExifTool 中，重复使用 -Tag=Value 会创建列表
            for tag in tag_list:
                cmd_args.append(f'-Keywords={tag}')
                cmd_args.append(f'-Subject={tag}')
            
            # 针对 Windows 专用标签 (XPKeywords)，它通常是分号分隔的字符串
            xp_keywords_str = ";".join(tag_list)
            cmd_args.append(f'-XPKeywords={xp_keywords_str}')

    # -----------------------------------------------------
    
    cmd_args.append(file_path)
    
    # 执行命令
    result = run_exiftool(cmd_args)
    
    if result.returncode == 0:
        print(f"  Exif 修复与修改成功。")
    else:
        errors = [line for line in result.stderr.splitlines() if "Warning" not in line and "image files updated" not in line and line.strip()]
        if not errors:
             print(f"  Exif 修复与修改成功 (存在警告但已忽略)。")
        else:
             print(f"  Exif 修改失败: {errors}")
             return 

    # -----------------------------------------------------
    # 3. 同步文件系统时间
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
    
    parser.add_argument('-p', '--path', required=True, help="目标文件或目录路径")
    parser.add_argument('--sync', choices=['m', 'c', 's'], help="同步模式: m(修改时间), c(创建时间), s(拍摄时间)")
    
    # 原始参数
    parser.add_argument('--cameramode', help="相机型号 (Model)")
    parser.add_argument('--author', help="作者 (Artist/Copyright)")
    parser.add_argument('--lensmode', help="镜头型号 (LensModel)")
    
    # 新增参数
    parser.add_argument('--aperture', help="光圈值 (例如: 1.8, 2.8, 5.6)")
    parser.add_argument('--iso', help="ISO 感光度 (例如: 100, 400, 800)")
    parser.add_argument('--focal35', help="35mm等效焦距 (例如: 24, 35, 50)")
    parser.add_argument('--tags', help="标签/关键字，多个标签用|分隔 (例如: '风景|旅行|2023')")

    args = parser.parse_args()

    files = get_files(args.path)
    print(files)
    if not files:
        print("未找到支持的文件。")
        return
    
    print(f"找到 {len(files)} 个文件，开始处理...")

    for i, f in enumerate(files):
        filename = os.path.basename(f)
        print(f"[{i+1}/{len(files)}] 正在处理: {filename}")
        
        best_time = get_best_time(f, args.sync)
        smart_repair_and_modify(f, best_time, args)

    print("所有操作完成。")

if __name__ == "__main__":
    main()