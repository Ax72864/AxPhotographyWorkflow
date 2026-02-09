import os,sys,shutil
import rawpy

def prepare(dst):
    keep_path = os.path.join(dst,"photos")
    discard_path = os.path.join(dst,"discard")
    remove_path = os.path.join(dst,"remove")
    select_path = os.path.join(dst,"select")
    if not os.path.exists(keep_path):
        os.mkdir(keep_path)
    if not os.path.exists(discard_path):
        os.mkdir(discard_path)
    if not os.path.exists(remove_path):
        os.mkdir(remove_path)
    if not os.path.exists(select_path):
        os.mkdir(select_path)

def organize(path):
    keep_path = os.path.join(dst,"photos")
    discard_path = os.path.join(dst,"discard")
    remove_path = os.path.join(dst,"remove")
    select_path = os.path.join(dst,"select")
    keep_files = set()
    discard_files = set()
    remove_files = set()
    select_files = set()
    for root, dirs, files in os.walk(path):
        for file in files:
            # print(file,os.path.join(root,file))
            sp = file.split(".")
            ext = sp[1].lower()
            basename = sp[0]
            file_path = os.path.join(root,basename)
            print(f"file: {basename}  {ext}   {file_path}")
            if ext == "arw":
                keep_files.add(file_path)
            elif ext == "hif":
                discard_files.add(file_path)
            elif ext == "dng":
                select_files.add(file_path)
    print("="*16+" Keep "+"="*16)
    for keep in keep_files:
        if keep in discard_files:
            discard_files.remove(keep)
            remove_files.add(keep)
        tar = os.path.join(keep_path,os.path.basename(keep)+".ARW")
        print(keep+".ARW","=>",tar)
        shutil.move(keep+".ARW",tar)
    
    print("="*16+" Discard "+"="*16)
    for discard in discard_files:
        tar = os.path.join(discard_path,os.path.basename(discard)+".HIF")
        print(discard+".HIF","=>",tar)
        shutil.move(discard+".HIF",tar)

    print("="*16+" Remove "+"="*16)
    for remove in remove_files:
        tar = os.path.join(remove_path,os.path.basename(remove)+".HIF")
        print(remove+".HIF","=>",tar)
        shutil.move(remove+".HIF",tar)

    print("="*16+" Select "+"="*16)
    for select in select_files:
        tar = os.path.join(select_path,os.path.basename(select)+".DNG")
        print(select+".DNG","=>",tar)
        shutil.move(select+".DNG",tar)


def checkUnkeep(path):
    removefiles = []
    for root, dirs, files in os.walk(path):
        for file in files:
            sp = file.split(".")
            ext = sp[1].lower()
            basename = sp[0]
            file_path = os.path.join(root,basename)
            if ext == "hif":
                raw_path = file_path+".ARW"
                if not os.path.exists(raw_path):
                    removefiles.append(file_path+".HIF")
    return removefiles

def removeUnkeep(path):
    for root, dirs, files in os.walk(path):
        for file in files:
            sp = file.split(".")
            ext = sp[1].lower()
            basename = sp[0]
            file_path = os.path.join(root,basename)
            if ext == "hif":
                raw_path = file_path+".ARW"
                if not os.path.exists(raw_path):
                    print(f"remove {basename}.HIF")
                    os.remove(file_path+".HIF")

if __name__ == "__main__":
    path = sys.argv[1] 
    dst = sys.argv[2]
    mode = sys.argv[3]

    if mode == "remove":
        removeFiles = checkUnkeep(path)
        op = input(f"将删除{len(removeFiles)}张没有对应ARW的HIF图片{path},输入Y确认")
        if op.lower() == "y":
            removeUnkeep(path)
    else:
        print(f"将整理{path}")
        prepare(dst)
        organize(path)