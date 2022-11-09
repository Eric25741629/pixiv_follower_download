import os
from PIL import Image
import cv2 as cv
import numpy as np
from tqdm import tqdm, trange
import concurrent.futures
import tqdm as tqdm
def PNG_JPG(PngPath):
    #img = cv.imread(PngPath, 0)
    try:
        img =cv.imdecode(np.fromfile(PngPath, dtype=np.uint8), -1)
        #print(img.shape[::-2])
        t,w, h = img.shape[::-1]
        infile = PngPath
        outfile = os.path.splitext(infile)[0] + ".jpg"
        img = Image.open(infile)
        #img = img.resize((int(w / 2), int(h / 2)), Image.ANTIALIAS)
        #print()
        try:
            if len(img.split()) == 4:
                # prevent IOError: cannot write mode RGBA as BMP
                r, g, b, a = img.split()
                #img.save(outfile)
                img = Image.merge("RGB", (r, g, b))
                img.convert('RGB').save(outfile, quality=98)
                os.rename(PngPath,r'E:/png/'+os.path.splitext(infile)[0].split("/")[1]+".png")

            else:
                #img.save(outfile)
                img.convert('RGB').save(outfile, quality=98)
                os.rename(PngPath,r'E:/png/'+os.path.splitext(infile)[0].split("/")[1]+".png")
                #os.remove(PngPath)
            return outfile
        except Exception as e:
            print("PNG转换JPG 错误", e)
    except Exception as e:
        print("PNG转换JPG 错误", e)

#path_root = os.getcwd()
Path=r'E:\8/'
img_dir = os.listdir(Path)
imglist=[]
for img in img_dir:
    #print(img)
    if img.endswith('.png'):
        PngPath= Path + img
        imglist.append(PngPath)
with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:  
    url = list(tqdm.tqdm(executor.map(PNG_JPG, imglist), total=len(imglist))) 
img_dir = os.listdir(Path)
for img in img_dir:
    print(img)