import threading
import pixiv_api
import requests,json,time
import datetime,os
import zipfile
import loguru
import imageio
from tqdm import tqdm, trange
import concurrent.futures
from functools import partial
import tqdm as tqdm
from pathlib import Path
import numpy as np
import random


def _load_json_list(path):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, list):
            return [str(item).strip() for item in data if str(item).strip()]
    except Exception:
        pass
    return []


def _resolve_user_agent(agent_value=""):
    """
    Resolve UA with best-effort priority:
    1) explicit function arg
    2) %APPDATA%/pixiv_download/cookies.json -> agent
    3) modern fallback UA
    """
    explicit = str(agent_value or "").strip()
    if explicit:
        return explicit
    try:
        cfg = os.path.join(os.getenv("APPDATA") or "", "pixiv_download", "cookies.json")
        if os.path.isfile(cfg):
            with open(cfg, "r", encoding="utf-8") as f:
                data = json.load(f) or {}
            ua = str(data.get("agent", "")).strip()
            if ua:
                return ua
    except Exception:
        pass
    return (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/145.0.0.0 Safari/537.36 Edg/145.0.0.0"
    )

def gif_download(cookie,agent='',path=r'C:\Users',url=None):
    try:
        agent = _resolve_user_agent(agent)
        cookies=random.choice(cookie)
        pid=url.rsplit('/',1)[1].rsplit('_')[0]
        tag,like,pagecount,img_url=pixiv_api.Pixiv_info(
            'https://www.pixiv.net/artworks/'+pid,
            Agent=agent,
            cookie=cookies,
        )
        url='https://www.pixiv.net/ajax/illust/%s/ugoira_meta?lang=zh_tw'%pid
        headers = { 'User-Agent':agent,
                    'Cookie':cookies
                    ,'Referer':('http://www.pixiv.net/'+str(pid))}
        htmlfile = requests.get(url,headers=headers,verify=False,stream=True)
        htmlfile.raise_for_status() 
        gif_info=json.loads((htmlfile.content))['body']
        download_url=gif_info['originalSrc']
        delay_info=[item["delay"] for item in gif_info["frames"]]
        delay=sum(delay_info)/len(delay_info)
        url=download_url
        headers = { 'User-Agent':agent,
                    'Cookie':cookies,
                    'Referer':('http://www.pixiv.net/'+str(pid))}
        htmlfile = requests.get(url,headers=headers,verify=False,stream=True)
        size = 0
        chunk_size = 1024
        #content_size=int(htmlfile.headers['content-length'])
        #print(content_size)
        if htmlfile.status_code == 200: #判断是否响应成功
                #print('Start download,[File size]:{size:.2f} MB'.format(size = content_size / chunk_size /1024)) #开始下载，显示下载文件大小
                rename=('illust_'+pid+((datetime.datetime.now()).strftime('_%Y%m%d_%H%M%S.zip')))
                #print(rename)
                if 'R-18G' in tag:
                    if not os.path.exists(path+'R-18G/'):
                            #print('mkdir ' + path)
                            os.mkdir(path+'/R-18G/')    
                    filepath = path+'/R-18G/'+rename #設置圖片名稱，注：必须加上扩展名
                else:
                    filepath = path+rename #設置圖片名稱，注：必须加上扩展名
                with open(filepath,'wb') as file: #显示进度条
                    for data in htmlfile.iter_content(chunk_size = chunk_size):
                        file.write(data)
                        size +=len(data)
                        # print('\r'+'[%s]:%s%.2f%%'% (rename,'█'*int(size*50/ content_size), float(size / content_size * 100)) ,end=' ')  
        temp_file_list = []
        file_path = path+pid
        try:
            os.mkdir(file_path)
        except Exception as err:
            print(err)
            pass
        zipo = zipfile.ZipFile(filepath,"r")
        for file in zipo.namelist():
            temp_file_list.append(os.path.join(file_path,file))
            zipo.extract(file, file_path)
        zipo.close()
        os.remove(filepath)
        image_data=[]
        for file in temp_file_list:
            image_data.append(imageio.imread(file))
        hashtag=""    
        try:
            for many in tag:
                if len(hashtag)>230: 
                    print(tag+'抓取失敗')
                    raise Exist
                else: 
                    hashtag=hashtag+' '+many
            name=(datetime.datetime.now()).strftime('%Y%m%d_%H%M%S')+'_'+'PID'+pid+hashtag+'.gif' 
        except:
            name='illust_'+pid+((datetime.datetime.now()).strftime('_%Y%m%d_%H%M%S.gif'))
        imageio.mimsave((path+name),image_data,"GIF", duration=delay / 1000)
        for file in temp_file_list:
            os.remove(file)
        return 1
    except Exception as err:
        print(err,cookie)

        return url
