import easyocr,os,time,shutil
from tqdm import tqdm, trange
from functools import partial
import concurrent.futures
import tqdm as tqdm
import cv2
import numpy as np
def set_gpus(gpu_index):
    if type(gpu_index) == list:
        gpu_index = ','.join(str(_) for _ in gpu_index)
    if type(gpu_index) ==int:
        gpu_index = str(gpu_index)
    os.environ["CUDA_VISIBLE_DEVICES"] = gpu_index
def get_filelist(path):
    Filelist = []
    homes=[]
    filenames=[]
    for home, dirs, files in os.walk(path):
        for filename in files:
            # 文件名列表，包含完整路徑
            homes.append(home)
            filenames.append(filename)
            # # 文件名列表，只包含文件名
            # Filelist.append( filename)
    return homes,filenames
def fanbox_reader(reader,home,filename):
            if(filename.rsplit(".",1)[1]!='jpg' and filename.rsplit(".",1)[1]!='png' and filename.rsplit(".",1)[1]!='gif'):
                return 0
            try:
                pic =cv2.imdecode(np.fromfile(home+filename, dtype=np.uint8), -1)
                #pic=cv2.imread(home+filename)
                pic = cv2.cvtColor(pic, cv2.COLOR_BGR2GRAY)
                pic=cv2.resize(pic,None,fx=0.8, fy=0.8, interpolation = cv2.INTER_CUBIC)
                #shutil.move(home+filename, home+'路徑帶中文名/'+filename)
            except:
                try:
                    pic =cv2.imdecode(np.fromfile(home+filename, dtype=np.uint8), -1)
                except:
                    return 0
            try:
                result = reader.readtext(pic)
            except Exception as err:
                result = ''  
                print(err)
            for x in range(len(result)):
                check_str=result[x][1].upper().replace(" ","")
                #print(check_str)
                if('FANBOX' in check_str or 'FANTIA' in check_str or 'SAMPLE'in check_str or 'JPEG'in check_str or 'PATREON'in check_str or 'CG'in check_str or 'BOOTH'in check_str or 'COMIC'in check_str):
                    try:    
                        shutil.move(home+filename, home+'Fanbox/'+filename)
                    except:
                        pass
                    return 0
            return 0
def main(path):
    set_gpus(0)
    
    reader = easyocr.Reader(['en'], gpu = True)
    #
    homes,filenames=get_filelist(path)
    if os.path.isdir(homes[0]+'Fanbox'):
        print("目錄存在。")
    else:
        print("目錄不存在。")
        os.makedirs(homes[0]+'Fanbox')
    if os.path.isdir(homes[0]+'路徑帶中文名/'):
        print("目錄存在。")
    else:
        print("目錄不存在。")
        os.makedirs(homes[0]+'路徑帶中文名')         
    #for i in trange(len(homes)):
        #print(filenames[i])
    func=partial(fanbox_reader,reader,homes[0])
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:  
        results = list(tqdm.tqdm(executor.map(func, filenames), total=len(filenames)))
    '''with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:  
        results = list(tqdm.tqdm(executor.map(func, gifs), total=len(gifs)))     '''
if __name__ == '__main__':
    for i in range(106,143):   
        start=time.time()   
        print('現在是第'+str(i))  
        path=r'D:\P站爬蟲/'+str(i)+'/'
        main(path)
        print(time.time()-start)