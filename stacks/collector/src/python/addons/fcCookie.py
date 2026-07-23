# Obtains the playlist URL from a returned cookie


# External libraries import statements
import logging


# This application's import statements
from exceptions import *
import superGlblVars as GLOBALS


logger = logging.getLogger()


def getPlaylist(ap):
    if GLOBALS.useTestData:
        class MyClass:
            content = bytes("No web page data needed for this subtype", encoding="utf-8")
            headers = "Testing; no headers here either"
            class cookies:
                def get_dict():
                    return {"PHPSESSID":"o29u0k5dtbu4gtmucjjpht5va2"}
        resp = MyClass()

    else:
        try:
            resp = GLOBALS.netUtils.get(ap["accessUrl"], headers=ap["headers"])
        except Exception:
            raise HPatrolError(f"URL access failed from {GLOBALS.perceivedIP} attempting {ap["accessUrl"]}")

    allCookies = resp.cookies.get_dict()
    firstContactData = ap["firstContactData"]
    logger.debug(f"Cookies Received:  {allCookies}")

    lookFor = firstContactData["cookie"]
    returnUrl = firstContactData["urlTemplate"].format(cookie=allCookies[lookFor])

    return returnUrl
