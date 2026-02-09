import csv
import json
import sys
from collections import defaultdict

def build_tree():
    """创建一个无限嵌套的字典"""
    return defaultdict(build_tree)

def process_csv_to_json(csv_file, json_file):
    # 读取 CSV 文件
    with open(csv_file, 'r', encoding='utf-8') as f:
        reader = csv.reader(f)
        headers = next(reader)  # 读取表头
        
        # 创建数据结构
        tree = build_tree()
        species_map = {}
        
        # 记录上一个物种的信息，用于处理亚种
        last_species = None
        last_cn_name = None
        
        # 处理每一行数据
        for row in reader:
            # 将行数据与表头对应
            data = dict(zip(headers, row))
            # print(data)
            # 只处理鸟纲的物种
            if data['纲中文名'] != '鸟纲':
                continue
                
            # 处理物种中文名（如果为空，使用上一个记录的中文名）
            if not data['物种中文名'].strip():
                species_data = last_species
                species_data['subspecies'].append(data['\ufeff物种拉丁名'])
            else:            
                species_data = {
                    "cn_name": data['物种中文名'],
                    "latin_name": data['\ufeff物种拉丁名'],
                    "subspecies": [data['\ufeff物种拉丁名']],
                    "class":f"{data['目中文名']} {data['科中文名']} {data['属中文名']}",
                }
                # 构建层级结构
                tree[data['目中文名']][data['科中文名']][data['属中文名']][data['物种中文名']] = species_data
                last_species = species_data

            species_map[data['\ufeff物种拉丁名']] = species_data
    
    # 将默认字典转换为普通字典
    def convert_tree(t):
        if isinstance(t, defaultdict):
            return {k: convert_tree(v) for k, v in t.items()}
        return t
    
    tree = convert_tree(tree)
    
    # 写入 JSON 文件
    output = {
        "Tree": tree,
        "Map": species_map
    }
    
    with open(json_file, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)

# 使用示例
if __name__ == "__main__":
    input_csv = sys.argv[1]  # 输入的 CSV 文件名
    output_json = sys.argv[2]  # 输出的 JSON 文件名
    process_csv_to_json(input_csv, output_json)