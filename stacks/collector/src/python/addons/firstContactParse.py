# Handles different types of firstContact scenarios

# External libraries import statements
import json
import logging


# This application's import statements
from exceptions import *
import addons.fcRegex as regex
import addons.fcCookie as cookie
import addons.fcRegexJson as regexJson
import addons.fcRegexTemplate as regexTemplate


logger = logging.getLogger()


def getPlaylist(ap):
    returnedStr = _handleSubtype(ap)

    # Clean up string if necessary; just fixes any potentially escaped characters
    cleanUp = json.loads(f'{{"url":"{returnedStr}"}}')
    returnUrl = cleanUp["url"]

    try:
        # For cases where the returned string is a partial URL
        if ap["firstContactData"]["prependUrl"]:
            return f"{ap["accessUrl"]}{returnUrl}"
    except KeyError:
        pass
    return returnUrl


def _handleSubtype(ap):
    firstContactData = ap["firstContactData"]

    # Check that subtype is specified before attempting to access the target
    subType = firstContactData["subtype"]
    logger.info(f"FirstContact subtype: {subType}")

    if subType == "cookie":
        return cookie.getPlaylist(ap)

    elif subType == "regex":
        return regex.getPlaylist(ap)

    elif subType == "regexJson":
        return regexJson.getPlaylist(ap)

    elif subType == "regexTemplate":
        return regexTemplate.getPlaylist(ap)

    elif subType == "json":
        # As of 09.21.22, not using this subtype
        # This is meant to handle the return of a JSON string from an initial URL
        # The key to use in the received JSON is specified in the firstContactData
        theJson = json.loads("pageContent")
        keyPath = firstContactData["key"].split("/")

        # Navigate the JSON to the key we want
        theUrl = theJson
        for aKey in keyPath:
            theUrl = theUrl[aKey]
        return theUrl

    else:
        logger.error("FirstContact subtype not specified")
        raise HPatrolError("FirstContact subtype not specified")
