import os
import time
import io
import hashlib
import signal
import requests
from PIL import Image
from selenium import webdriver
import pandas as pd
import caffeine
import json
import argparse
import numpy as np
from webdriver_manager.chrome import ChromeDriverManager

pd.set_option('display.max_columns', 100)
pd.set_option('display.max_colwidth', None)


"""
    Credit:
    https://github.com/Ladvien/deep_arcane/blob/main/1_get_images/scrap.py
    
    Note: 
    Requires chromedriver installed in PATH variable

"""

class timeout:
    def __init__(self, seconds=1, error_message="Timeout"):
        self.seconds = seconds
        self.error_message = error_message

    def handle_timeout(self, signum, frame):
        raise TimeoutError(self.error_message)

    def __enter__(self):
        signal.signal(signal.SIGALRM, self.handle_timeout)
        signal.alarm(self.seconds)

    def __exit__(self, type, value, traceback):
        signal.alarm(0)


def fetch_image_urls(query: str, max_links_to_fetch: int, wd: webdriver, existing_urls: list, sleep_between_interactions: float = 0.1):

    def scroll_to_end(wd):
        wd.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        time.sleep(sleep_between_interactions)

    # build the google query
    search_url = "https://www.google.com/search?safe=off&site=&tbm=isch&source=hp&q={q}&oq={q}&gs_l=img"

    # load the page
    wd.get(search_url.format(q=query))

    image_urls = set()
    image_count = 0
    results_start = 0
    start = time.time()
    while image_count < max_links_to_fetch:


        scroll_to_end(wd)

        # get all image thumbnail results
        thumbnail_results = wd.find_elements_by_css_selector("img.Q4LuWd")
        number_results = len(thumbnail_results)

        print(
            f"Found: {number_results} search results. Extracting links from {results_start}:{number_results}"
        )

        for img in thumbnail_results[results_start:number_results]:
            # try to click every thumbnail such that we can get the real image behind it
            try:
                img.click()
                time.sleep(sleep_between_interactions)
            except Exception:
                continue

            # extract image urls
            actual_images = wd.find_elements_by_css_selector("img.n3VNCb")
            for actual_image in actual_images:
                if actual_image.get_attribute(
                    "src"
                ) and "http" in actual_image.get_attribute("src") and \
                        actual_image.get_attribute("src") not in existing_urls:
                    image_urls.add(actual_image.get_attribute("src"))

            image_count = len(image_urls)

            if len(image_urls) >= max_links_to_fetch:
                print(f"Found: {len(image_urls)} image links, done!")
                break
        else:
            print("Found:", len(image_urls), "image links, looking for more ...")
            time.sleep(20)

            if (time.time() - start) / 60 > 5:  # if still searching for >5 min, break and return whatever have
                break

            not_what_you_want_button = ""
            try:
                not_what_you_want_button = wd.find_element_by_css_selector(".r0zKGf")
            except:
                pass

            # If there are no more images return.
            if not_what_you_want_button:
                print("No more images available.")
                return image_urls

            load_more_button = wd.find_element_by_css_selector(".mye4qd")
            if load_more_button and not not_what_you_want_button:
                wd.execute_script("document.querySelector('.mye4qd').click();")

        # move the result startpoint further down
        results_start = len(thumbnail_results)

    return image_urls

def search_and_download(wd, query: str, rootOutput: str, output_path: str, number_images: int =5):

    # Open JSON of image source URLs, if exists already, otherwise initialize
    if os.path.exists('./results/image_sources.json'):

        with open('./results/image_sources.json', 'rb') as j:
            existing_urls = json.load(j)

    else:
        existing_urls = {}

    res = fetch_image_urls(
        query,
        number_images,
        wd=wd,
        existing_urls=list(set(existing_urls.values()))
    )

    if res is not None:

        for url in res:
            try:
                print("Getting image")
                with timeout(2):
                    image_content = requests.get(url, verify=False).content

            except Exception as e:
                print(f"ERROR - Could not download {url} - {e}")

            try:
                image_file = io.BytesIO(image_content)
                image = Image.open(image_file).convert("RGB")
                file_path = os.path.join(rootOutput, output_path, hashlib.sha1(image_content).hexdigest()[:10] + ".jpg")
                with open(file_path, "wb") as f:
                    image.save(f, "JPEG", quality=85)
                print(f"SUCCESS - saved {url} - as {file_path}")

                existing_urls[output_path] = url  # only relative path to image

            except Exception as e:
                print(f"ERROR - Could not save {url} - {e}")

            with open(
                    './results/image_sources.json',
                    'w') as j:
                json.dump(existing_urls, j)

    else:
        print(f"Failed to return links for term: {query}")

