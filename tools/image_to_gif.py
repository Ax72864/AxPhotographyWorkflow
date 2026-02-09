#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
import argparse
import glob
from typing import List, Tuple, Optional
from PIL import Image, ImageSequence
import re

def natural_sort_key(text: str) -> List:
    """
    自然排序的key函数，正确处理数字排序
    例如: image1.jpg, image2.jpg, image10.jpg 会按正确顺序排列
    """
    def convert(text):
        return int(text) if text.isdigit() else text.lower()
    
    return [convert(c) for c in re.split('([0-9]+)', text)]

def get_image_files(input_path: str) -> List[str]:
    """
    获取图片文件列表
    
    Args:
        input_path: 输入路径，可以是目录或文件路径模式
    
    Returns:
        排序后的图片文件路径列表
    """
    supported_formats = {'.jpg', '.jpeg', '.png', '.bmp', '.tiff', '.webp', '.gif'}
    image_files = []
    
    if os.path.isdir(input_path):
        # 如果是目录，获取目录中所有支持的图片文件
        for file in os.listdir(input_path):
            file_path = os.path.join(input_path, file)
            if os.path.isfile(file_path):
                _, ext = os.path.splitext(file.lower())
                if ext in supported_formats:
                    image_files.append(file_path)
    else:
        # 如果是文件模式（支持通配符）
        matching_files = glob.glob(input_path)
        for file_path in matching_files:
            if os.path.isfile(file_path):
                _, ext = os.path.splitext(file_path.lower())
                if ext in supported_formats:
                    image_files.append(file_path)
    
    if not image_files:
        raise ValueError(f"在 '{input_path}' 中未找到支持的图片文件")
    
    # 按文件名自然排序
    image_files.sort(key=lambda x: natural_sort_key(os.path.basename(x)))
    return image_files

def resize_image(image: Image.Image, target_size: Tuple[int, int], maintain_aspect: bool = True) -> Image.Image:
    """
    调整图片大小
    
    Args:
        image: 原始图片
        target_size: 目标尺寸 (width, height)
        maintain_aspect: 是否保持宽高比
    
    Returns:
        调整后的图片
    """
    if maintain_aspect:
        # 保持宽高比，使用thumbnail方法
        image_copy = image.copy()
        image_copy.thumbnail(target_size, Image.Resampling.LANCZOS)
        
        # 创建目标尺寸的画布，居中放置图片
        canvas = Image.new('RGBA', target_size, (255, 255, 255, 0))
        
        # 计算居中位置
        x = (target_size[0] - image_copy.width) // 2
        y = (target_size[1] - image_copy.height) // 2
        
        canvas.paste(image_copy, (x, y))
        return canvas
    else:
        # 直接拉伸到目标尺寸
        return image.resize(target_size, Image.Resampling.LANCZOS)

def create_gif(image_files: List[str], 
               output_path: str, 
               fps: float = 10.0, 
               resolution: Optional[Tuple[int, int]] = None,
               maintain_aspect: bool = True,
               optimize: bool = True,
               loop: int = 0) -> None:
    """
    创建GIF动图
    
    Args:
        image_files: 图片文件路径列表
        output_path: 输出GIF文件路径
        fps: 帧率
        resolution: 目标分辨率 (width, height)
        maintain_aspect: 是否保持宽高比
        optimize: 是否优化GIF文件大小
        loop: 循环次数，0表示无限循环
    """
    if not image_files:
        raise ValueError("图片文件列表为空")
    
    # 计算帧间隔（毫秒）
    duration = int(1000 / fps)
    
    print(f"正在处理 {len(image_files)} 张图片...")
    
    images = []
    
    for i, file_path in enumerate(image_files):
        try:
            print(f"处理图片 {i+1}/{len(image_files)}: {os.path.basename(file_path)}")
            
            with Image.open(file_path) as img:
                # 转换为RGBA模式以保持透明度支持
                if img.mode != 'RGBA':
                    img = img.convert('RGBA')
                
                # 调整分辨率
                if resolution:
                    img = resize_image(img, resolution, maintain_aspect)
                
                images.append(img.copy())
                
        except Exception as e:
            print(f"警告：无法处理图片 {file_path}: {e}")
            continue
    
    if not images:
        raise ValueError("没有成功加载任何图片")
    
    print(f"保存GIF到: {output_path}")
    print(f"参数: FPS={fps}, 分辨率={images[0].size}, 帧数={len(images)}")
    
    # 保存为GIF
    images[0].save(
        output_path,
        save_all=True,
        append_images=images[1:],
        duration=duration,
        loop=loop,
        optimize=optimize,
        format='GIF'
    )
    
    # 显示文件大小
    file_size = os.path.getsize(output_path) / (1024 * 1024)  # MB
    print(f"GIF创建完成！文件大小: {file_size:.2f} MB")

