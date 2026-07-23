# External libraries import statements
import time
import logging
import datetime as dt


# This application's import statements
import addons.fcRegexJson as regexJson
import superGlblVars as GLOBALS
from exceptions import *


logger = logging.getLogger()


def getPlaylist(ap):
    """
    Interface function for video retrievals
    """

    # Moidom returns a session cookie, even though the request returns 403
    _getSessionCookie(ap)

    # Reusing addons; moidom vids use the regexJson technique
    ap["playlistRegex"] = "(.*)"
    ap["firstContactData"] = {
          "subtype": "regexJson"
        , "key": "url"
    }
    # logger.debug("SLEEP for 2s before requesting playlist") # it just proved useful
    time.sleep(2)

    return regexJson.getPlaylist(ap)


def getAccessUrl(ap):
    """
    Interface function for still images retrieval
    """

    # Moidom returns a session cookie, even though the request returns 403
    _getSessionCookie(ap)

    camId = ap["deviceID"].split("-")[0]
    now = dt.datetime.now()
    # Must be rounded down
    minutes = (now.minute // 10) * 10
    firstUrl = ap["urlTemplate"].format(
        ID=camId,
        YYYY=now.year,
        MM=f"{now.month:02d}",
        DD=f"{now.day:02d}",
        hh=f"{now.hour:02d}",
        mm=f"{minutes:02d}"
    )

    # Get the JSON that has the actual accessURL
    # TODO: Add GLOBALS.useTestData 
    try:
        response = GLOBALS.netUtils.get(firstUrl)
        urlJson = response.json()
        returnUrl = urlJson["url"]

    except KeyError as err:
        logger.error(f"Parameter {err} missing in response")
        logger.debug(f"Content received is:\n{response.text}")
        raise ConnectionError("Error getting accessURL")
    except ConnectionError as err:
        raise ConnectionError(f"Error getting accessURL::{err}")

    return returnUrl


def _getSessionCookie(ap):
    if GLOBALS.useTestData:
        # Not hitting the 'net in TEST mode
        return

    headers = {
        "Host": "moidom.citylink.pro",
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br, zstd",
        "Referer": "https://moidom.citylink.pro",
        "DNT": "1",
        "Sec-GPC": "1",
        "Connection": "keep-alive",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "Priority": "u=4"
    }

    # Only interested in the session cookie here
    try:
        # Bypass using netUtils.get() here because we know that this request will return a 403 response
        # This avoids the 30 second sleep in netUtils and a repeat request to the same URL
        # Use sessionObj.get() directly so that the cookies still get set on the shared session object
        GLOBALS.netUtils.sessionObj.get("https://moidom.citylink.pro/web/api/v2/session", headers=headers, timeout=10)
    except Exception:
        pass

    if not GLOBALS.netUtils.sessionObj.cookies.get_dict():
        logger.warning(f"Session cookies were not set for camId {ap['deviceID']}")
        raise HPatrolError("Session cookies not set")

    logger.info(f"NOM NOM COOKIE!: {GLOBALS.netUtils.sessionObj.cookies.get_dict()}")
