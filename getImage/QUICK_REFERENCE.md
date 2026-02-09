# 快速参考指南 (Quick Reference)

## 一键开始 (Quick Start)

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 最简单的方式：使用Wiki Commons（无需配置）
python download_rated_images.py --count 10 --sources wiki

# 3. 推荐方式：下载专家评分数据集
python setup_koniq_dataset.py
python download_rated_images.py --count 20 --sources koniq,wiki
```

## 常用命令

### 基础下载
```bash
# 下载20张图片（自动分布）
python download_rated_images.py --count 20

# 下载50张图片
python download_rated_images.py --count 50
```

### 自定义分布
```bash
# 指定高中低质量图片数量
python download_rated_images.py --count 50 --high 15 --medium 20 --low 15

# 只下载高质量图片
python download_rated_images.py --count 30 --high 30 --medium 0 --low 0
```

### 选择图源
```bash
# 仅使用KonIQ（专家评分，最可靠）
python download_rated_images.py --count 20 --sources koniq

# 仅使用Wiki Commons（获奖作品，无需配置）
python download_rated_images.py --count 20 --sources wiki

# 使用Flickr和KonIQ
python download_rated_images.py --count 50 --sources flickr,koniq
```

### 其他选项
```bash
# 详细日志输出
python download_rated_images.py --count 10 --verbose

# 指定输出目录
python download_rated_images.py --count 10 --output ./my_images

# 使用自定义配置文件
python download_rated_images.py --count 10 --config my_config.ini
```

## 图源配置

### Flickr (可选，但推荐)
1. 访问: https://www.flickr.com/services/apps/create/
2. 创建应用获取API Key和Secret
3. 编辑 `config.ini`：
   ```ini
   [flickr]
   api_key = YOUR_KEY_HERE
   api_secret = YOUR_SECRET_HERE
   ```

### KonIQ-10k (推荐但可选)
```bash
# 尝试自动下载（约2GB）
python setup_koniq_dataset.py

# 如果失败，可以手动下载
# 访问: http://database.mmsp-kn.de/koniq-10k-database.html
# 或: https://github.com/subpic/koniq-PyTorch
# 解压到: ./datasets/koniq10k/
```

**注意**: 即使没有KonIQ，工具仍可使用Flickr和Wiki正常工作！

### Wiki Commons (无需配置)
直接可用，无需任何设置。

## 评分说明

| 分数范围 | 质量描述 | 典型来源 |
|---------|---------|---------|
| 9-10 | 杰出作品 | Wiki获奖、KonIQ高分 |
| 7-9 | 优秀作品 | KonIQ中上、Flickr top 10% |
| 5-7 | 中等作品 | 各图源中等水平 |
| 3-5 | 较差作品 | KonIQ中下、Flickr底部 |
| 0-3 | 低质作品 | KonIQ低分、技术问题图片 |

## 文件命名

下载的图片格式：`作品名-作者-评分.jpg`

示例：
- `Mountain Landscape-John Doe-8.5.jpg`
- `Portrait Study-Unknown-9.2.jpg`
- `Blurry Photo-Test User-2.1.jpg`

## 输出位置

默认输出目录：`../rated/`  
去重数据库：`../downloaded.json`

可在 `config.ini` 中修改：
```ini
[download]
output_dir = ../rated
```

## 故障排除

### 问题：没有图源可用
```
解决方案：
1. 最简单：python download_rated_images.py --sources wiki
2. 推荐：python setup_koniq_dataset.py
3. 扩充：配置Flickr API
```

### 问题：Flickr API错误
```
检查：
1. API密钥是否正确配置在config.ini
2. 是否超过3600次/小时配额
3. 尝试使用其他图源：--sources koniq,wiki
```

### 问题：KonIQ数据集不可用
```
解决：
1. 运行：python setup_koniq_dataset.py
2. 或手动下载：http://database.mmsp-kn.de/koniq-10k-database.html
3. 解压到：./datasets/koniq10k/
```

### 问题：下载速度慢
```
优化：
1. 仅使用本地KonIQ：--sources koniq
2. 减少数量：--count 10
3. 检查网络连接
```

## 目录结构

```
PhotoMaster/
├── getImage/                    # 工具目录
│   ├── download_rated_images.py # 主程序
│   ├── setup_koniq_dataset.py   # 数据集设置
│   ├── config.ini               # 配置文件
│   ├── requirements.txt         # 依赖
│   ├── README.md                # 详细文档
│   └── datasets/                # 数据集（自动创建）
│       └── koniq10k/
│           ├── images/
│           └── *.csv
├── rated/                       # 下载的图片（自动创建）
│   ├── image1-author-8.5.jpg
│   └── ...
└── downloaded.json              # 去重数据库（自动创建）
```

## 检查工具状态

```bash
# 查看帮助
python download_rated_images.py --help

# 查看示例用法
python example_usage.py

# 检查KonIQ数据集
ls datasets/koniq10k/images/
```

## 使用场景

### 场景1: 快速测试（5分钟）
```bash
pip install -r requirements.txt
python download_rated_images.py --count 10 --sources wiki
```

### 场景2: 专业评估（推荐）
```bash
pip install -r requirements.txt
python setup_koniq_dataset.py  # 需要约10-20分钟
python download_rated_images.py --count 100 --sources koniq,wiki
```

### 场景3: 大规模数据集
```bash
# 1. 配置Flickr API
# 2. 设置KonIQ数据集
python setup_koniq_dataset.py

# 3. 下载500张图片
python download_rated_images.py --count 500
```

## 性能提示

- **最快**: 使用本地KonIQ（`--sources koniq`），无网络延迟
- **最可靠**: KonIQ有专家评分，质量最高
- **最大量**: Flickr样本量最大，但需要API密钥
- **最简单**: Wiki Commons无需配置，直接可用

## 技术支持

- 详细文档: `README.md`
- 实施总结: `IMPLEMENTATION_SUMMARY.md`
- 示例代码: `example_usage.py`

## 相关链接

- Flickr API: https://www.flickr.com/services/api/
- KonIQ-10k: http://database.mmsp-kn.de/koniq-10k-database.html
- Wiki Commons: https://commons.wikimedia.org/
