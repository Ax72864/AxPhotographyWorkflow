from birdnetlib import Recording
from birdnetlib.analyzer import Analyzer
from datetime import datetime
from tabulate import tabulate
from pydub import AudioSegment
import os,sys,json,time,requests

'''
    使用Birdnet分析音频文件
'''
def analyze_recording(recording_path,lat=-1,lon=-1,date=None):
    analyzer = Analyzer()
    if date is None:
        date = datetime.fromtimestamp(os.path.getctime(recording_path))

    recording = Recording(
        analyzer,
        recording_path,
        min_conf=0.25,
        lat=lat,
        lon=lon,
        date=date,
    )
    recording.analyze()
    return recording.detections

def get_detection_result(recording_path,lat=-1,lon=-1,date=None):
    dir_path = os.path.dirname(recording_path)
    filename = os.path.basename(recording_path)
    basename = os.path.splitext(filename)[0]
    out_path = os.path.join(dir_path, basename)
    out_file_path = os.path.join(out_path, filename + ".json")
    if os.path.exists(out_file_path):
        print("检测结果已存在，直接读取")
        return json.load(open(out_file_path, "r",encoding="utf8"))
    else:
        print("开始分析音频文件")
        detections = analyze_recording(recording_path,lat,lon,date)
        os.makedirs(out_path, exist_ok=True)
        with open(out_file_path, "w",encoding="utf8") as f:
            f.write(json.dumps(detections, indent=4))
        return detections

'''
    分析音频文件并输出结果
'''
def normal_analyze(recording_path,lat=-1,lon=-1,date=None):
    detections = get_detection_result(recording_path,lat,lon,date)
    format_print_detections(detections)
'''
    格式化输出检测结果
'''
def format_print_detections(detections):
    data = []
    for detection in detections:
        data.append(format_detections(detection))
    headers = ["物种", "时间", "置信度"]
    print(tabulate(data, headers=headers, tablefmt="grid"))


'''
    根据检测结果切分音频
'''
def cut_audio_by_detections(recording_path,detections):
    # 检查输入文件是否存在
    if not os.path.exists(recording_path):
        raise FileNotFoundError(f"找不到输入文件: {recording_path}")
    audio = AudioSegment.from_file(recording_path)
    for detection in detections:
        name = translate_name(detection['scientific_name'])
        print(detection)
        cut_audio(audio, recording_path,detection['start_time'], detection['end_time'], name)

"""
剪裁音频文件
参数:
input_file: 输入音频文件路径
start_time: 起始时间 (HH:MM:SS)
end_time: 结束时间 (HH:MM:SS)
save_name: 保存文件名
output_dir: 输出目录
"""
def cut_audio(audio, recording_path,start_time, end_time, save_name):
    try:
        # 创建输出目录
        dir_path = os.path.dirname(recording_path)
        filename = os.path.basename(recording_path)
        basename = os.path.splitext(filename)[0]
        out_path = os.path.join(dir_path, basename)
        save_dir = os.path.join(out_path, save_name)
        os.makedirs(save_dir, exist_ok=True)
        # 加载音频文件
        
        # 转换时间为毫秒
        start_ms = start_time*1000
        end_ms = end_time*1000
        # 检查时间范围是否有效
        if start_ms >= end_ms:
            raise ValueError("起始时间必须小于结束时间")
        if end_ms > len(audio):
            raise ValueError("结束时间超出音频长度")
        # 剪裁音频
        extracted_audio = audio[start_ms:end_ms]
        # 构建输出文件名
        output_filename = f"{save_name}-{seconds_to_hms(start_time).replace(':','_')}-{seconds_to_hms(end_time).replace(':','_')}.m4a"
        output_path = os.path.join(save_dir, output_filename)
        # 导出音频
        extracted_audio.export(output_path, format="mp3")
        # extracted_audio.export(output_path.replace("m4a","mp4"), format="mp3")
        print(f"{save_name} {seconds_to_hms(start_time)}-{seconds_to_hms(end_time)}片段已保存至: {output_path}")
        return output_path

    except Exception as e:
        print(f"处理音频时出错: {str(e)}")
        return None

'''
    翻译物种名称
'''
BIRD_DATA = None
def get_species_names(species_name, language='zh'):
    global BIRD_DATA
    if BIRD_DATA is None:
        with open("birddata-cn.json", "r",encoding="utf8") as f:
            BIRD_DATA = json.load(f)
    if species_name in BIRD_DATA["Map"]:
        species_data = BIRD_DATA["Map"][species_name]
        if language == "zh":
            return species_data["cn_name"]
        else:
            return species_data["latin_name"]
    return species_name

def translate_name(common_name,lang="zh"):
    if lang == "zh":
        return get_species_names(common_name,"zh")
    else:
        return common_name
    
'''
    格式化相关
'''
def seconds_to_hms(seconds):
    if seconds < 3600:
        return time.strftime('%M:%S', time.gmtime(seconds))
    else:
        return time.strftime('%H:%M:%S', time.gmtime(seconds))

def format_detections(detections):
    formatted_start_time = seconds_to_hms(detections["start_time"])
    formatted_end_time = seconds_to_hms(detections["end_time"])
    return [f"{translate_name(detections['scientific_name'])}({detections['scientific_name']})" , f"{formatted_start_time}-{formatted_end_time}" ,f"{detections['confidence']*100:.1f}%"]

'''
    main
'''
def main():
    print(sys.argv)
    if len(sys.argv) < 3:
        print("用例: python3 birdnet_analyze.py <模式> <录音文件路径> <纬度> <经度> <日期(2024-12-16)>")
        print("模式: ")
        print("      -a 分析音频并输出结果")
        print("      -c 格式化显示Json结果")
        print("      -d 切分音频结果")
        sys.exit(1)

    mode = sys.argv[1]
    recording_path = sys.argv[2]
    lat = -1
    lon = -1
    date = None
    if len(sys.argv) >= 5:
        lat = float(sys.argv[3])
        lon = float(sys.argv[4])
    if len(sys.argv) >= 6:
        date = datetime.strptime(sys.argv[5], "%Y-%m-%d")
    
    print(f"模式: {mode}")

    if mode == "-a":
        normal_analyze(recording_path,lat,lon,date)
    elif mode == "-c":
        with open(recording_path, "r",encoding="utf8") as f:
            detections = json.load(f)
            format_print_detections(detections)
    elif mode == "-d":
        
        detections = get_detection_result(recording_path,lat,lon,date)
        cut_audio_by_detections(recording_path, detections)
    
if __name__ == "__main__":
    main()
