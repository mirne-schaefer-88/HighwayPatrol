# Grab a JSON object from a server response and
# navigate to a specific key in said JSON to get the playlist URL


# External libraries import statements
import re
import json
import logging


# This application's import statements
from exceptions import *
import superGlblVars as GLOBALS


logger = logging.getLogger()


def getPlaylist(ap):
    if GLOBALS.useTestData:
        testFile = "EarthCam - Las Vegas Cams.html"
        logger.debug(f"Reading from TEST file '{testFile}'")
        with open(f"{GLOBALS.testResources}/{testFile}", 'r') as f:
            testData = f.read()
        pageContent = testData

    else:
        try:
            resp = GLOBALS.netUtils.get(ap["accessUrl"], headers=ap["headers"])
            pageContent = resp.text
        except Exception as err:
            logger.error(f"Exception: {err}")
            raise HPatrolError(f"URL access failed from {GLOBALS.perceivedIP} attempting {ap["accessUrl"]}")

    regex = ap["playlistRegex"]
    firstContactData = ap["firstContactData"]
    keyPath = firstContactData["key"].split("/")

    try:
        matches = re.search(regex, pageContent)
    except TypeError:
        logger.error(f"TypeError looking for '{regex}'")
        logger.debug(f"Content received is:\n{pageContent}")
        raise HPatrolError("TypeError")

    if matches:
        # # print ("Match was found at {start}-{end}: {match}".format(start = matches.start(), end = matches.end(), match = matches.group()))
        # for groupNum in range(0, len(matches.groups())):
        #     groupNum = groupNum + 1
        #     print ("Group {groupNum} found at {start}-{end}: {group}".format(groupNum = groupNum, start = matches.start(groupNum), end = matches.end(groupNum), group = matches.group(groupNum)))

        theJson = json.loads(matches.group(1))
        # logger.debug(f"\nGROUP1: \n'{theJson}'\n")
    else:
        logger.error(f"No matches found in pageContent looking for '{regex}'")
        logger.debug(f"Content received is:\n{pageContent}")
        raise HPatrolError(f"No matches found in pageContent looking for '{regex}'")

    # Navigate the JSON to the key we want
    returnUrl = theJson
    for aKey in keyPath:
        returnUrl = returnUrl[aKey]

    return returnUrl
