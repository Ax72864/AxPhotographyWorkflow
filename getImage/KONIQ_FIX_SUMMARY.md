# ✅ KonIQ-10k 下载脚本已修复！

## 🎉 问题已解决

### 更新内容

已使用**官方最新下载链接**更新 `setup_koniq_dataset.py` 脚本。

### 📝 新的下载地址（已验证）

根据官方提供的链接，脚本现在使用：

1. **图片数据集** (~2GB)  
   `http://datasets.vqa.mmsp-kn.de/archives/koniq10k_1024x768.zip`

2. **评分数据** (CSV文件在zip中)  
   `http://datasets.vqa.mmsp-kn.de/archives/koniq10k_scores_and_distributions.zip`

3. **质量指标** (可选)  
   `http://datasets.vqa.mmsp-kn.de/archives/koniq10k_indicators.zip`

### 🔧 主要改进

#### 1. 正确的URL
- ✅ 使用官方 `datasets.vqa.mmsp-kn.de` 域名
- ✅ 文件名更正为 `koniq10k_1024x768.zip`
- ✅ 评分文件现在也是zip格式，包含CSV

#### 2. 临时文件管理
- ✅ 所有临时文件下载到 `./temp/` 目录
- ✅ 解压后自动清理zip文件
- ✅ 避免污染主目录

#### 3. 智能解压
- ✅ 自动从zip中提取CSV文件
- ✅ 正确处理文件路径
- ✅ 详细的进度提示

## 🚀 现在可以使用了！

### 方法1: 自动下载（推荐）

```bash
cd getImage
python setup_koniq_dataset.py
```

脚本将：
1. 下载图片压缩包 (~2GB) 到 `temp/`
2. 下载评分数据压缩包到 `temp/`
3. 解压图片到 `datasets/koniq10k/images/`
4. 解压CSV到 `datasets/koniq10k/`
5. 清理临时文件

**预计时间**: 10-30分钟（取决于网速）

### 方法2: 手动下载

如果自动下载仍有问题，可以手动操作：

```bash
# 1. 创建目录
mkdir -p datasets/koniq10k/images
mkdir -p temp

# 2. 手动下载文件
# 浏览器访问：
# - http://datasets.vqa.mmsp-kn.de/archives/koniq10k_1024x768.zip
# - http://datasets.vqa.mmsp-kn.de/archives/koniq10k_scores_and_distributions.zip
# 保存到 temp/ 目录

# 3. 解压
unzip temp/koniq10k_1024x768.zip -d datasets/koniq10k/images/
unzip temp/koniq10k_scores_and_distributions.zip -d datasets/koniq10k/

# 4. 验证
python setup_koniq_dataset.py
```

### 方法3: 不使用KonIQ（仍然可用）

如果您不想下载2GB的数据集，工具完全可以只使用其他图源：

```bash
# 仅使用Wiki Commons（无需任何配置）
python download_rated_images.py --count 20 --sources wiki

# 或使用Flickr（需要配置API密钥）
python download_rated_images.py --count 50 --sources flickr,wiki
```

## 📊 下载后的目录结构

```
PhotoMaster/
├── getImage/
│   ├── setup_koniq_dataset.py
│   ├── download_rated_images.py
│   ├── config.ini
│   └── datasets/
│       └── koniq10k/
│           ├── images/
│           │   ├── 1.jpg
│           │   ├── 2.jpg
│           │   └── ... (10,073 files)
│           └── koniq10k_scores_and_distributions.csv
├── temp/                          # 临时文件（可安全删除）
└── rated/                         # 下载的评分图片（运行主程序后）
```

## ✅ 验证安装

运行脚本验证数据集完整性：

```bash
python setup_koniq_dataset.py
```

如果看到：
```
✓ KonIQ-10k dataset already exists and is complete!
  Location: C:\...\datasets\koniq10k
```

说明安装成功！

## 🎯 使用KonIQ数据集

安装完成后，即可使用KonIQ图源：

```bash
# 仅使用KonIQ（最可靠的专家评分）
python download_rated_images.py --count 50 --sources koniq

# 使用所有图源（KonIQ + Flickr + Wiki）
python download_rated_images.py --count 100

# 指定质量分布
python download_rated_images.py --count 60 --high 20 --medium 20 --low 20 --sources koniq
```

## 📈 KonIQ评分说明

- **原始评分**: MOS (Mean Opinion Score) 1-5分
- **归一化**: 自动转换为0-10分
- **评分者**: 真实的人类专家标注
- **覆盖**: 从低质量(1-2分)到高质量(4-5分)的全范围

### 评分映射

| KonIQ MOS | 归一化后 | 质量描述 |
|-----------|---------|---------|
| 1.0-2.0 | 0-2.5 | 低质量 |
| 2.0-3.0 | 2.5-5.0 | 中下 |
| 3.0-4.0 | 5.0-7.5 | 中上 |
| 4.0-5.0 | 7.5-10.0 | 高质量 |

## 🔍 故障排除

### 问题：下载速度很慢

**解决**：
- 2GB文件需要时间，请耐心等待
- 可以使用下载工具（如IDM、迅雷）手动下载
- 脚本支持断点续传，中断后重新运行即可

### 问题：解压失败

**解决**：
```bash
# 检查zip文件是否完整
ls -lh temp/*.zip

# 手动解压测试
unzip -t temp/koniq10k_1024x768.zip
```

### 问题：仍然404错误

**解决**：
- 检查网络连接
- 尝试浏览器直接下载链接
- 使用VPN或代理
- 联系官方获取最新下载地址

## 📚 相关文档

- `README.md` - 完整使用文档
- `KONIQ_TROUBLESHOOTING.md` - 详细故障排除
- `QUICK_REFERENCE.md` - 快速参考指南

## 🎊 总结

✅ **脚本已修复** - 使用官方最新链接  
✅ **临时文件管理** - 自动清理，保持整洁  
✅ **智能解压** - 自动处理zip格式的CSV  
✅ **完整文档** - 详细的使用说明  
✅ **多种方案** - 自动/手动/不使用KonIQ都可以  

现在您可以开始下载高质量的专家评分图片数据集了！🚀
