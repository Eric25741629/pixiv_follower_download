import json
import os
import random
import threading
import time

import bs4
import requests
from bs4 import BeautifulSoup

from app.core.proxy_utils import to_requests_proxies


def make_session(proxy_url: "str | None" = None) -> requests.Session:
    """Create a requests.Session pre-configured with proxy and SSL settings.

    The caller is expected to pass a URL that has been validated by
    ``proxy_utils.parse_proxy_url`` (or ``None`` for direct connection).
    """
    sess = requests.Session()
    proxies = to_requests_proxies(proxy_url)
    if proxies:
        sess.proxies.update(proxies)
    sess.verify = True
    return sess


# 子執行緒的工作函數
import re
from queue import Queue
from time import sleep

try:
    from selenium import webdriver
    from selenium.webdriver.common.by import By
    from selenium.webdriver.common.keys import Keys
    from selenium.webdriver.support import expected_conditions as EC
    from selenium.webdriver.support.ui import WebDriverWait
    _SELENIUM_AVAILABLE = True
    _SELENIUM_IMPORT_ERROR = None
except Exception as _selenium_err:
    webdriver = None
    By = None
    Keys = None
    EC = None
    WebDriverWait = None
    _SELENIUM_AVAILABLE = False
    _SELENIUM_IMPORT_ERROR = _selenium_err
from tqdm import trange


if _SELENIUM_AVAILABLE:
    option = webdriver.ChromeOptions()
else:
    option = None
from pathlib import Path
from app.core.pixiv_thread_utils import safe_json, safe_read_json


def _extract_artwork_body(payload):
    """從 Pixiv ajax 回傳的 payload 中萃取 body dict。

    payload['body'] 可能是 dict、list 或缺失；統一回傳 dict（最差情況為空 dict）。
    """
    if not isinstance(payload, dict):
        return {}
    body = payload.get('body', {})
    if isinstance(body, list):
        body = body[0] if (len(body) > 0 and isinstance(body[0], dict)) else {}
    if not isinstance(body, dict):
        body = {}
    return body


def _ai_type_label(body):
    """aiType==2 時回傳 'AI生成'，否則回傳 None。"""
    ai_type = body.get('aiType', None)
    if ai_type is None:
        return None
    try:
        if int(ai_type) == 2:
            return 'AI生成'
    except (TypeError, ValueError):
        return None
    return None


def _normalize_raw_tags_field(body):
    """把 body['tags'] 統一成 list；可能來源是 list、含 'tags' 子鍵的 dict、或單一值。"""
    raw_tags = body.get('tags', [])
    if isinstance(raw_tags, dict):
        raw_tags = raw_tags.get('tags', [])
    if isinstance(raw_tags, list):
        return raw_tags
    return [raw_tags] if raw_tags else []


def _tag_entry_to_str(entry):
    """把單一 tag entry 轉為字串；entry 可能是 str、dict（多種命名）或其他原值。"""
    if isinstance(entry, str):
        return entry
    if isinstance(entry, dict):
        tag_name = entry.get('tag') or entry.get('name') or entry.get('translated_name')
        if not tag_name and isinstance(entry.get('translation'), dict):
            tag_name = entry['translation'].get('en')
        return str(tag_name) if tag_name else None
    if entry is not None:
        return str(entry)
    return None


_USER_BUCKET_TAG = "users入り"


def _extract_artwork_tags(body):
    """抽取作品標籤清單，並在 aiType==2 時於最前面加上 'AI生成' 標籤。

    過濾掉 Pixiv 自動加上的書籤桶 marker tag（含 ``users入り`` 子字串，例如
    ``5000users入り``）——這類字串不是使用者寫的 tag，是 Pixiv 依書籤數塞進去
    的 metadata。

    回傳前以保序方式 dedup：Pixiv 偶爾會在同一作品的 tag list 中重覆某個 tag
    （例如 `_tag_entry_to_str` 對某些 entry 退而採用 `translation.en` 而與
    另一筆字面 `name` 撞同字串），這裡用 ``dict.fromkeys`` 保留第一次出現的
    順序，重覆者直接丟掉。AI 標籤已先 prepend，所以若 Pixiv tag 中也含
    ``AI生成`` 字串會被去除。
    """
    normalized_tags = []

    ai_label = _ai_type_label(body)
    if ai_label:
        normalized_tags.append(ai_label)

    for entry in _normalize_raw_tags_field(body):
        tag_str = _tag_entry_to_str(entry)
        if tag_str and _USER_BUCKET_TAG not in tag_str:
            normalized_tags.append(tag_str)

    return list(dict.fromkeys(normalized_tags))


