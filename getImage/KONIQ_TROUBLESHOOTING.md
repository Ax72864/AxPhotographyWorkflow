# KonIQ-10k 数据集下载故障排除

## 问题：自动下载失败（404错误）

如您所见，KonIQ-10k的官方下载链接可能不稳定或已更改。这是正常现象，因为学术数据集的托管地址会变化。

### 解决方案

#### 方案1: 使用Wiki Commons（最简单，无需KonIQ）

如果您只是想快速测试工具，可以完全跳过KonIQ数据集：

```bash
# 直接使用Wiki Commons下载图片
python download_rated_images.py --count 20 --sources wiki
```

Wiki Commons提供的都是获奖摄影作品，质量评分为8-10分。

#### 方案2: 手动下载KonIQ-10k

**官方下载链接（已验证可用）**:

1. **图片数据集** (~2GB)  
   http://datasets.vqa.mmsp-kn.de/archives/koniq10k_1024x768.zip

2. **评分数据** (CSV)  
   http://datasets.vqa.mmsp-kn.de/archives/koniq10k_scores_and_distributions.zip

3. **质量指标** (可选)  
   http://datasets.vqa.mmsp-kn.de/archives/koniq10k_indicators.zip

**手动安装步骤**:

```bash
# 1. 创建目录
mkdir -p datasets/koniq10k/images
mkdir -p temp

# 2. 下载文件到temp目录
# 下载上述三个zip文件

# 3. 解压图片
unzip temp/koniq10k_1024x768.zip -d datasets/koniq10k/images/

# 4. 解压评分CSV
unzip temp/koniq10k_scores_and_distributions.zip -d datasets/koniq10k/

# 5. 清理临时文件
rm -rf temp/*.zip
```

**或者使用自动脚本**:

```bash
python setup_koniq_dataset.py
```

脚本现在使用最新的官方链接，应该可以正常下载。

#### 方案3: 使用Flickr（需要API密钥）

Flickr提供大量图片，涵盖各种质量等级：

1. 访问: https://www.flickr.com/services/apps/create/
2. 创建应用获取API Key和Secret
3. 编辑 `config.ini`：
   ```ini
   [flickr]
   api_key = YOUR_KEY_HERE
   api_secret = YOUR_SECRET_HERE
   ```
4. 使用Flickr下载：
   ```bash
   python download_rated_images.py --count 50 --sources flickr,wiki
   ```

## KonIQ-10k 数据集详情

### 什么是KonIQ-10k？

- **图片数量**: 10,073张
- **评分方式**: 专家标注的MOS (Mean Opinion Score)
- **评分范围**: 1-5分（工具自动归一化到0-10）
- **图片类型**: 自然场景照片，包含各种质量等级
- **文件大小**: 约2GB (1024x768分辨率)

### 为什么推荐KonIQ？

✅ **最可靠的评分**: 真实的人类专家评判，不是算法估计  
✅ **全范围覆盖**: 从低质量到高质量的完整分布  
✅ **学术标准**: 广泛用于图像质量评估研究  
✅ **数量充足**: 10,000+张图片足够大多数测试需求

### KonIQ vs 其他图源对比

| 特性 | KonIQ | Flickr | Wiki Commons |
|------|-------|--------|--------------|
| 评分可靠性 | ⭐⭐⭐⭐⭐ | ⭐⭐⭐ | ⭐⭐⭐⭐ |
| 需要配置 | ❌ 手动下载 | ✅ 需要API | ❌ 无需配置 |
| 样本量 | 10,073 | 无限 | 数千 |
| 质量分布 | 0-10全覆盖 | 0-10全覆盖 | 主要8-10分 |
| 下载速度 | 本地快速 | 网络请求 | 网络请求 |

## 常见问题

### Q: 必须要KonIQ数据集吗？

**A**: 不是！工具完全可以只使用Wiki Commons和/或Flickr运行。KonIQ只是提供最可靠的专家评分。

### Q: 我下载了KonIQ但脚本仍然报错？

**A**: 检查以下几点：
1. 图片是否在 `datasets/koniq10k/images/` 目录下
2. CSV文件是否在 `datasets/koniq10k/` 目录下
3. CSV文件名是否为 `koniq10k_scores_and_distributions.csv`
4. 是否有至少10,000个.jpg文件

### Q: 可以只下载部分KonIQ图片吗？

**A**: 可以！工具会使用任意数量的KonIQ图片。只需确保CSV文件包含对应的记录。

### Q: 其他数据集可以用吗？

**A**: 理论上可以。只需：
1. 图片放在指定目录
2. 提供CSV文件，包含 `image_name` 和 `MOS` 列
3. MOS评分在1-5或0-10范围

## 推荐使用策略

### 策略1: 快速测试（5分钟）
```bash
# 只用Wiki Commons，无需任何配置
python download_rated_images.py --count 10 --sources wiki
```
**适用**: 快速验证工具功能

### 策略2: 混合图源（推荐）
```bash
# 配置Flickr API
# 编辑 config.ini

# 使用Flickr + Wiki
python download_rated_images.py --count 100 --sources flickr,wiki
```
**适用**: 需要大量样本，但KonIQ下载困难

### 策略3: 最佳质量（如果能下载KonIQ）
```bash
# 手动下载并安装KonIQ
# 使用所有三个图源
python download_rated_images.py --count 200
```
**适用**: 需要最可靠的评分标准

## 技术支持

如果仍有问题：

1. 查看详细日志:
   ```bash
   python download_rated_images.py --count 10 --verbose
   ```

2. 检查配置文件:
   ```bash
   cat config.ini
   ```

3. 验证目录结构:
   ```bash
   ls -la datasets/koniq10k/
   ls -la datasets/koniq10k/images/ | head
   ```

## 替代数据集（高级）

如果KonIQ完全无法获取，可以考虑：

- **LIVE IQA**: 另一个图像质量评估数据集
- **TID2013**: 失真图像数据集
- **KADID-10k**: 类似KonIQ的质量评估集

这些需要修改代码以适配不同的CSV格式和评分范围。

## 总结

**关键点**:
- ✅ 工具无需KonIQ即可运行
- ✅ Wiki Commons无需配置即可使用
- ✅ Flickr提供最大样本量（需API密钥）
- ✅ KonIQ提供最可靠评分（如能获取）

选择最适合您需求的图源组合即可！
