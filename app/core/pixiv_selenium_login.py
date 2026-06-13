"""Selenium-dependent Pixiv login / cookie-grab helpers (file-size refactor).

Split out of ``pixiv_api.py`` so the heavy optional ``selenium`` import block
and the browser-driving login flows live in one place. ``pixiv_api`` re-imports
everything here at the bottom of the module (``from
app.core.pixiv_selenium_login import *`` plus the underscore names) so the
``from pixiv_api import *`` star surface and ``pixiv_api.NAME`` attribute lookups
are byte-identical. This module imports nothing from ``pixiv_api`` so there is
no import cycle.

selenium is OPTIONAL: when it can't be imported the module still loads with the
names bound to ``None`` and ``_require_selenium`` raises a clear error only when
a selenium-backed entrypoint is actually called.
"""
import json
import os
import random
import time
from time import sleep

import bs4
from bs4 import BeautifulSoup

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


if _SELENIUM_AVAILABLE:
    option = webdriver.ChromeOptions()
else:
    option = None


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