class Exist(Exception):
    pass
def Download_Pixiv_url(path,lock,url): 
    global download_start_time
    lock.acquire()
    timetag=download_start_time.strftime('%Y%m%d_%H%M%S')
    download_start_time += datetime.timedelta(seconds=1)
    lock.release()
    for i in range (0,5): #重試5次 如果下載成功 將會直接Return回去
        try:
            pid=str(url).rsplit('/',1)[1].rsplit('_',1)[0]  #圖片id
            tag,like,pagecount,img_url=pixiv_api.Pixiv_info('https://www.pixiv.net/artworks/'+pid)
            if(like==404 and tag ==404):
                return
            p=str(url).rsplit('_',1)[1].rsplit('.',1)[0]    #第幾張圖片
            picture_format=url.rsplit('.')[3]
            headers = { 'User-Agent':'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/99.0.4844.82 Safari/537.36',
                    'Referer':('http://www.pixiv.net/'+str(pid))}
            htmlfile = requests.get(url,headers=headers,verify=False,stream=True,timeout=5)
            htmlfile.raise_for_status() 
            size = 0
            chunk_size = 1024
            hashtag=''
            if htmlfile.status_code == 200: #判断是否响应成功
                try:
                    for many in tag:
                        if len(hashtag)>230: 
                            print(tag+'抓取失敗')
                            raise Exist
                        else: 
                            hashtag=hashtag+' '+many
                    name=timetag+'_'+'PID'+pid+p+hashtag+'.'+picture_format 
                except:
                    name=('illust_'+pid+p+timetag+'.'+picture_format)
                tag=str(tag)
                if 'R-18G' in tag or '糞'in tag or '子宮脫' in tag :
                    if not os.path.exists(path+'/R-18G/'):
                            #print('mkdir ' + path)
                            os.mkdir(path+'/R-18G/')    
                    filepath = path+'/R-18G/'+name #設置圖片名稱，注：必须加上扩展名
                else:
                    filepath = path+name #設置圖片名稱，注：必须加上扩展名
                with open(filepath,'wb') as file: #显示进度条
                    for data in htmlfile.iter_content(chunk_size = chunk_size):
                        file.write(data)
                        size +=len(data)  
            return 1
        except Exist as err:
            print('跳過')
            return 1
        except Exception as err: 
            print(err)
            if '404' in str(err): #只有404會被回傳 因為該網址無法訪問了
                return url+"   "+timetag

global download_start_time
#download_start_time=datetime.datetime(2022, 7, 19, 14, 20, 0)
download_start_time=datetime.datetime.now()
def get_filelist(self,path):
    Filelist = []
    for home, dirs, files in os.walk(path):
        for filename in files:
            # 文件名列表，包含完整路徑
            Filelist.append(os.path.join(home, filename))
            # # 文件名列表，只包含文件名
            # Filelist.append( filename)
    return Filelist
