
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
import bs4
url='https://twitter.com/H8eWu1HNJ5MfSRv/likes'
cookies='personalization_id="v1_NfOlXe7lbETn3pCFDImDRA=="; guest_id=v1%3A160640246900346225; dnt=1; ads_prefs="HBESAAA="; remember_checked_on=1; auth_token=06c5d835e4aee4efd4af11526754f13aedac96d3; ct0=c510b60e3b6d50c49f3442925f8689f0200aa70c225afe81a863c438341ef8cca04cdd5927250959407b052416bffec22150fdbc3a1e10fc8ec74450811480401dc4d852003e19020ebe4d183efb9d41; twid=u%3D1140474248919736320; guest_id_marketing=v1%3A160640246900346225; guest_id_ads=v1%3A160640246900346225; at_check=true; des_opt_in=Y; mbox=PC#d6636acb81744a69853733d0037864fd.32_0#1714933429|session#f72749c9dd1948c99304ef4c63a2bee8#1651690489; _twitter_sess=BAh7CSIKZmxhc2hJQzonQWN0aW9uQ29udHJvbGxlcjo6Rmxhc2g6OkZsYXNo%250ASGFzaHsABjoKQHVzZWR7ADoPY3JlYXRlZF9hdGwrCAFESqGBAToMY3NyZl9p%250AZCIlZDJhOTM5MWRkNTA2ZWQwNGRkNTA5MDg4YjhmOWM1NzY6B2lkIiU5ZGNj%250AY2ZlOTkxNWYyMGQzODBiYmFkYjNmMzQ0ZDdiMg%253D%253D--32b66dca7d004158b524310806f1ab3571fd6725; external_referer=padhuUp37zjgzgv1mFWxJ12Ozwit7owX|0|8e8t2xd8A2w%3D'
res = requests.get(url,cookies=cookies)
#res=bs4.BeautifulSoup(res.text, 'lxml')
print(res.text)