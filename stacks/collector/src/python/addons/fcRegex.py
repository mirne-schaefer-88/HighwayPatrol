# Extract playlist URL with a regex statement
# The "group" parameter in "firstContactData" indicates which group in the regex to grab


# External libraries import statements
import re
import logging


# This application's import statements
from exceptions import *
import superGlblVars as GLOBALS


logger = logging.getLogger()


def getPlaylist(ap):
    if GLOBALS.useTestData:
        testFile = "NovoSibirsk.html"
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
    # regex = r"n_url = (?:\"(https?:\/\/.*)\");"  This works for rtsp.me
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

        returnUrl = matches.group(firstContactData["group"])
        return returnUrl

    else:
        logger.error(f"No matches found in pageContent looking for '{regex}'")
        logger.debug(f"Content received is:\n{pageContent}")
        raise HPatrolError(f"No matches found in pageContent looking for '{regex}'")