def splitID(Filelist):
    exist_pid=[]
    print(len(Filelist))
    for file in  Filelist :
        if (('jpg' not in file ) and ('png' not in file) and ('gif' not in file)):
            continue
        if('PID'not in file and 'illust' not in file):
            continue
        try:
            id=file.split('PID=')[1].split('_')[0]
            #print(id)
            if len(id)<12 and len(id)>6:
                #print(id)
                exist_pid.append(id)     
        except Exception as err:
            #print(err)
            try:
                id=file.split('PID')[1].split(' ')[0]
                if len(id)<=13 and len(id)>6:
                        #print(id)
                        exist_pid.append(id)
                else:
                    if(str.isdigit(id.split('p')[0])):
                        
                        try:
                            exist_pid.append(id.split('.')[0])
                            #print(id.split('.')[0])
                        except:
                            #print(id)
                            exist_pid.append(id)
            except Exception as err:
                #print(err)
                try:                    
                    id=file.split('_')[1]
                    #print(id)
                    if len(id)<12 and len(id)>6:
                        #print(id)
                        exist_pid.append(id)
                except Exception as err:
                    #print(err)
                    try:      #illust_44773280_20220413_040534.jpg              
                        id=file.split('_')[1]
                        if len(id)<12 and len(id)>6:
                            #print(id)
                            exist_pid.append(id)
                    except Exception as err:
                        print(err)
                        pass
        #print(file)
    exist_pid=np.unique(exist_pid).tolist()
    return exist_pid 
def del_emp_dir(path):
  for (root,dirs,files) in os.walk(path):
    for item in dirs:
      dir = os.path.join(root,item)
      try:
        os.rmdir(dir) #os.rmdir() 方法用於刪除指定路徑的目錄。僅當這資料夾是空的才可以,否則,丟擲OSError。
      except Exception as e:
        print(e)
        pass