def _extract_artwork_pagecount(body, artwork_id):
    """取得 pageCount；本層 body 缺少時退而從 userIllusts[pid] 撈，最終預設為 1。"""
    page_count = body.get('pageCount')
    if page_count is None:
        user_illusts = body.get('userIllusts', {})
        if isinstance(user_illusts, dict):
            illust_info = user_illusts.get(str(artwork_id)) or user_illusts.get(artwork_id) or {}
            if isinstance(illust_info, dict):
                page_count = illust_info.get('pageCount')
    try:
        return int(page_count or 1)
    except (TypeError, ValueError):
        return 1


def _extract_artwork_upload_date(body):
    """讀 body['uploadDate']（Pixiv 真正上傳時間，ISO 8601 含時區）。缺失或空字串時回 None。"""
    val = body.get('uploadDate')
    if not val:
        return None
    return str(val)


def _extract_artwork_create_date(body):
    """讀 body['createDate']（Pixiv 作品建立時間，ISO 8601 含時區）。缺失或空字串時回 None。"""
    val = body.get('createDate')
    if not val:
        return None
    return str(val)


def _extract_artwork_user_id(body):
    """讀 body['userId']（畫師 Pixiv ID 字串）。缺失或空字串時回 None。"""
    val = body.get('userId')
    if val in (None, ''):
        return None
    return str(val)


def _extract_artwork_user_name(body):
    """讀 body['userName']（畫師顯示名稱）。缺失或空字串時回 None。"""
    val = body.get('userName')
    if val in (None, ''):
        return None
    return str(val)


def _extract_artwork_img_url(body):
    """從 body['urls'] 取出原圖 URL；對 multi-page / ugoira 路徑做 p0→p 與 ugoira0→ugoira 修正。"""
    try:
        urls_obj = body.get('urls', {})
        if isinstance(urls_obj, dict):
            original_url = urls_obj.get('original') or urls_obj.get('regular')
        else:
            original_url = None
        if not original_url:
            return None
        return str(original_url).replace("p0", "p", 1).replace("ugoira0", "ugoira", 1)
    except Exception:
        return None


def _append_pixiv_info_history(trace_path, pid, trace_entry):
    try:
        history = safe_read_json(trace_path, {})
        if not isinstance(history, dict):
            history = {}
        record = history.get(str(pid))
        if not isinstance(record, dict):
            record = {}
        hist = record.get('history')
        if not isinstance(hist, list):
            hist = []
        hist.append(trace_entry)
        record['history'] = hist[-50:]  # 保留最近 50 次，避免檔案無限長大
        record['latest'] = trace_entry
        record['requires_cookie'] = trace_entry.get('requires_cookie')
        record['artwork_url'] = trace_entry.get('artwork_url')
        record['ajax_url'] = trace_entry.get('ajax_url')
        record['status_no_cookie'] = trace_entry.get('status_no_cookie')
        record['status_cookie'] = trace_entry.get('status_cookie')
        record['result_preview'] = trace_entry.get('result_preview')
        record['checked_at'] = trace_entry.get('checked_at')
        history[str(pid)] = record
        try:
            from safe_io import atomic_write_json
            atomic_write_json(trace_path, history, backup=True)
        except Exception:
            with open(trace_path, 'w', encoding='utf-8') as f:
                json.dump(history, f, indent=2, ensure_ascii=False)
    except Exception:
        pass


def _clean_request_text(value):
    try:
        text = str(value).replace('\ufeff', '').replace('\r', ' ').replace('\n', ' ').strip()
        return text.encode('latin-1', 'ignore').decode('latin-1').strip()
    except Exception:
        try:
            return str(value or '').strip()
        except Exception:
            return ''


def _clean_headers(headers):
    try:
        return {str(k): _clean_request_text(v) for k, v in dict(headers).items()}
    except Exception:
        return headers


def _normalize_artwork_id(raw_value):
    try:
        text = _clean_request_text(raw_value)
    except Exception:
        text = str(raw_value or "").strip()
    if not text:
        return ""
    token = text.rsplit("/", 1)[-1]
    token = token.split("?", 1)[0].split("#", 1)[0].strip()
    m = re.match(r"^(\d+)", token)
    if m:
        return m.group(1)
    return token

