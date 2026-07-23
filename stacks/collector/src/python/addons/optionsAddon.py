# External libraries import statements
import json
import logging


# This application's import statements
from exceptions import *
import superGlblVars as GLOBALS


logger = logging.getLogger()


# This server requires receiving an OPTIONS call before the request
def getPlaylist(ap) -> str:
    theUrl = ap["accessUrl"]
    deviceID = ap["deviceID"]
    theHeaders = ap["headers"]

    try:
        GLOBALS.netUtils.options(theUrl, headers=theHeaders)

        response = GLOBALS.netUtils.post(theUrl, data=json.dumps(deviceID))
    except Exception:
        raise ConnectionError(f"URL access failed from {GLOBALS.perceivedIP} attempting {theUrl}")

    contents = json.loads(response.text)

    # This is the path to the URL for the avanta-telecom.ru site
    # Modify this code with a generic solution if needed for another OPTIONS site
    try:
        returnUrl = contents["result"]["cam"]
    except TypeError as err:
        logger.error(err)
        logger.debug(f"Content received is:\n{contents}")
        raise HPatrolError("TypeError")

    # logger.debug(returnUrl)
    return returnUrl