def parse_resolution(resolution_str: str) -> Tuple[int, int]:
    """
    解析分辨率字符串
    
    Args:
        resolution_str: 分辨率字符串，格式如 "800x600"
    
    Returns:
        (width, height) 元组
    """
    try:
        width, height = map(int, resolution_str.lower().split('x'))
        if width <= 0 or height <= 0:
            raise ValueError("分辨率必须是正数")
        return (width, height)
    except ValueError:
        raise ValueError(f"无效的分辨率格式: {resolution_str}，请使用格式如 '800x600'")

def main():
    parser = argparse.ArgumentParser(
        description='将图片序列转换为GIF动图',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python images_to_gif.py -i ./images/ -o animation.gif
  python images_to_gif.py -i "frame*.png" -o output.gif --fps 15 --resolution 800x600
  python images_to_gif.py -i image1.jpg image2.jpg image3.jpg -o result.gif --fps 5
        """
    )
    
    parser.add_argument(
        '-i', '--input', 
        required=True, 
        nargs='+',
        help='输入图片路径：可以是目录路径、通配符模式或多个图片文件路径'
    )
    
    parser.add_argument(
        '-o', '--output', 
        required=True, 
        help='输出GIF文件路径'
    )
    
    parser.add_argument(
        '--fps', 
        type=float, 
        default=10.0,
        help='帧率 (默认: 10.0)'
    )
    
    parser.add_argument(
        '--resolution', 
        type=str,
        help='目标分辨率，格式如 "800x600"'
    )
    
    parser.add_argument(
        '--no-aspect', 
        action='store_true',
        help='不保持宽高比，直接拉伸到目标分辨率'
    )
    
    parser.add_argument(
        '--no-optimize', 
        action='store_true',
        help='不优化GIF文件大小'
    )
    
    parser.add_argument(
        '--loop', 
        type=int, 
        default=0,
        help='循环次数，0表示无限循环 (默认: 0)'
    )
    
    args = parser.parse_args()
    
    try:
        # 验证FPS
        if args.fps <= 0:
            raise ValueError("FPS必须大于0")
        
        # 解析分辨率
        resolution = None
        if args.resolution:
            resolution = parse_resolution(args.resolution)
        
        # 获取所有图片文件
        all_image_files = []
        
        for input_path in args.input:
            if len(args.input) == 1:
                # 单个输入，可能是目录或通配符
                image_files = get_image_files(input_path)
            else:
                # 多个输入，视为单独的文件
                if os.path.isfile(input_path):
                    all_image_files.append(input_path)
                else:
                    print(f"警告: 文件不存在: {input_path}")
                continue
            
            all_image_files.extend(image_files)
        
        # 如果是多个单独文件，需要排序
        if len(args.input) > 1:
            all_image_files.sort(key=lambda x: natural_sort_key(os.path.basename(x)))
        
        if not all_image_files:
            raise ValueError("未找到有效的图片文件")
        
        print(f"找到 {len(all_image_files)} 张图片")
        
        # 创建输出目录（如果不存在）
        output_dir = os.path.dirname(args.output)
        if output_dir and not os.path.exists(output_dir):
            os.makedirs(output_dir)
        
        # 创建GIF
        create_gif(
            image_files=all_image_files,
            output_path=args.output,
            fps=args.fps,
            resolution=resolution,
            maintain_aspect=not args.no_aspect,
            optimize=not args.no_optimize,
            loop=args.loop
        )
        
    except Exception as e:
        print(f"错误: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()