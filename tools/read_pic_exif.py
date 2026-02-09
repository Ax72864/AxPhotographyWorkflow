import exifread
import os,sys,subprocess,json

def dump_exif(path):
    with open(path,"rb") as f:
        tags = exifread.process_file(f)
    for tag in tags:
        if tag not in ['JPEGThumbnail', 'TIFFThumbnail', 'Filename']:
            print(f"{tag} : {tags[tag]}")


def read_keywords_with_exiftool(image_path):
    try:
        # 调用 exiftool，输出格式为 JSON
        result = subprocess.run(['exiftool', '-json', image_path], stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        metadata = json.loads(result.stdout)
        
        if metadata and len(metadata) > 0:
            # 关键词通常存储在 "Keywords" 或 "Subject" 字段中
            # for m in metadata:
            #     print(m)
            keywords = metadata[0].get('Keywords', [])
            if not keywords:
                keywords = metadata[0].get('Subject', [])
                
            print("Keywords:", keywords)
        else:
            print("No metadata found.")
            
    except Exception as e:
        print(f"An error occurred: {e}")

if __name__ == "__main__":
    dump_exif(sys.argv[1])
    read_keywords_with_exiftool(sys.argv[1])