# 防止打印一些无用的日志
#option.add_experimental_option("excludeSwitches", ['enable-automation', 'enable-logging'])
#options = Options()
#https://www.pixiv.net/ajax/user/490219/profile/illustswork_category=illustManga&is_first_page=0&lang=zh_tw
def _require_selenium():
    if not _SELENIUM_AVAILABLE:
        raise RuntimeError(f"selenium is required for this action: {_SELENIUM_IMPORT_ERROR}")


def logging(address,password):
    _require_selenium()
    url = 'https://pixiv.net/'
    driver = webdriver.Chrome(options=option)
    try:
        driver.get(url)
        driver.find_element(By.XPATH,'//*[@id="wrapper"]/div[3]/div[2]/a[2]').click()
        driver.find_element(By.XPATH,"//input[@autocomplete = 'username']").send_keys(address)
        passwd=driver.find_element(By.XPATH,"//input[@autocomplete = 'current-password']")
        passwd.send_keys(password)
        passwd.send_keys(Keys.RETURN)
    finally:
        driver.quit()

#about_cookies
def auto_get_cookie(address,password,mode=0):
    _require_selenium()
    print(f"[pixiv_api] auto_get_cookie called with mode={mode}")
    def facebook_login(driver,email,password):
        print("[pixiv_api] google_login: trying CSS selector button.btn-item.btn-gplus")
        try:
            btn = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "button.btn-item.btn-gplus"))
            )
            btn.click()
        except Exception as e:
            print(f"[pixiv_api] google_login: primary selector failed: {e}")
        # 等待 email 輸入欄位出現，若沒有則嘗試其他頁面上的按鈕作為 fallback
        try:
            WebDriverWait(driver, 8).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='email'], #identifierId, input[autocomplete='username']"))
            )
        except Exception as e:
            print(f"[pixiv_api] google_login: no email input after click: {e}")
            try:
                buttons = driver.find_elements(By.TAG_NAME, "button")
                for b in buttons:
                    dl = (b.get_attribute('data-label') or '').lower()
                    txt = (b.text or '').lower()
                    cls = (b.get_attribute('class') or '').lower()
                    if 'google' in dl or 'google' in txt or 'gplus' in cls:
                        try:
                            print('[pixiv_api] google_login: trying alternative button with', dl, txt, cls)
                            b.click()
                            WebDriverWait(driver, 6).until(
                                EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='email'], #identifierId, input[autocomplete='username']"))
                            )
                            break
                        except Exception as e2:
                            print('[pixiv_api] google_login: alternative click failed', e2)
            except Exception as e3:
                print('[pixiv_api] google_login: failed enumerating buttons', e3)
        # 填寫帳號密碼（等待 email 欄位存在）
        try:
            WebDriverWait(driver, 8).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "input[type='email'], #identifierId, input[autocomplete='username']"))
            ).send_keys(email)
            passwd = driver.find_element(By.XPATH, "//input[@autocomplete = 'current-password']")
            passwd.send_keys(password)
            passwd.send_keys(Keys.RETURN)
        except Exception as e:
            print(f"[pixiv_api] google_login: failed to fill login form: {e}")
        try:
            driver.find_element(By.XPATH, '//*[@class="x1lliihq x6ikm8r x10wlt62 x1n2onr6 xlyipyv xuxw1ft x1j85h84"]').click()
        except Exception:
            pass
    def google_login(driver,email, password):
        print("[pixiv_api] google_login: trying CSS selector button.btn-item.btn-gplus")
        try:
            btn = WebDriverWait(driver, 10).until(
                EC.element_to_be_clickable((By.CSS_SELECTOR, "button.btn-item.btn-gplus"))
            )
            btn.click()
        except Exception as e:
            print(f"[pixiv_api] google_login: primary selector failed: {e}")
            try:
                buttons = driver.find_elements(By.TAG_NAME, "button")
                infos = []
                for b in buttons:
                    infos.append({
                        'text': b.text[:30],
                        'class': b.get_attribute('class'),
                        'data-label': b.get_attribute('data-label')
                    })
                print('[pixiv_api] google_login: found buttons:', infos)
            except Exception:
                pass
        WebDriverWait(driver, 10).until(
            EC.presence_of_element_located((By.XPATH, "//input[@autocomplete='username']"))
        ).send_keys(email)
        passwd = driver.find_element(By.XPATH, "//input[@autocomplete = 'current-password']")
        passwd.send_keys(password)
        passwd.send_keys(Keys.RETURN)
    def pixiv_login(driver,email,password):
        driver.find_element(By.XPATH,'//*[@id="wrapper"]/div[3]/div[2]/a[2]').click()
        driver.find_element(By.XPATH,"//input[@autocomplete = 'username']").send_keys(email)
        passwd=driver.find_element(By.XPATH,"//input[@autocomplete = 'current-password']")
        passwd.send_keys(password)
        passwd.send_keys(Keys.RETURN)
    def get_cookies():
        cookies = ""
        selenium_cookies = driver.get_cookies()
        for cookie in selenium_cookies:
            cookies+=str(cookie['name'])
            cookies+="="
            cookies+=str(cookie['value'])
            cookies+=";"
        return cookies
    option = webdriver.ChromeOptions()
    option.add_experimental_option("excludeSwitches", ['enable-automation', 'enable-logging'])
    #option.add_argument("--headless")
    option.add_argument("--disable-backgrounding-occluded-windows")
    driver = webdriver.Chrome(options=option)
    try:
        url = 'https://pixiv.net/'
        driver.get(url)
        print(f"[pixiv_api] selected login mode: {mode}")
        if (mode == 0):
            pixiv_login(driver, address, password)
        elif (mode == 1):
            # UI: mode 1 = Google
            google_login(driver, address, password)
        elif (mode == 2):
            # UI: mode 2 = Facebook
            facebook_login(driver, address, password)
        sleep(2)
        url = 'https://pixiv.net/'
        driver.get(url)
        sleep(5)
        soup = bs4.BeautifulSoup(driver.page_source, 'lxml')
        user_num=(str(soup.head).split('user_id')[1].split('_gaq.push')[0].split('"')[1])
        url='https://www.pixiv.net/artworks/96509143'
        driver.get(url)
        sleep(5)
        agent=driver.execute_script("return navigator.userAgent")
        cookies=get_cookies()
        return str(user_num),str(cookies),str(agent)
    finally:
        driver.quit()

