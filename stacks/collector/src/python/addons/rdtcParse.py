# External libraries import statements
import time
import logging
from urllib.parse import urlencode, parse_qs, urlparse


# This application's import statements
try:
    # These are for when running in an EC2
    from exceptions import *
    import superGlblVars as GLOBALS

except ModuleNotFoundError as err:
    # These are for when running in a Lambda
    print(f"Loading module for lambda execution: {__name__}")
    from src.python.exceptions import *
    from src.python import superGlblVars as GLOBALS


logger = logging.getLogger()


def getPlaylist(ap):
    baseUrl = ap["accessUrl"]
    
    if not GLOBALS.useTestData:
        # First make a get request to set the PHPSESSID cookie in our session
        GLOBALS.netUtils.get(baseUrl)

    camId = ap["deviceID"]
    headers = {
        "Host": "www.city-n.ru",
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
        "X-Requested-With": "XMLHttpRequest",
        "Origin": "https://www.city-n.ru",
        "DNT": "1",
        "Sec-GPC": "1",
        "Connection": "keep-alive",
        "Referer": "https://www.city-n.ru/road_cam.html",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "Priority": "u=0"
    }

    # Must encode the payload here because the cam["source"] is a URL,
    # the server will return an empty response if it's not encoded
    payload = urlencode({
        "act": "token",
        "source": f"https://ipcam.rdtc.ru/ipcam/ipcam_{camId}/embed.html"
    })

    if GLOBALS.useTestData:
        token = "9d4663e6f4608938ca742fbddcdc0f0d0ee1bd1a-1041a6f75e1566ff5047efe71521f4a5-1775060509-1775049709"
    else:
        try:
            response = GLOBALS.netUtils.post(inUrl="https://www.city-n.ru/engine/module/road_cam_backend.php",
                                             verify=True, headers=headers, data=payload, timeout=40)
            responseUrl = response.text.strip()
            parsedUrl = urlparse(responseUrl)
            params = parse_qs(parsedUrl.query)
            token = params.get("token", [None])[0]
            if not token:
                raise HPatrolError("Token not found in response to POST request")

        except Exception as e:
            logger.error(f"Error getting token for camId {camId}: {e}")

    ap["headers"]["Referer"] = f"https://ipcam.rdtc.ru/ipcam/ipcam_{camId}/embed.html?dvr=false&token={token}"
    returnUrl = f"https://ipcam.rdtc.ru/ipcam/ipcam_{camId}/tracks-v1/index.fmp4.m3u8?token={token}"

    return returnUrl
