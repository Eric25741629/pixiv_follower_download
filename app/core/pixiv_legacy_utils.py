"""Shadowed / unused legacy free functions split out of ``pixiv_api.py``.

These module-level functions were superseded by class-method equivalents on
the worker threads (``thread_following.illusts`` / ``.get_follow_illust``,
``thread_url_fetch.get_download_url``) or are standalone utility-script
entrypoints with no live caller in the GUI pipeline. They are kept for
backward compatibility — ``pixiv_api`` re-imports everything here at the bottom
of the module (``from app.core.pixiv_legacy_utils import *`` plus the
underscore names the helper test imports) so the ``from pixiv_api import *``
star surface and ``pixiv_api.NAME`` attribute lookups are byte-identical.

``_pixiv_info_with_retry`` calls ``Pixiv_info`` (which stays in ``pixiv_api``);
it is imported lazily inside the function so this module imports nothing from
``pixiv_api`` at module load time — no import cycle.
"""
import json
import os
import random
import threading
import time
from queue import Queue
from time import sleep

import bs4
import requests
from tqdm import trange

from app.core.pixiv_thread_utils import safe_json


def get_follow_illust(id,headers,state,times):
    '''獲得所有你關注的畫師 需輸入查詢的ID 第幾個 偽裝 公開/私人'''
    url = ('https://www.pixiv.net/ajax/user/{}/following?offset='+str(times)+'&limit=100&rest='+state+'&tag=&lang=zh_tw')

    res = requests.get(url.format(id), headers=headers, timeout=(10, 30))
    resdicts = safe_json(res, 'body', 'users', default=[])
    return [int(_.get('userId')) for _ in resdicts]
def illusts(id,cookie,Agent):				#輸入你的id得到你所有關注的P站畫師
    headers = {
        'User-Agent': Agent,
        'Cookie':cookie
        ,'referer': 'https://www.pixiv.net/users/'+id+'/following',
    }
    times=0
    pixiv_author_id=[]
    url = ('https://www.pixiv.net/ajax/user/27915696/following?offset='+str(times)+'&limit=1&rest=show&tag=&lang=zh_tw') # 访问存有画师所有作品
    print(url)
    res = requests.get(url, headers=headers, timeout=(10, 30))
    show_total_num = safe_json(res, 'body', 'total', default=0)
    url = ('https://www.pixiv.net/ajax/user/27915696/following?offset='+str(times)+'&limit=1&rest=hide&tag=&lang=zh_tw')
    res = requests.get(url, headers=headers, timeout=(10, 30))
    hide_total_num = safe_json(res, 'body', 'total', default=0)
    print(show_total_num,hide_total_num)
    threads=[]
    queue=Queue()
    for i in range(0,show_total_num+100,100):
        threads.append(threading.Thread(target =get_follow_illust, args =(i,headers,queue,'show') ))
        #print(i)
    for i in range(0,len(threads)):
        threads[i].start()
        while(threading.activeCount()>=7):
            sleep(0.01)
    for i in range(0,len(threads)):
        threads[i].join()

    threads.clear()
    for i in range(0,hide_total_num+100,100):
        threads.append(threading.Thread(target =get_follow_illust, args =(i,headers,queue,'hide') ))
        #print(i)
    for i in range(0,len(threads)):
        threads[i].start()
        while(threading.activeCount()>=7):
            sleep(0.01)
    for i in range(0,len(threads)):
        threads[i].join()
    while not(queue.empty()):
        pixiv_author_id.append(queue.get())
    return (pixiv_author_id)
def thread_no_use_seleium_get_pid(cookie,Agent,path,num,author_pids):
    #author_pids=str(author_pids)
    pid=[]
    try:
        url='https://www.pixiv.net/ajax/user/'+author_pids+'/profile/all?lang=zh%27'
        headers = {
        'User-Agent': Agent,
        'Cookie':cookie
        ,'referer': 'https://www.pixiv.net/users/'+author_pids,
        }
        res = requests.get(url, headers=headers, timeout=(10, 30))
        resdicts = safe_json(res, 'body', 'illusts', default={})
        for key in resdicts:
            pid.append(key)
    except Exception as err:
        print(err)
        try:
            from safe_io import atomic_append_text
            atomic_append_text(os.path.join(path, f"authorPids_err{int(num)}.txt"), author_pids)
        except Exception:
            try:
                f = open((path+"authorPids_err"+str(num)+".txt"), "a+")
                f.write(author_pids+'\n')
                f.close()
            except Exception:
                pass
    return pid