def Test_cookies(lists,agent):
    cookies=[]
    i=0
    for list1 in lists:
        try:
            pid='96509143'
            headers = {
                'User-Agent': agent,
                'Cookie':list1
                ,'Referer':('http://www.pixiv.net/'+str(pid)),        
                    } 
            url='https://www.pixiv.net/ajax/illust/'+pid+'/pages?lang=zh_tw'            
            htmlfile = requests.get(url,headers=headers,timeout=(10, 30))
            #print(htmlfile.text)
            htmlfile.raise_for_status() 
            #objSoup = bs4.BeautifulSoup(htmlfile.text, 'lxml')
            #print(objSoup.text)
            i=i+1
            cookies.append(list1) 
        except Exception as err:
            print(err)
            pass
    return i,cookies

def get_author_picture_ids(illust_ids,path,num,q,exist_pid):
    _require_selenium()
    download_Pid=[]
    temp = 0
    total = len(illust_ids)     
    for illust_id in illust_ids:
        try:
            temp += 1
            print('\r' + '[線程%s]:[%s%s]%.2f%%' % (num,'█' * int(temp*20/total), ' ' * (20-int(temp*20/total)),float(temp/total*100)), end='')
            driver = webdriver.Chrome(options=option)
            try:
                time.sleep(5)
                url = ('https://pixiv.net/ajax/user/' + illust_id + '/profile/all?lang=zh')				#畫師id 輸入後可得到畫師所有的作品
                driver.switch_to.window(driver.window_handles[num])
                driver.get(url)
                #time.sleep(1)
                soup = BeautifulSoup(driver.page_source, 'html.parser')
                res=soup.find('pre')
                res=str(res)
                res=res.replace('<pre style="word-wrap: break-word; white-space: pre-wrap;">','')	#清除後才能夠轉為json
                res=res.replace('</pre>','')														#清除後才能夠轉為json
                #print(res)
                res=res.encode('UTF-8')
                resdict = json.loads(res)['body']['illusts']		  								# 將json轉化為python的字典后提取元素
                Pids=[key for key in resdict]                        #將元素放入陣列裡
                for Pid in Pids:
                    if Pid in exist_pid:
                        break
                    else :
                        download_Pid.append(Pid)
            finally:
                driver.quit()
        except Exception as err:
            print(Pid+'獲取失敗',err)
            try:
                from safe_io import atomic_append_text
                atomic_append_text(os.path.join(path, f"get_download_author_err{int(num)}.txt"), illust_id)
            except Exception:
                try:
                    f = open((path+"get_download_author_err"+str(num)+".txt"), "a")
                    f.write(illust_id+'\n')
                    f.close()
                except Exception:
                    pass
        time.sleep(random.random())
        #print(num)
    q.put(download_Pid)
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

