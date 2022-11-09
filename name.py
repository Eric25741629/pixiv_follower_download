import easyocr,os,time,shutil
from tqdm import tqdm, trange
from functools import partial
import concurrent.futures
import tqdm as tqdm
import cv2
import numpy as np
def get_filelist(path):
    Filelist = []
    for home, dirs, files in os.walk(path):
        for filename in files:
            # 文件名列表，包含完整路徑
            Filelist.append(filename)
            # # 文件名列表，只包含文件名
            # Filelist.append( filename)
    return Filelist

list1=(get_filelist(r'E:/yolov5y/data/dice/images/train/'))

f = open(("train.txt"), "w+")
for text in list1:
    f.write('data/dice/images/train/'+text+'\n')
f.close()