def parse_opt():
    parser = argparse.ArgumentParser()
    parser.add_argument('output-path', type=str, help='path to output scraped images')
    parser.add_argument('num-images', type=str, default=100, help='number of images per detailed make-model class to scrape')
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument('--top', action='store_true', help='sort df ascending, begin with vehicle makes a -> z')
    group.add_argument('--bottom', action='store_true', help='sort df descending, begin with vehicle makes z -> a')
    return parser.parse_args()

def main(opt):

    # Read in database of makes and models to scrape
    df = pd.read_csv('./data/make_model_database_mod.csv')

    # Remove vehicle make-model-year rows if dir already exists on disk (in case successfully ran previously)
    lst = []
    for subdir, dirs, files in os.walk(opt.output_path):
        for file in [i for i in files if 'jpg' in i or 'png' in i]:
            lst.append('/'.join(os.path.join(subdir, file).split('/')[-4:]))  # does not count empty subdirectories
    foo = pd.DataFrame(lst, columns=["Path"])
    foo['Make'] = foo['Path'].apply(lambda x: x.split('/')[0])
    foo['Model'] = foo['Path'].apply(lambda x: x.split('/')[1])
    foo['Year'] = foo['Path'].apply(lambda x: x.split('/')[2]).astype(int)
    foo['dir'] = foo['Path'].apply(lambda x: '/'.join(x.split('/')[:-1]))

    # Fixes to account for Chevrolet C/K and RAM C/V
    # Note - this was run on a MacBook. macOS behavior in Python changes '/' in strings is to ':'
    foo.loc[(foo.Make == 'Chevrolet') & (foo.Model == 'C:K'), 'Model'] = 'C/K'
    foo['dir'] = np.where((foo['Make'] == 'Chevrolet') & (foo['Model'] == 'C/K'), 'Chevrolet/C\/K/' + foo['Year'].astype(str), foo['dir'])
    foo['Path'] = np.where((foo['Make'] == 'Chevrolet') & (foo['Model'] == 'C/K'), foo['dir'] + '/' + foo['Path'].apply(lambda x: x.split('/')[-1]), foo['Path'])

    foo.loc[(foo.Make == 'RAM') & (foo.Model == 'C:V'), 'Model'] = 'C/V'
    foo['dir'] = np.where((foo['Make'] == 'RAM') & (foo['Model'] == 'C/V'), 'RAM/C\/V/' + foo['Year'].astype(str), foo['dir'])
    foo['Path'] = np.where((foo['Make'] == 'RAM') & (foo['Model'] == 'C/V'), foo['dir'] + '/' + foo['Path'].apply(lambda x: x.split('/')[-1]), foo['Path'])

    foo['count'] = foo.groupby(['Make', 'Model', 'Year'])['Path'].transform('count')
    complete = foo.loc[foo['count'] >= opt.num_images][['Make', 'Model', 'Year']].drop_duplicates().reset_index(drop=True)

    # Remove make-model-year combinations where image count sufficient
    df = df.merge(complete, on=['Make', 'Model', 'Year'], how='outer', indicator=True)
    df = df.loc[df._merge != 'both'].reset_index(drop=True)
    del df['_merge']

    if opt.top:
        df = df.sort_values(by=['Make', 'Model', 'Year'], ascending=True)
    else:
        df = df.sort_values(by=['Make', 'Model', 'Year'], ascending=False)

    wd = webdriver.Chrome(ChromeDriverManager().install())
    wd.get("https://google.com")

    for i in range(len(df)):
        query = df.iloc[i, 0] + ' ' + df.iloc[i, 1] + ' ' + df.iloc[i, 3] + ' ' + str(df.iloc[i, 4])

        # Ensuring directory structure right
        if df.iloc[i, 2] == 'C/K':
            fix_model = 'C:K'
        elif df.iloc[i, 2] == 'C/V':
            fix_model = 'C:V'
        else:
            fix_model = df.iloc[i, 2]

        output_path = os.path.join(df.iloc[i, 0], fix_model, str(df.iloc[i, 4]))
        os.makedirs(os.path.join(opt.output_path, output_path), exist_ok=True)

        search_and_download(wd, query, opt.output_path, output_path, number_images=opt.num_images)

    caffeine.off()

if __name__ == '__main__':

    opt = parse_opt()
    main(opt)