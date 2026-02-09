# Multi-Source Rated Images Downloader

一个基于多图源的带评分图片下载工具，用于评估LLM图片评分系统的效果。该工具从Flickr、KonIQ-10k数据集和Wiki Commons获取图片，并提供统一的0-10分评分标准。

## 核心特性

✅ **多图源支持**: 集成Flickr API、KonIQ-10k专家标注数据集、Wiki Loves Monuments获奖作品  
✅ **评分一致性**: 统一的0-10分归一化策略，确保不同来源评分可比  
✅ **专家标准**: KonIQ-10k提供10,000+真实人类质量评判作为基准  
✅ **全范围覆盖**: 从杰出作品(9-10分)到低质图片(0-3分)的完整分布  
✅ **智能去重**: 基于URL和图片哈希的双重去重机制  
✅ **自动切换**: API限额或失败时自动切换到其他图源  
✅ **可控分布**: 支持指定高/中/低质量图片的数量分布

## 快速开始

### 1. 安装依赖

```bash
cd getImage
pip install -r requirements.txt
```

### 2. 配置图源

#### 选项A：使用Flickr（推荐，样本量大）

1. 访问 [Flickr App Garden](https://www.flickr.com/services/apps/create/)
2. 创建应用获取API Key和Secret
3. 编辑 `config.ini`，填入API凭据：

```ini
[flickr]
api_key = YOUR_FLICKR_API_KEY
api_secret = YOUR_FLICKR_SECRET
```

**免费配额**: 3600次请求/小时

#### 选项B：使用KonIQ-10k数据集（推荐，专家评分）

运行设置脚本自动下载数据集（约2GB）：

```bash
python setup_koniq_dataset.py
```

脚本将自动：
1. 下载图片压缩包（~2GB）
2. 下载评分CSV文件
3. 解压到正确的目录
4. 清理临时文件

**官方下载地址**（脚本已更新使用这些链接）:
- 图片: http://datasets.vqa.mmsp-kn.de/archives/koniq10k_1024x768.zip
- 评分: http://datasets.vqa.mmsp-kn.de/archives/koniq10k_scores_and_distributions.zip

**如果自动下载失败**，可以手动下载上述文件，解压到：
- 图片 → `./datasets/koniq10k/images/`
- CSV → `./datasets/koniq10k/koniq10k_scores_and_distributions.csv`

**许可**: 学术研究可用

**重要**: 即使没有KonIQ数据集，工具仍可使用Flickr和Wiki Commons图源正常工作！

#### 选项C：使用Wiki Commons（无需配置）

Wiki Loves Monuments和精选图片，无需API密钥，自动可用。

### 3. 下载图片

```bash
# 下载20张图片（自动分布）
python download_rated_images.py --count 20

# 下载50张图片，指定分布
python download_rated_images.py --count 50 --high 15 --medium 20 --low 15

# 仅使用特定图源
python download_rated_images.py --count 30 --sources flickr,koniq

# 使用自定义配置文件
python download_rated_images.py --count 10 --config my_config.ini
```

下载的图片将保存到 `../rated/` 目录（可在config.ini中修改）。

## 评分机制详解

### 评分一致性策略

本工具采用**统一标准法**，将不同图源的评分映射到统一的0-10分制：

| 评分区间 | 质量描述 | 图源示例 |
|---------|---------|---------|
| 9-10分 | 杰出作品 | Wiki获奖作品、KonIQ高分、Flickr top 1% |
| 7-9分 | 优秀作品 | KonIQ中上、Flickr top 10% |
| 5-7分 | 中等作品 | KonIQ中等、Flickr中位数附近 |
| 3-5分 | 较差作品 | KonIQ中下、Flickr bottom 30% |
| 0-3分 | 低质作品 | KonIQ低分、Flickr底部 |

### 各图源评分方法

#### 1. Flickr多指标评分

Flickr使用浏览量、收藏数、评论数的加权组合：

```python
# 对数变换处理数量级差异
v = log10(views + 1)
f = log10(favorites * 10 + 1)
c = log10(comments * 20 + 1)

# 加权组合（可调节）
raw_score = 0.3*v + 0.5*f + 0.2*c

# 百分位归一化到0-10
score = percentile(raw_score) * 10
```

**优点**: 多维度评价，样本量大  
**注意**: 反映流行度而非纯艺术质量，需要与专家评分交叉验证

#### 2. KonIQ-10k专家评分

直接使用数据集的MOS (Mean Opinion Score)：

```python
# KonIQ的MOS范围通常是1-5
normalized_score = (MOS - 1) / (5 - 1) * 10
```

**优点**: 真实的人类质量评判，最可靠的标准  
**数据集**: 10,073张图片，涵盖从差到优秀的全范围

#### 3. Wiki Commons获奖评分

基于摄影比赛获奖等级：

```python
award_scores = {
    '1st': 10.0,      # 一等奖
    '2nd': 9.5,       # 二等奖
    '3rd': 9.0,       # 三等奖
    'finalist': 8.5,  # 入围
    'featured': 8.0   # 精选
}
```

**优点**: 专业评审，艺术性和技术性都有保证  
**限制**: 主要提供高质量样本

## 文件命名规则

下载的图片按以下格式命名：

```
作品名-作者-评分.jpg
```

示例：
- `Mountain Landscape-John Doe-8.5.jpg` (Flickr高分)
- `Urban Street-Jane Smith-6.2.jpg` (Flickr中等)
- `Portrait Study-Unknown-9.2.jpg` (KonIQ高分)
- `Monument Winner-Photographer-10.0.jpg` (Wiki获奖)

特殊字符会被自动清理，文件名过长会被截断。

## 去重机制

工具使用双重去重机制：

1. **URL去重**: 记录已下载的图片URL
2. **感知哈希去重**: 计算图片的感知哈希，避免内容相同但URL不同的重复

去重数据库保存在 `downloaded.json`。

## 命令行参数

```bash
python download_rated_images.py [选项]

选项:
  --count N          下载图片总数（默认: 20）
  --high N           高质量图片数量（7-10分）
  --medium N         中等质量图片数量（4-7分）
  --low N            低质量图片数量（0-4分）
  --sources LIST     指定图源，逗号分隔: flickr,koniq,wiki
  --config FILE      配置文件路径（默认: config.ini）
  --output DIR       输出目录（覆盖配置文件设置）
  --verbose          启用详细日志
  -h, --help         显示帮助信息
```

## 配置文件说明

`config.ini` 包含所有图源和下载的配置：

```ini
[flickr]
api_key = YOUR_FLICKR_API_KEY        # Flickr API密钥
api_secret = YOUR_FLICKR_SECRET       # Flickr API密钥

[koniq]
dataset_path = ./datasets/koniq10k   # KonIQ数据集路径
metadata_file = koniq10k_scores_and_distributions.csv

[wiki_commons]
# Wiki Commons无需配置

[download]
output_dir = ../rated                 # 输出目录
max_retries = 3                       # 最大重试次数
batch_size = 10                       # 批量获取大小
```

## 项目结构

```
getImage/
├── download_rated_images.py      # 主程序
├── setup_koniq_dataset.py        # KonIQ数据集设置脚本
├── config.ini                    # 配置文件
├── requirements.txt              # Python依赖
├── README.md                     # 本文档
└── datasets/                     # 数据集目录（自动创建）
    └── koniq10k/
        ├── images/               # KonIQ图片
        └── koniq10k_scores_and_distributions.csv

../rated/                         # 下载的图片输出目录
../downloaded.json                # 去重数据库
```

## 使用场景示例

### 场景1: 评估LLM评分系统

```bash
# 下载100张各种质量的图片用于测试
python download_rated_images.py --count 100

# LLM对这些图片评分后，与文件名中的评分对比
# 计算相关系数、MSE等指标评估LLM表现
```

### 场景2: 训练图片质量评估模型

```bash
# 下载500张图片，均匀分布
python download_rated_images.py --count 500 \
  --high 150 --medium 200 --low 150

# 使用这些带标签的图片训练模型
```

### 场景3: 仅使用高质量专家评分数据

```bash
# 仅使用KonIQ和Wiki Commons（更可靠的评分）
python download_rated_images.py --count 50 \
  --sources koniq,wiki
```

## 常见问题

### Q: Flickr API返回403或429错误？

A: 可能是API配额用完或密钥无效。请检查：
- API密钥是否正确填写在config.ini
- 是否超过3600次/小时的免费配额
- 尝试切换到其他图源：`--sources koniq,wiki`

### Q: KonIQ数据集下载失败？

A: 如果官方源不可用，可以：
1. 访问 http://database.mmsp-kn.de/koniq-10k-database.html 手动下载
2. 解压到 `./datasets/koniq10k/`
3. 确保目录结构正确

### Q: 如何调整评分权重？

A: 编辑 `download_rated_images.py` 中的评分函数：

```python
# FlickrSource._calculate_flickr_score() 中
raw_score = 0.3*v + 0.5*f + 0.2*c  # 调整这些权重
```

### Q: 图片质量不符合预期？

A: 不同图源有不同的评分标准：
- Flickr反映**流行度**，可能不完全等同于艺术质量
- KonIQ反映**专家评价**，是最可靠的质量标准
- Wiki反映**获奖作品**，主要是高质量样本

建议混合使用多个图源以获得平衡的数据集。

### Q: 如何避免下载重复图片？

A: 工具已内置去重机制：
- 自动跳过已下载的URL
- 使用感知哈希检测内容相似的图片
- 去重记录保存在 `downloaded.json`

如需重新开始，删除此文件即可。

## 技术架构

```
┌─────────────────────────────────────────────┐
│           命令行接口 (main)                  │
└─────────────────┬───────────────────────────┘
                  │
         ┌────────▼────────┐
         │ ImageDownloader │
         │  (主控制器)      │
         └────────┬────────┘
                  │
      ┌───────────┼───────────┐
      │           │           │
┌─────▼─────┐ ┌──▼──┐ ┌──────▼──────┐
│  Flickr   │ │KonIQ│ │WikiCommons  │
│  Source   │ │Source│ │   Source    │
└─────┬─────┘ └──┬──┘ └──────┬──────┘
      │          │            │
      └──────────┼────────────┘
                 │
         ┌───────▼────────┐
         │  评分归一化     │
         │   (0-10分)      │
         └───────┬────────┘
                 │
         ┌───────▼────────┐
         │  去重 + 下载    │
         │  DeduplicationManager │
         └───────┬────────┘
                 │
         ┌───────▼────────┐
         │  ../rated/     │
         │  (输出目录)     │
         └────────────────┘
```

## 许可证与使用限制

- **本工具代码**: MIT许可证，可自由使用和修改
- **Flickr图片**: 遵循各图片的原始许可（请检查individual photo licenses）
- **KonIQ-10k**: 学术研究使用，请引用原论文
- **Wiki Commons**: 遵循Creative Commons和公有领域许可

## 引用

如果您在研究中使用本工具或KonIQ-10k数据集，请引用：

```bibtex
@article{hosu2020koniq,
  title={KonIQ-10k: An ecologically valid database for deep learning of blind image quality assessment},
  author={Hosu, Vlad and Lin, Hanhe and Sziranyi, Tamas and Saupe, Dietmar},
  journal={IEEE Transactions on Image Processing},
  volume={29},
  pages={4041--4056},
  year={2020}
}
```

## 更新日志

### v1.0.0 (2026-01-20)
- 首次发布
- 支持Flickr、KonIQ-10k、Wiki Commons三个图源
- 统一0-10分评分系统
- 智能去重和自动切换
- 完整的命令行接口

## 贡献与反馈

如有问题或建议，欢迎提交Issue或Pull Request。

## 相关链接

- [Flickr API文档](https://www.flickr.com/services/api/)
- [KonIQ-10k数据集](http://database.mmsp-kn.de/koniq-10k-database.html)
- [Wiki Loves Monuments](https://www.wikilovesmonuments.org/)
- [Wikimedia Commons API](https://commons.wikimedia.org/w/api.php)
