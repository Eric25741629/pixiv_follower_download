import shutil
import os
import concurrent.futures
import tqdm as tqdm
def movefunc(list):
    move(list[0]+list[1],list[2]+list[1])
def move(path,movepath):
    shutil.move(path,movepath)
Path=r'E:\png\test/'
movepath=r'本機\Pixel\內部共用儲存空間\png_backup/'
img_dir = os.listdir(Path)
imglist=[]
for img in img_dir:
    print(img)
    if img.endswith('.png'):
        PngPath=[Path , img,movepath]
        imglist.append(PngPath)
with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:  
    url = list(tqdm.tqdm(executor.map(movefunc, imglist), total=len(imglist))) 