def random_Agent():
    # Updated modern User-Agent list (desktop and mobile, common browsers)
    USER_AGENTS = [
        # Chrome (Windows)
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.5790.170 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.5845.179 Safari/537.36",
        # Edge (Chromium)
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.1938.81 Safari/537.36 Edg/116.0.1938.81",
        # Firefox
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:118.0) Gecko/20100101 Firefox/118.0",
        # macOS Safari
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.5 Safari/605.1.15",
        # Chrome on macOS
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.5845.179 Safari/537.36",
        # iPhone (Safari)
        "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/605.1.15",
        # iPad (Safari)
        "Mozilla/5.0 (iPad; CPU OS 17_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 Mobile/15E148 Safari/605.1.15",
        # Android Chrome (Pixel)
        "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/116.0.5845.179 Mobile Safari/537.36",
        # Samsung Internet
        "Mozilla/5.0 (Linux; Android 13; SAMSUNG SM-S916B) AppleWebKit/537.36 (KHTML, like Gecko) SamsungBrowser/21.0 Chrome/115.0.5790.170 Mobile Safari/537.36",
    ]
    return random.choice(USER_AGENTS)
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
def _result_preview(final_result):
    """Build the small dict logged into pixiv_cookie_requirement.json under ``result_preview``."""
    if not isinstance(final_result, list):
        return {"tags_len": 0, "bookmarkCount": 0, "pageCount": 0, "img_url": None}
    n = len(final_result)
    tags_len = (
        len(final_result[0])
        if n >= 1 and isinstance(final_result[0], list)
        else 0
    )
    return {
        "tags_len": tags_len,
        "bookmarkCount": final_result[1] if n >= 2 else 0,
        "pageCount": final_result[2] if n >= 3 else 0,
        "img_url": final_result[3] if n >= 4 else None,
    }


def _record_pixiv_info_trace(pid_id, ajax_url, requires_cookie,
                              status_no_cookie, status_cookie, final_result):
    """Append a Pixiv_info call to pixiv_cookie_requirement.json (best-effort)."""
    try:
        trace_entry = {
            'artwork_url': 'https://www.pixiv.net/artworks/' + pid_id,
            'pid': str(pid_id),
            'ajax_url': ajax_url,
            'requires_cookie': requires_cookie,
            'status_no_cookie': status_no_cookie,
            'status_cookie': status_cookie,
            'result_preview': _result_preview(final_result),
            'checked_at': time.strftime('%Y-%m-%d %H:%M:%S', time.localtime()),
        }
        trace_path = os.path.join(
            os.getenv('APPDATA') + r'/pixiv_download/',
            'pixiv_cookie_requirement.json',
        )
        os.makedirs(os.path.dirname(trace_path), exist_ok=True)
        _append_pixiv_info_history(trace_path, pid_id, {**trace_entry, 'source': 'fetch'})
    except Exception:
        pass


def _decide_pixiv_info_result(no_cookie_result, no_cookie_valid, cookie, fetch_with_cookie):
    """Decide which fetch result to return based on the no-cookie outcome.

    Returns ``(final_result, requires_cookie, status_cookie)``. ``status_cookie``
    is ``None`` when no cookie fetch was attempted.
    """
    if no_cookie_result == [404]:
        return [404], None, None
    if no_cookie_valid:
        return no_cookie_result, False, None
    if not cookie:
        return no_cookie_result, None, None
    cookie_result, cookie_valid, status_cookie = fetch_with_cookie()
    if cookie_valid:
        return cookie_result, True, status_cookie
    final = cookie_result if cookie_result != [404] else no_cookie_result
    return final, False, status_cookie


