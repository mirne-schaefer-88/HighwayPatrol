# This addon takes groups from playlistRegex and puts them where needed in urlTemplate to build the URL
# For example, with a playlistRegex as
# "theIframe\\.src = 'https?://(?P<group1>.*)/(.*)/embed\\.html\\?realtime&token=(?P<group2>.*)';"
# it gets group1 and group2 and puts them into the urlTemplate "http://{group1}/{deviceID}/playlist.m3u8?token={group2}"


# External libraries import statements
import re
import logging


# This application's import statements
from exceptions import *
import superGlblVars as GLOBALS


logger = logging.getLogger()


def getPlaylist(ap):
    if GLOBALS.useTestData:
        testFile = "wellcom.ru_get-camera.html"
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
    theTemplate = firstContactData["urlTemplate"]
    try:
        matches = re.search(regex, pageContent)
        if matches:
            # print ("Match was found at {start}-{end}: {match}".format(start = matches.start(), end = matches.end(), match = matches.group()))
            # for groupNum in range(0, len(matches.groups())):
            #     groupNum = groupNum + 1
            #     print ("Group {groupNum} found at {start}-{end}: {group}".format(groupNum = groupNum, start = matches.start(groupNum), end = matches.end(groupNum), group = matches.group(groupNum)))

            namedGroups = matches.groupdict()
            # logger.debug(f"\n\nMATCHES: {namedGroups}\n\n")
            group1 = namedGroups.get("group1", None)
            group2 = namedGroups.get("group2", None)
            group3 = namedGroups.get("group3", None)
            group4 = namedGroups.get("group4", None)
            group5 = namedGroups.get("group5", None)
        else:
            logger.error(f"No matches found in pageContent looking for '{regex}'")
            logger.debug(f"Content received is:\n{pageContent}")
            raise HPatrolError(f"No matches found in pageContent looking for '{regex}'")

    except TypeError:
        logger.error(f"TypeError looking for '{regex}'")
        logger.debug(f"Content received is:\n{pageContent}")
        raise HPatrolError("TypeError")

    returnUrl = theTemplate.format(
        group1=group1,
        group2=group2,
        group3=group3,
        group4=group4,
        group5=group5,
        deviceID=ap["deviceID"]
    )
    # logger.debug(f"returnUrl: {returnUrl}")

    return returnUrl
