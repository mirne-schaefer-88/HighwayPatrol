"""
Obtains video from a site in the "gandongyun.com" domain.

The sequence to obtain the m3u8 file is:
    0) Visit the main site, obtain a cookie from the response headers
    1) Construct a URL containing the camera ID, and the current time in epoch milliseconds. Specify the cookie in the
    request headers
    2) Visit that site; the returned JSON will contain the URL of the m3u8 playlist
    4) Visit the m3u8 URL and return its contents

    Note that both the main site, the constructed URL, and the playlist (m3u8) URL are visited using an augmented set
    of headers.
"""


# External libraries import statements
import json
import copy
import logging
import datetime as dt


# This application's import statements
from exceptions import *
import superGlblVars as GLOBALS


logger = logging.getLogger()


# Constants
TIME_ID = "&_="
VID_URL = "http://xzglwx.gandongyun.com/xz_video/video/getVideoUrl?videoId="

PLAY_URL = "playUrl"
SET_COOKIE = "Set-Cookie"
HOST = "xzglwx.gandongyun.com"


def getPlaylist(ap):
    theUrl = ap["accessUrl"]

    if GLOBALS.useTestData:
        testFile = "gandongRespHeaders.json"
        logger.debug(f"Reading from test file '{testFile}'")
        with open(f"{GLOBALS.testResources}/{testFile}", 'r') as f:
            respHeaders = json.load(f)
    else:
        # Set up headers to visit main URL
        # Note: Using deepcopy to not modify the received ap values
        theHeaders = copy.deepcopy(ap["headers"])
        theHeaders.pop("Referer", None)
        theHeaders.pop("Origin", None)

        theHeaders["Upgrade-Insecure-Requests"] = "1"
        logger.debug(f"Headers for main URL: {theHeaders}")

        # Just visit the main URL; get cookie from response headers
        logger.debug("Visiting main site")
        try:
            r = GLOBALS.netUtils.get(theUrl, headers=theHeaders)
        except Exception:
            raise ConnectionError(f"URL access failed from {GLOBALS.perceivedIP} attempting {theUrl}") from None

        respHeaders = r.headers

    # Get the cookie from the response headers
    # try:
    #    cookie = respHeaders[SET_COOKIE].split(';')[0]
    #    logger.debug(f"Cookie returned: {cookie}")
    # except KeyError:
    #    raise HPatrolError('Required cookie not in response headers')

    # And visit the site
    if GLOBALS.useTestData:
        testFile = "gandongRespText.json"
        logger.debug(f"Reading from test file '{testFile}'")
        with open(f"{GLOBALS.testResources}/{testFile}", 'r') as f:
            respText = f.read()
    else:
        # Create the video URL
        utcMilli = round(dt.datetime.now(dt.UTC).timestamp() * 1000)
        videoUrl = VID_URL + ap["deviceID"] + TIME_ID + str(utcMilli)
        #logger.debug(f"Video URL: {videoUrl}")

        # Tweak the headers
        theHeaders.pop("Upgrade-Insecure-Requests", None)
        theHeaders["Host"] = HOST
        # theHeaders["Cookie"] = cookie
        theHeaders["Referer"] = theUrl
        logger.debug(f"Headers for video URL: {theHeaders}")

        try:
            r = GLOBALS.netUtils.get(videoUrl, headers=theHeaders)
        except Exception:
            raise ConnectionError(f"URL access failed from {GLOBALS.perceivedIP} attempting {videoUrl}") from None

        respText = r.text

    # Retrieve the JSON and get the playlist URL
    videoDict = json.loads(respText)
    try:
        returnUrl = videoDict[PLAY_URL]
    except KeyError:
        raise HPatrolError('Play URL not in returned JSON')

    # And return the playlist URL
    if not returnUrl:
        raise HPatrolError(f"Could not find playlist URL in JSON returned from video URL: {respText}")

    return returnUrl.strip()