def Pixiv_info(url,
    Agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/98.0.4758.80 Safari/537.36 Edg/98.0.1108.50'
    ,cookie=None,ip=None, *, session: "requests.Session | None" = None):                                                #回傳標籤
        url = _clean_request_text(url)
        Agent = _clean_request_text(Agent)
        cookie = _clean_request_text(cookie) if cookie is not None else None
        id = _normalize_artwork_id(url)
        if not str(id).isdigit():
            return [404]

        ajax_url='https://www.pixiv.net/ajax/illust/'+id

        def _parse_payload(payload):
            body = _extract_artwork_body(payload)
            try:
                bookmark_count = int(body.get('bookmarkCount', 0) or 0)
            except (TypeError, ValueError):
                bookmark_count = 0
            page_count = _extract_artwork_pagecount(body, id)
            normalized_tags = _extract_artwork_tags(body)
            img_url = _extract_artwork_img_url(body)
            upload_date = _extract_artwork_upload_date(body)
            create_date = _extract_artwork_create_date(body)
            user_id = _extract_artwork_user_id(body)
            user_name = _extract_artwork_user_name(body)
            result = [
                list(normalized_tags), int(bookmark_count), int(page_count), str(img_url),
                upload_date, create_date, user_id, user_name,
            ]
            valid = bool(img_url) and str(img_url) != 'None'
            return result, valid

        def _fetch(use_cookie=False, retry=0):
            headers = {
                'User-Agent': Agent,
                'referer': 'https://www.pixiv.net/artworks/'+id,
            }
            if use_cookie and cookie:
                headers['Cookie'] = cookie
            headers = _clean_headers(headers)
            try:
                res = (session or requests).get(ajax_url, headers=headers, timeout=20)
            except (requests.exceptions.ProxyError,
                    requests.exceptions.ConnectTimeout,
                    requests.exceptions.ConnectionError):
                # Propagate so the scheduler-aware caller can disable the cookie/proxy.
                raise
            except Exception as e:
                print(f"Pixiv_info request error pid={id}: {e}")
                return [404], False, -1
            if res.status_code == 404:
                return [404], False, 404
            if res.status_code == 429 and retry < 1:
                print(429)
                time.sleep(60)
                return _fetch(use_cookie=use_cookie, retry=retry+1)
            try:
                payload = res.json()
            except Exception as e:
                print(f"Pixiv_info json error pid={id}: {e}")
                print(f"Pixiv_info response content: {res.text[:500]}")
                print(f"Pixiv_info status code: {res.status_code}")
                return [404], False, res.status_code
            parsed, valid = _parse_payload(payload)
            return parsed, valid, res.status_code

        no_cookie_result, no_cookie_valid, status_no_cookie = _fetch(use_cookie=False)
        final_result, requires_cookie, status_cookie = _decide_pixiv_info_result(
            no_cookie_result, no_cookie_valid, cookie,
            lambda: _fetch(use_cookie=True),
        )
        _record_pixiv_info_trace(
            id, ajax_url, requires_cookie,
            status_no_cookie, status_cookie, final_result,
        )
        return final_result

def get_pixiv_cookie_requirement(pid):
    """回傳指定 PID 最近一次是否需要 cookie，找不到時回傳 None。"""
    try:
        trace_path = os.path.join(os.getenv('APPDATA')+r'/pixiv_download/', 'pixiv_cookie_requirement.json')
        data = safe_read_json(trace_path, None)
        if not isinstance(data, dict):
            return None
        pid_key = _normalize_artwork_id(pid)
        entry = data.get(str(pid_key))
        if entry is None and str(pid_key) != str(pid):
            entry = data.get(str(pid))
        if isinstance(entry, dict):
            return entry.get('requires_cookie')
    except Exception:
        return None
    return None
    
def userId(url,
    Agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/98.0.4758.80 Safari/537.36 Edg/98.0.1108.50'):                                                 #回傳標籤
    # try:
        url = _clean_request_text(url)
        Agent = _clean_request_text(Agent)
        headers = {
            'User-Agent': Agent,
            'referer': 'https://www.pixiv.net/',        
        }
        headers = _clean_headers(headers)
        id=url.rsplit('/',1)[1]
        res = requests.get(url, headers=headers, timeout=(10, 30))
        if res.status_code == 404:
            return 404,404,404,404
        #print(res.json())
        obj = str(bs4.BeautifulSoup(res.text, 'lxml').select_one('meta[name="preload-data"]'))
        obj=obj.replace('<meta content=\'','')
        obj=obj.replace('id="meta-preload-data" name="preload-data"/>','') 
        o=obj.rsplit('\'',1)[0] 
        #print(o)
        o = o.encode('UTF-8')
        data = json.loads(o)
        userId = data['illust'].get(id)
        return userId['userId']
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

