import json
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
import math
import argparse
import os
import re
import piexif
from fractions import Fraction
import traceback
import copy
import exiftool
import sys
import bisect
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor, as_completed


def create_exiftool():
    """创建带有正确编码设置的ExifTool实例，解决中文Windows路径问题

    通过 -charset filename=utf8 告知ExifTool使用UTF-8处理文件名，
    通过 encoding="utf-8" 告知PyExifTool使用UTF-8进行进程间通信。
    保留默认的 -G（输出分组前缀）和 -n（数值输出）参数。
    """
    common_args = ["-G", "-n", "-charset", "filename=utf8"]
    try:
        return exiftool.ExifTool(common_args=common_args, encoding="utf-8")
    except TypeError:
        # 旧版本PyExifTool不支持encoding参数
        print("⚠️  建议升级PyExifTool以获得更好的中文路径支持: pip install --upgrade PyExifTool")
        return exiftool.ExifTool(common_args=common_args)


@contextmanager
def exiftool_session(et=None):
    """ExifTool会话管理器：如果提供了已启动的实例则直接复用，否则创建新的"""
    if et is not None:
        yield et
    else:
        _et = create_exiftool()
        _et.run()
        try:
            yield _et
        finally:
            _et.terminate()


def bd09_to_wgs84(bd_lon, bd_lat):
    """BD09坐标系转WGS84坐标系"""
    x_pi = 3.14159265358979324 * 3000.0 / 180.0
    pi = 3.1415926535897932384626
    a = 6378245.0
    ee = 0.00669342162296594323
    
    x = bd_lon - 0.0065
    y = bd_lat - 0.006
    z = math.sqrt(x * x + y * y) - 0.00002 * math.sin(y * x_pi)
    theta = math.atan2(y, x) - 0.000003 * math.cos(x * x_pi)
    gcj_lon = z * math.cos(theta)
    gcj_lat = z * math.sin(theta)
    
    dlat = transform_lat(gcj_lon - 105.0, gcj_lat - 35.0)
    dlon = transform_lon(gcj_lon - 105.0, gcj_lat - 35.0)
    radlat = gcj_lat / 180.0 * pi
    magic = math.sin(radlat)
    magic = 1 - ee * magic * magic
    sqrtmagic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((a * (1 - ee)) / (magic * sqrtmagic) * pi)
    dlon = (dlon * 180.0) / (a / sqrtmagic * math.cos(radlat) * pi)
    mglat = gcj_lat - dlat
    mglon = gcj_lon - dlon
    
    return mglon, mglat

def wgs84_to_gcj02(wgs_lon, wgs_lat):
    """WGS84坐标系转GCJ02坐标系"""
    pi = 3.1415926535897932384626
    a = 6378245.0
    ee = 0.00669342162296594323
    
    dlat = transform_lat(wgs_lon - 105.0, wgs_lat - 35.0)
    dlon = transform_lon(wgs_lon - 105.0, wgs_lat - 35.0)
    radlat = wgs_lat / 180.0 * pi
    magic = math.sin(radlat)
    magic = 1 - ee * magic * magic
    sqrtmagic = math.sqrt(magic)
    dlat = (dlat * 180.0) / ((a * (1 - ee)) / (magic * sqrtmagic) * pi)
    dlon = (dlon * 180.0) / (a / sqrtmagic * math.cos(radlat) * pi)
    mglat = wgs_lat + dlat
    mglon = wgs_lon + dlon
    
    return mglon, mglat

def wgs84_to_bd09(wgs_lon, wgs_lat):
    """WGS84坐标系转BD09坐标系"""
    # 先转为GCJ02
    gcj_lon, gcj_lat = wgs84_to_gcj02(wgs_lon, wgs_lat)
    
    # 再转为BD09
    x_pi = 3.14159265358979324 * 3000.0 / 180.0
    z = math.sqrt(gcj_lon * gcj_lon + gcj_lat * gcj_lat) + 0.00002 * math.sin(gcj_lat * x_pi)
    theta = math.atan2(gcj_lat, gcj_lon) + 0.000003 * math.cos(gcj_lon * x_pi)
    bd_lon = z * math.cos(theta) + 0.0065
    bd_lat = z * math.sin(theta) + 0.006
    
    return bd_lon, bd_lat

