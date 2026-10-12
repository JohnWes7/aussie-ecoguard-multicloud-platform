"""Download the test videos listed in videos.csv from Wikimedia Commons into videos/.

Usage: python experiments/test-media/download_videos.py
The videos are not committed (70 MB, CC BY / CC BY-SA); files that exist are skipped.
"""

import csv
import json
import os
import time
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "videos")
# Wikimedia asks API clients for a descriptive User-Agent with a contact URL.
UA = "fit3184-a2-test-media/1.0 (https://git.infotech.monash.edu/fit3184-a2/fit3184-a2)"


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    return urllib.request.urlopen(req, timeout=120).read()


def main():
    os.makedirs(OUT, exist_ok=True)
    rows = list(csv.DictReader(open(os.path.join(HERE, "videos.csv"), encoding="utf-8")))
    for row in rows:
        path = os.path.join(OUT, row["file"])
        if os.path.exists(path):
            continue
        params = {"action": "query", "format": "json", "titles": "File:" + row["commons_title"],
                  "prop": "videoinfo", "viprop": "url"}
        info = json.loads(fetch("https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode(params)))
        page = next(iter(info["query"]["pages"].values()))
        with open(path, "wb") as fh:
            fh.write(fetch(page["videoinfo"][0]["url"]))  # the original upload, not a transcode
        print("downloaded", row["file"])
        time.sleep(1)  # stay well under Wikimedia's rate limits
    print(f"{len(rows)} videos in {OUT}")


if __name__ == "__main__":
    main()
