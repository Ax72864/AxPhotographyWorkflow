import os
import shutil
import argparse
from pathlib import Path
import glob

def copy_matching_images(source_dir, reference_dir, target_dir, recursive=False, case_sensitive=True):
    """
    复制匹配的图片文件
    
    Args:
        source_dir: 源目录
        reference_dir: 参考目录  
        target_dir: 目标目录
        recursive: 是否递归搜索子目录
        case_sensitive: 是否区分大小写
    """
    
    # 验证目录
    source_path = Path(source_dir)
    reference_path = Path(reference_dir)
    target_path = Path(target_dir)
    
    if not source_path.exists():
        print(f"❌ 源目录不存在: {source_dir}")
        return False
    
    if not reference_path.exists():
        print(f"❌ 参考目录不存在: {reference_dir}")
        return False
    
    # 创建目标目录
    target_path.mkdir(parents=True, exist_ok=True)
    
    # 获取源目录中的jpg文件
    if recursive:
        source_pattern = source_path / "**" / "*.jpg"
        source_files = list(source_path.glob("**/*.jpg"))
        source_files.extend(source_path.glob("**/*.JPG"))
    else:
        source_files = list(source_path.glob("*.jpg"))
        source_files.extend(source_path.glob("*.JPG"))
    
    # 提取文件名（不含路径）
    source_filenames = [f.name for f in source_files]
    
    if not case_sensitive:
        source_filenames = [f.lower() for f in source_filenames]
    
    print(f"📁 源目录中找到 {len(source_filenames)} 个JPG文件")
    
    # 获取参考目录中的所有jpg文件
    if recursive:
        reference_files = list(reference_path.glob("**/*.jpg"))
        reference_files.extend(reference_path.glob("**/*.JPG"))
    else:
        reference_files = list(reference_path.glob("*.jpg"))
        reference_files.extend(reference_path.glob("**/*.JPG"))
    
    # 创建参考文件的映射 {filename: full_path}
    reference_map = {}
    for ref_file in reference_files:
        key = ref_file.name if case_sensitive else ref_file.name.lower()
        reference_map[key] = ref_file
    
    print(f"📁 参考目录中找到 {len(reference_map)} 个JPG文件")
    
    # 开始复制
    copied_count = 0
    not_found_files = []
    
    for source_filename in source_filenames:
        search_key = source_filename if case_sensitive else source_filename.lower()
        
        if search_key in reference_map:
            # 找到匹配文件
            source_ref_path = reference_map[search_key]
            target_file_path = target_path / source_filename
            
            try:
                shutil.copy2(source_ref_path, target_file_path)
                copied_count += 1
                print(f"✅ 已复制: {source_filename}")
            except Exception as e:
                print(f"❌ 复制失败: {source_filename} - {e}")
        else:
            not_found_files.append(source_filename)
            print(f"⚠️  未找到: {source_filename}")
    
    # 输出结果
    print(f"\n{'='*50}")
    print(f"🎉 复制完成!")
    print(f"✅ 成功复制: {copied_count} 个文件")
    print(f"⚠️  未找到: {len(not_found_files)} 个文件")
    print(f"📂 目标目录: {target_dir}")
    
    # 保存未找到文件列表
    if not_found_files:
        not_found_file = target_path / "not_found_files.txt"
        with open(not_found_file, 'w', encoding='utf-8') as f:
            f.write("未找到的文件列表:\n")
            for file in not_found_files:
                f.write(f"{file}\n")
        print(f"📝 未找到文件列表已保存到: {not_found_file}")
    
    return True

def main():
    parser = argparse.ArgumentParser(description='复制匹配的JPG文件')
    parser.add_argument('source_dir', help='源目录路径')
    parser.add_argument('reference_dir', help='参考目录路径')
    parser.add_argument('target_dir', help='目标目录路径')
    parser.add_argument('-r', '--recursive', action='store_true', 
                       help='递归搜索子目录')
    parser.add_argument('-i', '--ignore-case', action='store_true',
                       help='忽略文件名大小写')
    
    args = parser.parse_args()
    
    print("🚀 开始复制文件...")
    print(f"📂 源目录: {args.source_dir}")
    print(f"📂 参考目录: {args.reference_dir}")
    print(f"📂 目标目录: {args.target_dir}")
    print(f"🔄 递归搜索: {'是' if args.recursive else '否'}")
    print(f"🔤 忽略大小写: {'是' if args.ignore_case else '否'}")
    print("-" * 50)
    
    success = copy_matching_images(
        args.source_dir, 
        args.reference_dir, 
        args.target_dir,
        recursive=args.recursive,
        case_sensitive=not args.ignore_case
    )
    
    if success:
        print("✨ 任务完成!")
    else:
        print("💥 任务失败!")

if __name__ == "__main__":
    # 如果有命令行参数，使用命令行模式
    import sys
    if len(sys.argv) > 1:
        main()
    else:
        # 交互式模式
        print("=== JPG文件复制工具 ===")
        source_dir = input("请输入源目录路径: ").strip()
        reference_dir = input("请输入参考目录路径: ").strip()
        target_dir = input("请输入目标目录路径: ").strip()
        
        recursive = input("是否递归搜索子目录? (y/n): ").strip().lower() == 'y'
        ignore_case = input("是否忽略文件名大小写? (y/n): ").strip().lower() == 'y'
        
        copy_matching_images(source_dir, reference_dir, target_dir, 
                           recursive=recursive, case_sensitive=not ignore_case)