def transform_lat(lng, lat):
    """纬度转换辅助函数"""
    ret = -100.0 + 2.0 * lng + 3.0 * lat + 0.2 * lat * lat + \
          0.1 * lng * lat + 0.2 * math.sqrt(abs(lng))
    ret += (20.0 * math.sin(6.0 * lng * math.pi) + 20.0 * math.sin(2.0 * lng * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(lat * math.pi) + 40.0 * math.sin(lat / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (160.0 * math.sin(lat / 12.0 * math.pi) + 320 * math.sin(lat * math.pi / 30.0)) * 2.0 / 3.0
    return ret

def transform_lon(lng, lat):
    """经度转换辅助函数"""
    ret = 300.0 + lng + 2.0 * lat + 0.1 * lng * lng + \
          0.1 * lng * lat + 0.1 * math.sqrt(abs(lng))
    ret += (20.0 * math.sin(6.0 * lng * math.pi) + 20.0 * math.sin(2.0 * lng * math.pi)) * 2.0 / 3.0
    ret += (20.0 * math.sin(lng * math.pi) + 40.0 * math.sin(lng / 3.0 * math.pi)) * 2.0 / 3.0
    ret += (150.0 * math.sin(lng / 12.0 * math.pi) + 300.0 * math.sin(lng / 30.0 * math.pi)) * 2.0 / 3.0
    return ret

def parse_iso_datetime(time_str):
    """解析ISO格式时间字符串"""
    # 处理不同的时间格式
    if 'T' in time_str and time_str.endswith('Z'):
        return datetime.fromisoformat(time_str.replace('Z', '+00:00'))
    else:
        return datetime.fromisoformat(time_str)

_LOCAL_TZ = datetime.now().astimezone().tzinfo

def fmt_local(dt):
    """将 datetime 转换为本机时区字符串显示"""
    if dt is None:
        return "未知"
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt)
    if dt.tzinfo is not None:
        dt = dt.astimezone(_LOCAL_TZ)
    return dt.strftime("%Y-%m-%d %H:%M:%S")

def calculate_distance(lat1, lon1, lat2, lon2):
    """计算两点间距离（米）"""
    R = 6371000  # 地球半径，米
    lat1_rad = math.radians(lat1)
    lat2_rad = math.radians(lat2)
    delta_lat = math.radians(lat2 - lat1)
    delta_lon = math.radians(lon2 - lon1)
    
    a = math.sin(delta_lat/2)**2 + math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lon/2)**2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return R * c
class KMLTrackAnalyzer:
    def __init__(self, kml_file):
        self.kml_file = kml_file
        self.coords = []
        self.times = []
        self.speeds = []
        self.track_name = ""
        self.track_description = ""
        self.track_segments = []  # 存储各个轨迹片段信息
        self._parse_kml()
    
    def _parse_kml(self):
        """解析KML文件"""
        namespaces = '{http://www.opengis.net/kml/2.2}'
        
        tree = ET.parse(self.kml_file)
        root = tree.getroot()
        
        doc = root.find(f"{namespaces}Document")
        # 获取轨迹名称和描述
        name_elem = doc.find(f'.//{namespaces}name')
        if name_elem is not None:
            self.track_name = name_elem.text.strip()
        # print("name_elem",name_elem)
        desc_elem = root.find(f'.//{namespaces}description')
        if desc_elem is not None:
            self.track_description = desc_elem.text.strip() if desc_elem.text else ""
        
        # 查找TbuluTrackFolder
        track_folder = root.find(f'.//{namespaces}Folder[@id="TbuluTrackFolder"]')
        # print("track_folder1",track_folder)
        # if track_folder is None:
        #     # 如果没找到特定的Folder，尝试查找任何包含轨迹的Folder
        #     track_folder = root.find(f'.//{namespaces}Folder[name="轨迹"]', namespaces)
        
        # print("track_folder2",track_folder)
        # if track_folder is None:
        #     # 如果还是没找到，尝试直接查找Placemark
        #     placemarks = root.findall('.//Placemark', namespaces)
        # else:
        #     # 在TbuluTrackFolder中查找所有Placemark
        #     placemarks = track_folder.findall('.//Placemark', namespaces)
        placemarks = track_folder.findall(f'.//{namespaces}Placemark')
        # print("placemarks",placemarks)
        if not placemarks:
            raise ValueError("未找到轨迹数据")
        
        print(f"找到 {len(placemarks)} 个轨迹片段")
        
        # 解析每个轨迹片段
        all_coords = []
        all_times = []
        all_speeds = []
        
        for i, placemark in enumerate(placemarks):
            segment_coords, segment_times, segment_speeds = self._parse_track_segment(placemark, namespaces, i + 1)
            
            if segment_coords and segment_times:
                # 记录片段信息
                segment_name_elem = placemark.find(f'.//{namespaces}name')
                segment_name = segment_name_elem.text.strip() if segment_name_elem is not None and segment_name_elem.text else f"轨迹片段{i+1}"
                
                self.track_segments.append({
                    'name': segment_name,
                    'coords': segment_coords,
                    'times': segment_times,
                    'speeds': segment_speeds,
                    'start_time': segment_times[0] if segment_times else None,
                    'end_time': segment_times[-1] if segment_times else None,
                    'point_count': len(segment_coords)
                })
                
                # 合并到总轨迹中
                all_coords.extend(segment_coords)
                all_times.extend(segment_times)
                all_speeds.extend(segment_speeds)
        
        if not all_coords or not all_times:
            raise ValueError("未找到有效的轨迹数据")
        
        # 按时间排序所有轨迹点
        combined_data = list(zip(all_times, all_coords, all_speeds))
        combined_data.sort(key=lambda x: x[0])  # 按时间排序
        
        # 分离排序后的数据
        self.times, self.coords, self.speeds = zip(*combined_data) if combined_data else ([], [], [])
        self.times = list(self.times)
        self.coords = list(self.coords)
        self.speeds = list(self.speeds)
        
        print(f"合并后总数据: {len(self.coords)} 个坐标点, {len(self.times)} 个时间点, {len(self.speeds)} 个速度点")
        if self.track_segments:
            print(f"轨迹片段详情:")
            for i, segment in enumerate(self.track_segments):
                print(f"  片段{i+1}: {segment['name']} - {segment['point_count']}个点 ({fmt_local(segment['start_time'])} 到 {fmt_local(segment['end_time'])})")
    
    def _parse_track_segment(self, placemark, namespaces, segment_num):
        """解析单个轨迹片段"""
        # 查找轨迹数据
        namespace = '{http://www.opengis.net/kml/2.2}'
        gx = '{http://www.google.com/kml/ext/2.2}'
        track = placemark.find(f'.//{gx}Track')
        if track is None:
            print(f"⚠️  片段{segment_num}未找到gx:Track数据")
            return [], [], []
        
        segment_coords = []
        segment_times = []
        segment_speeds = []
        
        # 提取坐标数据
        for coord in track.findall(f'.//{gx}coord'):
            if coord.text:
                parts = coord.text.strip().split()
                if len(parts) >= 3:
                    try:
                        lon, lat, alt = float(parts[0]), float(parts[1]), float(parts[2])
                        segment_coords.append((lon, lat, alt))
                    except ValueError:
                        print(f"⚠️  片段{segment_num}坐标解析错误: {coord.text}")
                        continue
        
        # 提取时间数据 - 尝试多种可能的位置
        when_elements = track.findall(f'.//{namespace}when')
        if not when_elements:
            when_elements = track.findall(f'.//{namespace}when')
        if not when_elements:
            when_elements = track.findall(f'.//{namespace}when')
        
        for when in when_elements:
            if when.text:
                try:
                    segment_times.append(parse_iso_datetime(when.text.strip()))
                except Exception as e:
                    print(f"⚠️  片段{segment_num}时间解析错误: {when.text} - {e}")
                    continue
        
        # 提取速度数据
        extended_data = track.find(f'.//{namespace}ExtendedData')
        if extended_data is None:
            extended_data = track.find(f'.//{namespace}ExtendedData')
        
        if extended_data is not None:
            # 尝试查找速度数据的多种可能格式
            speed_data = extended_data.find(f'.//{namespace}Data[@name="speed"]/value')
            if speed_data is None:
                speed_data = extended_data.find(f'.//{namespace}Data[@name="Speed"]/value')
            if speed_data is None:
                speed_data = extended_data.find(f'.//{namespace}Data[@name="GxTrackExtendedData"]/value')
            if speed_data is None:
                speed_data = extended_data.find(f'.//{namespace}Data[@name="GxTrackExtendedData"]/value')
            
            if speed_data is not None and speed_data.text:
                try:
                    speed_text = speed_data.text.strip()
                    if ';' in speed_text:
                        # 处理分号分隔的格式
                        speed_pairs = speed_text.split(';')
                        for pair in speed_pairs:
                            if pair.strip() and ',' in pair:
                                parts = pair.split(',')
                                if len(parts) >= 2:
                                    try:
                                        speed = float(parts[1])
                                        segment_speeds.append(speed)
                                    except ValueError:
                                        segment_speeds.append(0.0)
                    else:
                        # 尝试直接解析为数字
                        try:
                            speed = float(speed_text)
                            segment_speeds.append(speed)
                        except ValueError:
                            pass
                except Exception as e:
                    print(f"⚠️  片段{segment_num}速度数据解析错误: {e}")
        
        # 确保数据长度一致
        min_length = min(len(segment_coords), len(segment_times)) if segment_coords and segment_times else 0
        if min_length > 0:
            segment_coords = segment_coords[:min_length]
            segment_times = segment_times[:min_length]
            
            # 补齐速度数据
            while len(segment_speeds) < min_length:
                segment_speeds.append(0.0)
            segment_speeds = segment_speeds[:min_length]
        
        print(f"片段{segment_num}: {len(segment_coords)} 个坐标点, {len(segment_times)} 个时间点, {len(segment_speeds)} 个速度点")
        
        return segment_coords, segment_times, segment_speeds
    
    def get_track_summary(self):
        """获取轨迹摘要信息"""
        if not self.coords or not self.times:
            return {"错误": "没有有效的轨迹数据"}
        
        # 计算总距离
        total_distance = 0
        for i in range(1, len(self.coords)):
            lat1, lon1 = self.coords[i-1][1], self.coords[i-1][0]
            lat2, lon2 = self.coords[i][1], self.coords[i][0]
            total_distance += calculate_distance(lat1, lon1, lat2, lon2)
        
        # 计算高度信息
        altitudes = [coord[2] for coord in self.coords]
        min_alt = min(altitudes)
        max_alt = max(altitudes)
        
        # 计算爬升和下降
        total_ascent = 0
        total_descent = 0
        for i in range(1, len(altitudes)):
            diff = altitudes[i] - altitudes[i-1]
            if diff > 0:
                total_ascent += diff
            else:
                total_descent += abs(diff)
        
        # 时间信息
        start_time = self.times[0]
        end_time = self.times[-1]
        duration = end_time - start_time
        
        # 速度信息
        max_speed = max(self.speeds) if self.speeds else 0
        avg_speed = sum(self.speeds) / len(self.speeds) if self.speeds else 0
        
        summary = {
            "轨迹名称": self.track_name,
            "轨迹描述": self.track_description,
            "轨迹片段数": len(self.track_segments),
            "总轨迹点数": len(self.coords),
            "开始时间": fmt_local(start_time),
            "结束时间": fmt_local(end_time),
            "持续时间": str(duration),
            "总距离": f"{total_distance/1000:.2f} 公里",
            "最低海拔": f"{min_alt:.1f} 米",
            "最高海拔": f"{max_alt:.1f} 米",
            "累计爬升": f"{total_ascent:.1f} 米",
            "累计下降": f"{total_descent:.1f} 米",
            "最大速度": f"{max_speed:.1f} km/h",
            "平均速度": f"{avg_speed:.1f} km/h"
        }
        
        # 添加各片段详情
        if self.track_segments:
            summary["轨迹片段详情"] = []
            for i, segment in enumerate(self.track_segments):
                segment_distance = 0
                for j in range(1, len(segment['coords'])):
                    lat1, lon1 = segment['coords'][j-1][1], segment['coords'][j-1][0]
                    lat2, lon2 = segment['coords'][j][1], segment['coords'][j][0]
                    segment_distance += calculate_distance(lat1, lon1, lat2, lon2)
                
                segment_duration = segment['end_time'] - segment['start_time'] if segment['start_time'] and segment['end_time'] else None
                
                summary["轨迹片段详情"].append({
                    "片段名称": segment['name'],
                    "点数": segment['point_count'],
                    "距离": f"{segment_distance/1000:.2f} 公里",
                    "开始时间": fmt_local(segment['start_time']),
                    "结束时间": fmt_local(segment['end_time']),
                    "持续时间": str(segment_duration) if segment_duration else "未知"
                })
        
        return summary
    
    def get_position_at_time(self, target_time):
        """获取指定时间点的位置信息（使用二分查找加速）"""
        if not self.coords or not self.times:
            return None
        
        # 检查时间范围
        if target_time < self.times[0] or target_time > self.times[-1]:
            return None
        
        # 使用二分查找定位最接近的时间点（O(log n) 替代原始 O(n) 线性遍历）
        idx = bisect.bisect_left(self.times, target_time)
        
        if idx == 0:
            closest_index = 0
        elif idx >= len(self.times):
            closest_index = len(self.times) - 1
        else:
            # 比较前后两个点，选择时间差更小的
            diff_left = abs((target_time - self.times[idx - 1]).total_seconds())
            diff_right = abs((target_time - self.times[idx]).total_seconds())
            closest_index = idx - 1 if diff_left <= diff_right else idx
        
        min_diff = abs((target_time - self.times[closest_index]).total_seconds())
        
        # 确定是否需要插值
        interpolated = min_diff > 1.0  # and min_diff < 600 # 如果时间差超过1秒则进行插值
        if min_diff >= 180:
            print(f"时间差过大，坐标可能偏差较大: {fmt_local(target_time)}  差值: {min_diff}")
        # print("closest_index:",closest_index,"min_diff:",min_diff,"interpolated:",interpolated)
        if interpolated:
            # 找到插值用的前后两个点
            if target_time <= self.times[closest_index]:
                if closest_index > 0:
                    i1, i2 = closest_index - 1, closest_index
                else:
                    i1, i2 = 0, 1 if len(self.times) > 1 else 0
            else:
                if closest_index < len(self.times) - 1:
                    i1, i2 = closest_index, closest_index + 1
                else:
                    i1, i2 = len(self.times) - 2 if len(self.times) > 1 else 0, len(self.times) - 1
            
            # 插值计算
            if i1 != i2:
                total_time = (self.times[i2] - self.times[i1]).total_seconds() or 1
                target_offset = (target_time - self.times[i1]).total_seconds()

                weight = target_offset / total_time
                
                lon = self.coords[i1][0] + (self.coords[i2][0] - self.coords[i1][0]) * weight
                lat = self.coords[i1][1] + (self.coords[i2][1] - self.coords[i1][1]) * weight
                alt = self.coords[i1][2] + (self.coords[i2][2] - self.coords[i1][2]) * weight
                speed = self.speeds[i1] + (self.speeds[i2] - self.speeds[i1]) * weight
            else:
                lon, lat, alt = self.coords[closest_index]
                speed = self.speeds[closest_index]
        else:
            lon, lat, alt = self.coords[closest_index]
            speed = self.speeds[closest_index]
        
        # 坐标系转换
        gcj_lon, gcj_lat = wgs84_to_gcj02(lon, lat)
        bd_lon, bd_lat = wgs84_to_bd09(lon, lat)
        
        return {
            "timestamp": target_time.isoformat(),
            "closest_time": self.times[closest_index].isoformat(),
            "time_difference_seconds": min_diff,
            "in_range": True,
            "interpolated": interpolated,
            "coordinates": {
                "WGS84": {"longitude": lon, "latitude": lat},
                "GCJ02": {"longitude": gcj_lon, "latitude": gcj_lat},
                "BD09": {"longitude": bd_lon, "latitude": bd_lat}
            },
            "altitude_meters": alt,
            "speed_kmh": speed
        }

def parse_xmp_metadata_date(xmp_content):
    """从XMP内容中解析DateTimeOriginal"""
    pattern = r'exif:DateTimeOriginal="([^"]+)"'
    match = re.search(pattern, xmp_content)
    if match:
        try:
            return parse_iso_datetime(match.group(1))
        except:
            return None
    return None

def format_gps_coordinate(decimal_degrees, is_latitude=True):
    """将十进制度数转换为度分格式"""
    # 确定方向
    if is_latitude:
        direction = 'N' if decimal_degrees >= 0 else 'S'
    else:
        direction = 'E' if decimal_degrees >= 0 else 'W'
    
    # 取绝对值
    abs_degrees = abs(decimal_degrees)
    
    # 分离度和分
    degrees = int(abs_degrees)
    minutes = (abs_degrees - degrees) * 60
    
    # 格式化为字符串
    return f"{degrees},{minutes:.6f}{direction}"

def format_gps_altitude(altitude_meters):
    """将海拔高度格式化为分数形式"""
    # 转换为厘米，然后表示为分数
    altitude_cm = int(altitude_meters * 100)
    return f"{altitude_cm}/100"

def decimal_to_dms(decimal_degrees):
    """将十进制度数转换为度分秒格式 (DMS)"""
    abs_degrees = abs(decimal_degrees)
    degrees = int(abs_degrees)
    minutes_float = (abs_degrees - degrees) * 60
    minutes = int(minutes_float)
    seconds = (minutes_float - minutes) * 60
    
    # 转换为分数形式
    degrees_frac = (degrees, 1)
    minutes_frac = (minutes, 1)
    seconds_frac = (int(seconds * 1000000), 1000000)  # 精确到微秒
    
    return [degrees_frac, minutes_frac, seconds_frac]

def find_tag_by_id(tag_id):
    """在所有IFD中查找指定ID的标签"""
    all_ifds = [
        ('ImageIFD', piexif.ImageIFD),
        ('ExifIFD', piexif.ExifIFD),
        ('GPSIFD', piexif.GPSIFD),
        ('InteropIFD', piexif.InteropIFD)
    ]
    
    results = []
    for ifd_name, ifd_module in all_ifds:
        for attr in dir(ifd_module):
            if (not attr.startswith('_') and 
                isinstance(getattr(ifd_module, attr), int) and 
                getattr(ifd_module, attr) == tag_id):
                results.append(f"{ifd_name}.{attr}")
    
    return results

def update_dng_gps(dng_file_path, latitude, longitude, altitude, et=None):
    """使用ExifTool更新DNG文件中的GPS信息"""
    try:
        with exiftool_session(et) as _et:
            # 构建ExifTool命令参数
            params = [
                f"-GPSLatitude={abs(latitude)}",
                f"-GPSLatitudeRef={'N' if latitude >= 0 else 'S'}",
                f"-GPSLongitude={abs(longitude)}",
                f"-GPSLongitudeRef={'E' if longitude >= 0 else 'W'}",
                "-GPSVersionID=2.2.0.0",
            ]
            
            # 添加海拔信息
            if altitude is not None:
                params.extend([
                    f"-GPSAltitude={abs(altitude)}",
                    f"-GPSAltitudeRef={'0' if altitude >= 0 else '1'}",
                ])
            
            # 执行更新
            params.append(dng_file_path)
            _et.execute(*params)
            
        return True
        
    except Exception as e:
        print(f"❌ 使用ExifTool更新DNG文件GPS失败 {os.path.basename(dng_file_path)}: {e}")
        traceback.print_exc()
        return False
    
def has_gps_info(file_path, et=None):
    """检查文件是否已有GPS信息"""
    try:
        with exiftool_session(et) as _et:
            output = _et.execute('-j', file_path)
            if not output:
                return False, False
            
            metadata_list = json.loads(output)
            metadata = metadata_list[0] if metadata_list else {}

            # 检查GPS Status字段
            gps_status = metadata.get('EXIF:GPSStatus')
            # print("GPS Status:", gps_status, file_path)
            if gps_status == 'A':
                return True, True  # 有GPS且状态为Active，不应覆盖
            
            # return False,False
            
            # 检查是否有GPS坐标信息
            has_lat = any(key in metadata for key in ['EXIF:GPSLatitude', 'Composite:GPSLatitude'])
            has_lon = any(key in metadata for key in ['EXIF:GPSLongitude', 'Composite:GPSLongitude'])
            
            return has_lat and has_lon, False  # 有GPS但状态不是Active
            
    except Exception as e:
        # 如果检查失败，假设没有GPS信息
        print(f"❌ 检查文件GPS信息失败 {os.path.basename(file_path)}: {e}")
        traceback.print_exc()
        return False, False

def check_xmp_gps_info(xmp_content):
    """检查XMP内容是否已有GPS信息"""
    has_lat = re.search(r'exif:GPSLatitude="[^"]*"', xmp_content) is not None
    has_lon = re.search(r'exif:GPSLongitude="[^"]*"', xmp_content) is not None
    return has_lat and has_lon

def update_file_gps(file_path, latitude, longitude, altitude, force_overwrite=False, overwrite_active=False, et=None):
    """使用ExifTool更新文件中的GPS信息"""
    # print("Updating GPS for:", file_path)
    try:
        # 检查文件是否已有GPS信息
        has_gps, is_active = has_gps_info(file_path, et)
        
        if is_active and not overwrite_active:
            print(f"🔒 GPS状态为Active，跳过: {os.path.basename(file_path)}")
            return False
        
        if has_gps and not force_overwrite:
            print(f"📍 已有GPS信息，跳过: {os.path.basename(file_path)}")
            return False
        
        with exiftool_session(et) as _et:
            # 构建ExifTool命令参数
            params = [
                f"-GPSLatitude={abs(latitude)}",
                f"-GPSLatitudeRef={'N' if latitude >= 0 else 'S'}",
                f"-GPSLongitude={abs(longitude)}",
                f"-GPSLongitudeRef={'E' if longitude >= 0 else 'W'}",
                "-GPSVersionID=2.2.0.0",
            ]
            
            # 添加海拔信息
            if altitude is not None:
                params.extend([
                    f"-GPSAltitude={abs(altitude)}",
                    f"-GPSAltitudeRef={'0' if altitude >= 0 else '1'}",
                ])
            
            # 如果强制覆盖，添加覆盖参数
            # if force_overwrite:
            params.append("-overwrite_original")
            
            # 执行更新
            params.append(file_path)
            print("exiftool execute params:", params)
            _et.execute(*params)
            
        return True
        
    except Exception as e:
        print(f"❌ 更新文件GPS失败 {os.path.basename(file_path)}: {e}")
        return False

def update_xmp_gps(xmp_file_path, latitude, longitude, altitude, force_overwrite=False):
    """更新XMP文件中的GPS信息"""
    try:
        # 读取XMP文件
        with open(xmp_file_path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        # 检查是否已有GPS信息
        if check_xmp_gps_info(content) and not force_overwrite:
            print(f"📍 XMP已有GPS信息，跳过: {os.path.basename(xmp_file_path)}")
            return False
        
        # 格式化GPS坐标
        lat_str = format_gps_coordinate(latitude, True)
        lon_str = format_gps_coordinate(longitude, False)
        alt_str = format_gps_altitude(altitude)
        
        # 移除现有的GPS信息
        content = re.sub(r'exif:GPSLatitude="[^"]*"','', content)
        content = re.sub(r'exif:GPSLongitude="[^"]*"','', content)
        content = re.sub(r'exif:GPSAltitude="[^"]*"','', content)
        content = re.sub(r'exif:GPSVersionID="2.2.0.0"','', content)
        
        pixel_y_pattern = r'(exif:PixelYDimension="[^"]*")'
        match = re.search(pixel_y_pattern, content)
        
        if match:
            # 在exif:PixelYDimension后插入GPS信息
            insert_pos = match.end()
            gps_info = f'\n   exif:GPSLatitude="{lat_str}"\n   exif:GPSLongitude="{lon_str}"\n   exif:GPSAltitude="{alt_str}"\n   exif:GPSVersionID="2.2.0.0"'
            content = content[:insert_pos] + gps_info + content[insert_pos:]
            
            # 写回XMP文件
            with open(xmp_file_path, 'w', encoding='utf-8') as f:
                f.write(content)
            
            return True
        else:
            print(f"⚠️  跳过（未找到PixelYDimension）: {os.path.basename(xmp_file_path)}")
            return False
        
    except Exception as e:
        print(f"❌ 更新XMP文件失败 {xmp_file_path}: {e}")
        traceback.print_exc()
        return False

def find_related_files(raw_file_path):
    """查找RAW文件对应的XMP/DNG/JPG文件"""
    base_dir = os.path.dirname(raw_file_path)
    base_name = os.path.splitext(os.path.basename(raw_file_path))[0]
    
    related_files = []
    
    # 查找同名文件
    for ext in ['.xmp', '.dng', '.jpg', '.jpeg']:
        same_name_file = os.path.join(base_dir, base_name + ext)
        if os.path.exists(same_name_file):
            related_files.append(same_name_file)
    
    # 查找"-已增强-降噪"文件
    enhanced_name = f"{base_name}-已增强-降噪"
    for ext in ['.xmp', '.dng', '.jpg', '.jpeg']:
        enhanced_file = os.path.join(base_dir, enhanced_name + ext)
        if os.path.exists(enhanced_file):
            related_files.append(enhanced_file)
    
    return related_files

def find_files_to_process(photos_dir):
    """查找需要处理的文件"""
    # 查找所有RAW/图片文件
    raw_extensions = {'.arw', '.ori', '.orf', '.cr2', '.nef', '.raf', '.dng', '.tif', '.tiff', '.jpg', '.jpeg'}
    raw_files = []

    for root, _, files in os.walk(photos_dir):
        for filename in files:
            if os.path.splitext(filename)[1].lower() in raw_extensions:
                raw_files.append(os.path.join(root, filename))
    
    files_to_process = []
    for raw_file in raw_files:
        files_to_process.append(raw_file)
        # 查找相关文件
        related_files = find_related_files(raw_file)
        
        if related_files:
            # 如果有相关文件，处理相关文件
            files_to_process.extend(related_files)
        # else:
        #     # 如果没有相关文件，处理原始RAW文件
        #     files_to_process.append(raw_file)
    
    # 去重并返回
    return list(set(files_to_process))

# 将时间转换为 UTC
def to_utc(dt):
    if dt.tzinfo is None:
        # 如果 dt 是 naive (没有时区)，假设它是 UTC
        return dt.replace(tzinfo=timezone.utc)
    else:
        # 转换到 UTC
        return dt.astimezone(timezone.utc)

def parse_datetime_with_timezone(time_str, tz_offset_hours=8):
    # 解析日期时间字符串
    # 格式化字符串必须匹配时间字符串的格式
    naive_datetime = datetime.strptime(time_str, '%Y:%m:%d %H:%M:%S')
    
    # 创建时区信息，假设已知时区为 +8
    tz_info = timezone(timedelta(hours=tz_offset_hours))
    
    # 将 naive datetime 转换为带时区信息的 datetime
    aware_datetime = naive_datetime.replace(tzinfo=tz_info)
    
    return aware_datetime

def parse_time_offset(offset_str):
    """解析 HH:MM:SS 格式的时间偏移，支持前缀 +/-。"""
    match = re.fullmatch(r'([+-])?(\d+):([0-5]\d):([0-5]\d)', offset_str.strip())
    if not match:
        raise ValueError("offset格式错误，请使用 HH:MM:SS，例如 00:05:30 或 -00:05:30")

    sign, hours, minutes, seconds = match.groups()
    offset = timedelta(hours=int(hours), minutes=int(minutes), seconds=int(seconds))
    return -offset if sign == '-' else offset

def format_time_offset(offset):
    """将 timedelta 格式化为 +/-HH:MM:SS。"""
    total_seconds = int(offset.total_seconds())
    sign = "-" if total_seconds < 0 else "+"
    total_seconds = abs(total_seconds)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{sign}{hours:02d}:{minutes:02d}:{seconds:02d}"

def get_file_datetime(file_path, et=None):
    """获取文件的拍摄时间"""
    try:
        if file_path.lower().endswith('.xmp'):
            # XMP文件，从内容解析
            with open(file_path, 'r', encoding='utf-8') as f:
                content = f.read()
            return parse_xmp_metadata_date(content)
        else:
            # 使用 ExifTool 获取其他文件的时间
            with exiftool_session(et) as _et:
                output = _et.execute('-j', file_path)
                if not output:
                    return None
                metadata_list = json.loads(output)
                
                # metadata_list 是一个列表，取第一个元素即为该文件的元数据字典
                metadata = metadata_list[0] if metadata_list else {}
                
                # 尝试多个时间字段
                time_fields = [
                    'EXIF:DateTimeOriginal',
                    'EXIF:CreateDate',
                    'EXIF:DateTime',
                    'QuickTime:CreateDate',
                    'File:FileModifyDate'
                ]
                for field in time_fields:
                    if field in metadata:
                        time_str = parse_datetime_with_timezone(metadata[field])
                        return time_str
        return None
    except Exception as e:
        print(f"⚠️  获取文件时间失败 {os.path.basename(file_path)}: {e}")
        return None


def batch_get_file_datetimes(file_paths, et, batch_size=200):
    """批量获取文件拍摄时间

    对非XMP文件使用ExifTool批量读取（一次命令处理多个文件），
    对XMP文件使用多线程并行读取，大幅减少总耗时。

    Args:
        file_paths: 文件路径列表
        et: 已启动的ExifTool实例
        batch_size: 每批通过ExifTool处理的文件数

    Returns:
        dict: {file_path: datetime} 映射
    """
    results = {}

    # 分离XMP和非XMP文件
    xmp_files = []
    non_xmp_files = []
    for fp in file_paths:
        if fp.lower().endswith('.xmp'):
            xmp_files.append(fp)
        else:
            non_xmp_files.append(fp)

    # 并行处理XMP文件（纯文件I/O，不需要ExifTool）
    if xmp_files:
        def read_xmp_time(fp):
            try:
                with open(fp, 'r', encoding='utf-8') as f:
                    content = f.read()
                return fp, parse_xmp_metadata_date(content)
            except Exception as e:
                return fp, None

        with ThreadPoolExecutor(max_workers=min(8, len(xmp_files))) as executor:
            futures = [executor.submit(read_xmp_time, fp) for fp in xmp_files]
            for future in as_completed(futures):
                fp, dt = future.result()
                if dt is not None:
                    results[fp] = dt
                else:
                    print(f"⚠️  无法解析时间: {os.path.basename(fp)}")

    # 批量处理非XMP文件（通过ExifTool一次命令读取多个文件的元数据）
    total = len(file_paths)
    processed = len(xmp_files)

    time_fields = [
        'EXIF:DateTimeOriginal',
        'EXIF:CreateDate',
        'EXIF:DateTime',
        'QuickTime:CreateDate',
        'File:FileModifyDate'
    ]

    for i in range(0, len(non_xmp_files), batch_size):
        batch = non_xmp_files[i:i + batch_size]
        try:
            # 一次ExifTool命令读取整批文件的时间相关标签
            output = et.execute(
                '-j',
                '-DateTimeOriginal', '-CreateDate',
                '-DateTime', '-FileModifyDate',
                *batch
            )

            if output:
                metadata_list = json.loads(output)
                for j, metadata in enumerate(metadata_list):
                    if j >= len(batch):
                        break
                    fp = batch[j]

                    parsed = False
                    for field in time_fields:
                        if field in metadata:
                            try:
                                dt = parse_datetime_with_timezone(metadata[field])
                                results[fp] = dt
                                parsed = True
                                break
                            except Exception:
                                continue

                    if not parsed:
                        print(f"⚠️  无法解析时间: {os.path.basename(fp)}")
            else:
                for fp in batch:
                    print(f"⚠️  无法解析时间: {os.path.basename(fp)}")

        except Exception as e:
            # 批量读取失败时，逐个处理作为回退
            for fp in batch:
                try:
                    dt = get_file_datetime(fp, et)
                    if dt is not None:
                        results[fp] = dt
                    else:
                        print(f"⚠️  无法解析时间: {os.path.basename(fp)}")
                except Exception as e2:
                    print(f"❌ 读取文件失败: {os.path.basename(fp)} - {e2}")

        processed += len(batch)
        print(f"\r进度: {processed}/{total} ({processed / total * 100:.2f}%)", end="")

    return results


def fix_photos_gps(kml_file, photos_dir, force_overwrite=False, overwrite_active=False, offset=timedelta(0)):
    """修复照片的GPS信息"""
    print(f"🚀 开始GPS修复...")
    print(f"KML文件: {kml_file}")
    print(f"照片目录: {photos_dir}")
    print(f"覆盖GPS数据: {'是' if force_overwrite else '否'}")
    print(f"覆盖机内GPS数据: {'是' if overwrite_active else '否'}")
    print(f"照片时间偏移: {format_time_offset(offset)}")

    # 创建轨迹分析器
    try:
        analyzer = KMLTrackAnalyzer(kml_file)
    except Exception as e:
        print(f"❌ 解析KML文件失败: {e}")
        return
    
    if not analyzer.times:
        print("❌ KML文件中没有时间信息，无法进行GPS修复")
        return
    
    print(f"📊 轨迹信息: {len(analyzer.coords)} 个点，时间范围 {fmt_local(analyzer.times[0])} 到 {fmt_local(analyzer.times[-1])}")
    
    # 查找需要处理的文件
    print("🔍 正在查找需要处理的文件...")
    files_to_process = find_files_to_process(photos_dir)
    
    if not files_to_process:
        print(f"❌ 在目录 {photos_dir} 中未找到任何需要处理的文件")
        return
    
    print(f"📁 找到 {len(files_to_process)} 个需要处理的文件")
    
    # 创建单个ExifTool实例，在整个处理流程中复用，避免反复启停进程
    et = create_exiftool()
    et.run()

    try:
        # 批量解析所有文件的时间信息
        print("📅 正在解析时间信息...")
        file_times = batch_get_file_datetimes(files_to_process, et)

        file_time_list = [(fp, dt) for fp, dt in file_times.items()]

        if not file_time_list:
            print("\n❌ 没有找到任何有效的时间信息")
            return
        print("\n✅ 时间解析完成")
        
        # 按时间排序
        file_time_list.sort(key=lambda x: x[1])
        print(f"✅ 按时间排序完成，共 {len(file_time_list)} 个文件有效")
        print(f"⏰ 时间范围: {fmt_local(file_time_list[0][1])} 到 {fmt_local(file_time_list[-1][1])}")
        
        # 统计信息
        processed_count = len(file_time_list)
        updated_count = 0
        error_count = 0
        out_of_range_count = 0
        skipped_count = 0
        
        # 按时间顺序处理文件
        for i, (file_path, photo_time) in enumerate(file_time_list):
            try:
                # 显示进度
                progress = f"[{i+1}/{len(file_time_list)}]"
                # 从轨迹中获取对应位置（已使用二分查找加速）
                lookup_time = photo_time + offset
                position_info = analyzer.get_position_at_time(lookup_time)
                
                if position_info is None:
                    out_of_range_count += 1
                    print(f"{progress} ⚠️ 时间超出轨迹范围，跳过: {os.path.basename(file_path)} (照片时间 {fmt_local(photo_time)}, 查找时间 {fmt_local(lookup_time)})")
                    continue
                
                # 提取WGS84坐标
                wgs84_coords = position_info['coordinates']['WGS84']
                latitude = wgs84_coords['latitude']
                longitude = wgs84_coords['longitude']
                altitude = position_info['altitude_meters']
                
                # 更新文件GPS信息
                success = False
                if file_path.lower().endswith('.xmp'):
                    success = update_xmp_gps(file_path, latitude, longitude, altitude, force_overwrite)
                    
                    # 检查并更新对应的DNG文件
                    xmp_dir = os.path.dirname(file_path)
                    xmp_basename = os.path.basename(file_path)
                    xmp_name_without_ext = os.path.splitext(xmp_basename)[0]
                    
                else:
                    success = update_file_gps(file_path, latitude, longitude, altitude, force_overwrite, overwrite_active, et)

                basename = os.path.basename(file_path)
                name_without_ext = os.path.splitext(basename)[0]
                # 构造DNG文件名：A.xmp => A-已增强-降噪.dng
                dng_filename = f"{name_without_ext}-已增强-降噪.dng"
                dng_file_path = os.path.join(os.path.dirname(file_path), dng_filename)
                if os.path.exists(dng_file_path):
                    if update_file_gps(dng_file_path, latitude, longitude, altitude, force_overwrite, overwrite_active, et):
                        print(f"📷 DNG文件GPS已更新: {dng_filename}")


                if success:
                    updated_count += 1
                    time_diff = position_info['time_difference_seconds']
                    interpolated = "插值" if position_info['interpolated'] else "精确"
                    print(f"{progress} ✅ 已更新: {os.path.basename(file_path)} ({interpolated}, 时差{time_diff:.1f}s)")
                else:
                    skipped_count += 1
                    
            except Exception as e:
                error_count += 1
                print(f"{progress} ❌ 处理文件失败: {os.path.basename(file_path)} - {e}")
                traceback.print_exc()

    finally:
        # 确保ExifTool进程被正确关闭
        et.terminate()
    
    # 打印统计结果
    print("\n" + "="*60)
    print("📈 GPS修复完成统计")
    print("="*60)
    print(f"总文件数: {len(files_to_process)}")
    print(f"成功解析时间: {processed_count}")
    print(f"成功更新GPS: {updated_count}")
    print(f"跳过处理: {skipped_count}")
    print(f"时间超出范围: {out_of_range_count}")
    print(f"处理失败: {error_count}")
    print(f"成功率: {(updated_count/processed_count*100):.1f}%" if processed_count > 0 else "0%")

def main():
    parser = argparse.ArgumentParser(description='KML轨迹分析工具（支持两步路格式）')
    parser.add_argument('kml_file', help='KML文件路径')
    parser.add_argument('-t', '--time', help='查询时间点 (格式: 2025-08-23T11:40:15+08:00)')
    parser.add_argument('-s', '--summary', action='store_true', help='显示轨迹摘要信息')
    parser.add_argument('--fixgps', metavar='PHOTOS_DIR', help='修复照片GPS信息，指定包含照片文件的目录')
    parser.add_argument('-f', '--force', action='store_true', help='覆盖已有的GPS信息）')
    parser.add_argument('-a', '--active', action='store_true', help='覆盖机内的GPS信息（GPS Status为"Measurement Active"）')
    parser.add_argument('--offset', default='00:00:00', help='同步GPS时加到照片时间上的偏移，格式 HH:MM:SS，支持负数，例如 --offset=-00:05:30')
    
    args = parser.parse_args()
    
    try:
        # 检查文件是否存在
        if not os.path.exists(args.kml_file):
            print(f"❌ 文件不存在: {args.kml_file}")
            return
        
        # GPS修复功能
        if args.fixgps:
            if not os.path.exists(args.fixgps):
                print(f"❌ 照片目录不存在: {args.fixgps}")
                return
            
            try:
                offset = parse_time_offset(args.offset)
            except ValueError as e:
                print(f"❌ {e}")
                return

            fix_photos_gps(args.kml_file, args.fixgps, args.force, args.active, offset)
            return
        
        # 创建分析器
        print("🚀 开始解析KML文件...")
        analyzer = KMLTrackAnalyzer(args.kml_file)
        
        # 显示摘要信息
        if args.summary:
            summary = analyzer.get_track_summary()
            print("\n" + "="*60)
            print("📊 轨迹摘要信息")
            print("="*60)
            for key, value in summary.items():
                print(f"{key}: {value}")
        
        # 查询指定时间点
        if args.time:
            try:
                target_time = parse_iso_datetime(args.time)
                result = analyzer.get_position_at_time(target_time)
                
                if result:
                    print("\n" + "="*60)
                    print(f"📍 时间点 {args.time} 的位置信息")
                    print("="*60)
                    print(f"查询时间: {fmt_local(result['timestamp'])}")
                    print(f"最近轨迹点时间: {fmt_local(result['closest_time'])}")
                    print(f"时间差: {result['time_difference_seconds']:.1f} 秒")
                    print(f"在轨迹范围内: {'是' if result['in_range'] else '否'}")
                    print(f"插值计算: {'是' if result['interpolated'] else '否'}")
                    print(f"\n📐 坐标信息:")
                    print(f"  WGS84:  {result['coordinates']['WGS84']['longitude']:.6f}, {result['coordinates']['WGS84']['latitude']:.6f}")
                    print(f"  GCJ02:  {result['coordinates']['GCJ02']['longitude']:.6f}, {result['coordinates']['GCJ02']['latitude']:.6f}")
                    print(f"  BD09:   {result['coordinates']['BD09']['longitude']:.6f}, {result['coordinates']['BD09']['latitude']:.6f}")
                    print(f"\n📏 其他信息:")
                    print(f"  海拔: {result['altitude_meters']:.1f} 米")
                    print(f"  速度: {result['speed_kmh']:.1f} km/h")
                else:
                    print("❌ 未找到指定时间点的轨迹数据")
                    print("提示: 请检查时间是否在轨迹记录范围内")
                    
            except ValueError as e:
                print(f"❌ 时间格式错误: {e}")
                print("请使用格式: 2025-08-23T11:40:15+08:00")
        
        # 如果没有指定参数，显示帮助信息
        if not args.time and not args.summary:
            print("\n💡 使用提示:")
            print(f"  查看摘要: python {os.path.basename(__file__)} \"{args.kml_file}\" -s")
            print(f"  查询位置: python {os.path.basename(__file__)} \"{args.kml_file}\" -t \"2025-08-23T11:40:15+08:00\"")
            print(f"  修复GPS: python {os.path.basename(__file__)} \"{args.kml_file}\" --fixgps \"/path/to/photos\"")
            print(f"  强制覆盖: python {os.path.basename(__file__)} \"{args.kml_file}\" --fixgps \"/path/to/photos\" -f")
            print(f"  时间偏移: python {os.path.basename(__file__)} \"{args.kml_file}\" --fixgps \"/path/to/photos\" --offset \"00:05:30\"")
            print(f"  负数偏移: python {os.path.basename(__file__)} \"{args.kml_file}\" --fixgps \"/path/to/photos\" --offset=-00:05:30")
    
    except Exception as e:
        print(f"❌ 程序执行失败: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()