def Pixiv_Tag(url):                                                 #回傳標籤
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/98.0.4758.80 Safari/537.36 Edg/98.0.1108.50',
        'Cookie':''
        ,'referer': 'https://www.pixiv.net/users/27915696/following',
    }
    id=url.rsplit('/',1)[1]
    res = requests.get(url, headers=headers, timeout=(10, 30))
    obj = str(bs4.BeautifulSoup(res.text, 'lxml').find_all('meta')[25])
    obj=obj.replace('<meta content=\'','')
    obj=obj.replace('id="meta-preload-data" name="preload-data"/>','')
    o=obj.rsplit('\'',1)[0]
    o=o.encode('UTF-8')
    resdicts = str(json.loads(o)['illust'][str(id)]['tags']['tags'])
    #print(resdicts)
    #resdicts = str(json.loads(o)['illust'][str(id)]['tags']['tags'])
    return resdicts
_R18G_GORE_TAGS = (
    '死姦', '脫腸', '斬首', '屍姦', 'necrophilia', '割脖', '砍頭', '食糞', '眼孔姦',
)
_EXCLUDE_TAGS = ('gay', '原創BL')
_DEFAULT_LIKE_THRESHOLD = 300


def _pixiv_info_with_retry(url, Agent, max_attempts=2):
    """Fetch Pixiv info with up to ``max_attempts`` retries.

    Returns the ``(tag, like, pagecount, img_url)`` 4-tuple to keep the legacy
    utility script (``get_download_url``) untouched.  ``Pixiv_info`` itself
    returns an 8-element list including upload/create dates and user info;
    this wrapper deliberately slices it down to the first 4 fields.
    Returns ``None`` if every attempt produced the empty/404 response shape.
    """
    from app.core.pixiv_api import Pixiv_info
    for _ in range(max_attempts):
        info = Pixiv_info(url, Agent=Agent)
        if info == [404]:
            return None
        try:
            tag, like, pagecount, img_url, *_rest = info
        except Exception:
            continue
        if tag != [] or like != 404:
            return tag, like, pagecount, img_url
        if tag == 404 and like == 404:
            return None
    return None


def _is_blocked_r18g_artwork(tag):
    """An R-18G work mixed with any of the gore-marker tags is hard-blocked."""
    tag_str = str(tag)
    if 'R-18G' not in tag_str:
        return False
    return any(marker in tag_str for marker in _R18G_GORE_TAGS)


def _is_excluded_orientation_tag(tag):
    """Hard-exclude based on orientation/genre tags hard-coded in the original script."""
    tag_str = str(tag)
    return any(marker in tag_str for marker in _EXCLUDE_TAGS)


def _build_per_page_urls(img_url, pagecount):
    """Expand a single img_url into one URL per page using the original path scheme."""
    parts = img_url.rsplit(".", 1)
    return [parts[0] + str(i) + "." + parts[1] for i in range(pagecount)]


def get_download_url(path, cookie, Agent, num, pid):
    """回傳下載連結 — utility script entrypoint, not used by the main worker pipeline."""
    url = 'https://www.pixiv.net/artworks/' + pid
    try:
        info = _pixiv_info_with_retry(url, Agent)
    except Exception as err:
        print(pid + '獲取失敗', err)
        return []
    if info is None:
        return []
    tag, like, pagecount, img_url = info
    if _is_blocked_r18g_artwork(tag):
        return []
    if _is_excluded_orientation_tag(tag):
        return pid
    if like < _DEFAULT_LIKE_THRESHOLD:
        return pid
    download_url = _build_per_page_urls(img_url, pagecount)
    time.sleep(random.random() / 5)
    return download_url


def pixiv_following_count(id,cookie,Agent):
    url = ("https://www.pixiv.net/ajax/user/extra?lang=zh_tw") # 访问存有画师所有作品
    print(url)
    headers = {
        'User-Agent': Agent,
        'Cookie':cookie
        ,'referer': 'https://www.pixiv.net/users/'+id+'/following',
    }
    res = requests.get(url,headers=headers, timeout=(10, 30))
    return safe_json(res, 'body', 'following', default=0)

    #objSoup = bs4.BeautifulSoup(res.content, 'lxml')
    #print(objSoup)

def no_use_seleium_get_pid(author_pids,cookie,Agent,q,path,num,exist_pid):
    for i in trange(0,len(author_pids)):
        try:
            url='https://www.pixiv.net/ajax/user/'+author_pids[i]+'/profile/all?lang=zh%27'
            headers = {
            'User-Agent': Agent,
            'Cookie':cookie
            ,'referer': 'https://www.pixiv.net/users/'+author_pids[i],
            }
            res = requests.get(url, headers=headers, timeout=(10, 30))
            resdicts = safe_json(res, 'body', 'illusts', default={})
            for key in resdicts:
                if key not in exist_pid:
                    q.put(key)
        except Exception:
            with open((path+"authorPids_err"+str(num)+".txt"), "a+") as f:
                f.write(author_pids[i]+'\n')
