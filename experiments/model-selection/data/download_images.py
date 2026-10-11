"""Download the test images listed in sources.csv from Wikimedia Commons.

Usage: python data/download_images.py

The images are not committed to the repo (11 MB, mostly CC BY-SA). Each one is
fetched as Commons' 1280 px wide rendition (or the original if it is smaller),
the same files the experiment was run on, and saved to data/images/<file>.
Files that already exist are skipped.
"""

import csv
import json
import os
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "images")
# Wikimedia asks API clients for a descriptive User-Agent with a contact URL.
UA = "fit3184-a2-model-selection/1.0 (https://git.infotech.monash.edu/fit3184-a2/fit3184-a2)"


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    return urllib.request.urlopen(req, timeout=60).read()


def main():
    os.makedirs(OUT, exist_ok=True)
    rows = list(csv.DictReader(open(os.path.join(HERE, "sources.csv"), encoding="utf-8")))
    for row in rows:
        path = os.path.join(OUT, row["file"])
        if os.path.exists(path):
            continue
        params = {"action": "query", "format": "json", "titles": "File:" + row["commons_title"],
                  "prop": "imageinfo", "iiprop": "url", "iiurlwidth": 1280}
        info = json.loads(fetch("https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode(params)))
        page = next(iter(info["query"]["pages"].values()))
        image = page["imageinfo"][0]
        with open(path, "wb") as fh:
            fh.write(fetch(image.get("thumburl") or image["url"]))
        print("downloaded", row["file"])
        time.sleep(1)  # stay well under Wikimedia's rate limits
    print(f"{len(rows)} images in {OUT}")


if __name__ == "__main__":
    main()