def download_img_main(download_path,start,stop,cookie=None,Agent=None):
    #print(Agent)
    for i in range(start,stop+1):
        if not cookie:
            #呼叫錯誤 印出提示訊息
            print("請提供cookie")
            return
        if not Agent:
            Agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/96.0.4664.110 Safari/537.36 Edg/96.0.1054.62'
    
        path=os.getenv('APPDATA')+r'/pixiv_download/'
        with open((path+r"/pictures_url"+str(i)+".txt")) as file:     #讀取寫入的文檔
            print((path+r"/pictures_url"+str(i)+".txt"))
            pictures_urls = [line.rstrip() for line in file]
        #download_path=r'D:\P站爬蟲/new/'
        exist_json_path = os.path.join(path, "exist_pid.json")
        exist_pid = _load_json_list(exist_json_path)
        if not exist_pid:
            legacy_exist_json = os.path.join(path, "exist.json")
            exist_pid = _load_json_list(legacy_exist_json)
        if not exist_pid:
            legacy_exist_txt = os.path.join(path, "existPID.txt")
            try:
                with open(legacy_exist_txt, encoding='utf-8') as file:     # 舊格式相容
                    exist_pid = [line.rstrip().replace('p0', '') for line in file if line.rstrip()]
                try:
                    from safe_io import atomic_write_json
                    atomic_write_json(exist_json_path, sorted(set(exist_pid)))
                except Exception:
                    with open(exist_json_path, 'w', encoding='utf-8') as f:
                        json.dump(sorted(set(exist_pid)), f, ensure_ascii=False, indent=2)
            except Exception:
                exist_pid = []
        #print(splitID(get_filelist(download_path)))
        exist_pid = set(exist_pid + splitID(get_filelist(download_path)))
        gifs=[]
        pics=[]
        for line in trange(0,len(pictures_urls)):
                if 'ugoira' in pictures_urls[line]:
                    #print(pictures_urls[line])
                    pid=str(pictures_urls[line]).rsplit('/',1)[1].rsplit('_',1)[0].rsplit('ugoira0')[0]
                    #print(pid)
                    if(pid in exist_pid):
                        print('跳過')
                        continue
                    gifs.append(pictures_urls[line])
                else:
                    pid=str(pictures_urls[line].rsplit('/',1)[1].rsplit('.')[0].replace('_',"",1))
                    #print(pid)
                    if(pid in exist_pid):
                        print('跳過')
                        continue
                    pics.append(pictures_urls[line])
        loguru.logger.success("分析下載網址完成")    
        loguru.logger.success(len(pics))    
        loguru.logger.success("開始下載gif:")
        
        func=partial(gif_download,cookie,Agent,download_path)
        with concurrent.futures.ThreadPoolExecutor(max_workers=16) as executor:  
            results = list(tqdm.tqdm(executor.map(func, gifs), total=len(gifs))) 
        results=([item for item in results if item!=1])
        print(results)
        if(results!=[]):
            try:
                from safe_io import atomic_write_text
                atomic_write_text(os.path.join(path, f"network_err{int(i)}.txt"), results)
            except Exception:
                try:
                    f = open((path+"/network_err"+str(i)+".txt"), "w+")
                    for err_url in results:
                        f.write(err_url+'\n')
                    f.close()
                except Exception:
                    pass

        loguru.logger.success("開始下載pics:")
        global download_start_time
        if(download_start_time=='NULL'):
            download_start_time=(datetime.datetime.now())
        else:
            download_start_time=download_start_time   
        timetag=time.strftime('%Y%m%d_%H%M%S')
        lock = threading.Lock()
        func=partial(Download_Pixiv_url,download_path,lock)
        with concurrent.futures.ThreadPoolExecutor(max_workers=7) as executor:  
            url = list(tqdm.tqdm(executor.map(func, pics), total=len(pics))) 
        url=([item for item in results if item!=1])
        if(url!=[]):
            myfile = Path((path+"network_err.txt"))
            myfile.touch(exist_ok=True)
            f = open(path+"network_err.txt", "r")            
            exist=f.read()
            f.close()
            if str(url) not in exist:
                try:
                    from safe_io import atomic_append_text
                    atomic_append_text(os.path.join(path, f"network_err{int(i)}.txt"), str(url))
                except Exception:
                    try:
                        f = open((path+"network_err"+str(i)+".txt"), "a+")  
                        f.write(str(url)+'\n')
                        f.close()
                    except Exception:
                        pass
        loguru.logger.success('下載完成%d'%i)

        del_emp_dir(download_path)
        loguru.logger.info('寫入文檔')
        exist_json_path = os.path.join(path, "exist_pid.json")
        exist_pid = set(_load_json_list(exist_json_path))
        if not exist_pid:
            legacy_exist_json = os.path.join(path, "exist.json")
            exist_pid = set(_load_json_list(legacy_exist_json))
        if not exist_pid:
            legacy_exist_txt = os.path.join(path, "existPID.txt")
            try:
                with open(legacy_exist_txt, encoding='utf-8') as file:     # 舊格式相容
                    exist_pid = set(line.rstrip().replace('p0', '') for line in file if line.rstrip())
            except Exception:
                exist_pid = set()
        download_id=splitID(get_filelist(download_path))
        #print(download_id)
        try:
            from safe_io import atomic_write_json
            to_save = sorted(exist_pid.union(download_id))
            atomic_write_json(exist_json_path, to_save)
        except Exception:
            try:
                with open(exist_json_path, "w", encoding='utf-8') as f:
                    json.dump(sorted(exist_pid.union(download_id)), f, ensure_ascii=False, indent=2)
            except Exception:
                pass
        loguru.logger.success('寫入完成')
        #exist_pid = set(exist_pid)
        
        if(pics!=[] or gifs!=[]):
            loguru.logger.warning('等待5秒鐘')
            time.sleep(5)
        #gif_download('https://i.pximg.net/img-original/img/2018/06/24/12/04/57/69378147_ugoira0.jpg',cookie,path)
if __name__ == '__main__':
    #print(splitID(['E:\0\GIF\illust_44773280_20220413_040534.jpg']))
    '''for i in range(0,1):
        main(i) '''
    url='https://i.pximg.net/img-original/img/2022/07/30/00/10/43/100087317_ugoira0.jpg   ' 
    download_path=r'D:\P站爬蟲\new/'
    download_id=splitID(get_filelist(download_path))
    print(len(get_filelist(download_path)))
    print(len(download_id))
    #print(splitID(get_filelist(r'D:\P站爬蟲\35/')))      

    
