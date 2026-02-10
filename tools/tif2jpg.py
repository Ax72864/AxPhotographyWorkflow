import os
import argparse
from PIL import Image

def convert_tif_to_jpg(directory, quality=92):
    """
    将指定目录下的所有 tif/tiff 文件转换为 jpg。
    """
    # 检查目录是否存在
    if not os.path.exists(directory):
        print(f"错误: 目录 '{directory}' 不存在。")
        return

    # 支持的 TIF 扩展名
    tif_extensions = ('.tif', '.tiff', '.TIF', '.TIFF')
    
    # 获取目录下所有文件
    files = [f for f in os.listdir(directory) if f.endswith(tif_extensions)]
    
    if not files:
        print(f"在 '{directory}' 中没有找到 TIF 文件。")
        return

    print(f"找到 {len(files)} 个文件，准备开始转换 (质量: {quality})...")

    success_count = 0
    
    for filename in files:
        # 构建完整路径
        file_path = os.path.join(directory, filename)
        # 构建输出文件名 (替换后缀)
        file_name_without_ext = os.path.splitext(filename)[0]
        output_path = os.path.join(directory, file_name_without_ext + ".jpg")

        try:
            with Image.open(file_path) as img:
                # 转换模式：如果原图是 RGBA (透明通道) 或 P (调色板)，转为 RGB，否则 JPG 不支持
                if img.mode in ('RGBA', 'P'):
                    img = img.convert('RGB')
                
                # 保存为 JPG
                img.save(output_path, 'JPEG', quality=quality)
                print(f"[成功] {filename} -> {os.path.basename(output_path)}")
                success_count += 1
        except Exception as e:
            print(f"[失败] {filename}: {e}")

    print("-" * 30)
    print(f"处理完成。成功转换: {success_count}/{len(files)}")

if __name__ == "__main__":
    # 设置参数解析
    parser = argparse.ArgumentParser(description="将目录中的所有 TIF 文件转换为 JPG。")
    
    # 位置参数：目录路径
    parser.add_argument("directory", help="包含 TIF 文件的目录路径")
    
    # 可选参数：质量 (默认 92)
    parser.add_argument("-q", "--quality", type=int, default=92, 
                        help="JPG 输出质量 (1-95)，默认为 92")

    args = parser.parse_args()

    # 执行转换
    convert_tif_to_jpg(args.directory, args.quality)