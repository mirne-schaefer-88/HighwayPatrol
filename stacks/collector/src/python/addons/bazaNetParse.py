# External libraries import statements
import re
import logging


# This application's import statements
from exceptions import *
import superGlblVars as GLOBALS


logger = logging.getLogger()


# Need the token and the netLoc from the population.json then build a composed
# Generator did all the work for us
def getPlaylist(ap):
    theUrl = ap["accessUrl"]
    theHeaders = ap["headers"]
    theToken = ap["bazaParameters"]["token"]
    theServer = ap["bazaParameters"]["server"]
    theCamName = ap["bazaParameters"]["camName"]


    if GLOBALS.useTestData:
        return "https://dummyjson.com/test"

    try:
        # Not interested in the contents, only the cookies and headers
        # The network object maintains those across requests
        throwAway = GLOBALS.netUtils.get(theUrl, headers=theHeaders)
    except:
        raise ConnectionError(f"URL access failed from {GLOBALS.perceivedIP} attempting {theUrl}") from None

    returnUrl = f"https://{theServer}/{theCamName}/index.fmp4.m3u8?token={theToken}"
    logger.info("Successfully composed URL from where to get the playlist")

    return returnUrl


def OLD_parseForPlaylist(startContent):
    # Example of what we're expecting and searching for
    # <iframe allowfullscreen class='camera-frame' style='width: 100%; border: none; aspect-ratio: 1.7777777777778;' src='https://dvr1.baza.net/poshehonskoe.kolco-90efe89be1/embed.html?autoplay=true&token=YzJmZTUxNThjMjliMTY1NWJmMjdhNDNmNWM5YTNjYWUyYmQxZDdjNy4xNzQ2ODE0MzMw'></iframe>

    regex = r"<iframe (?:.*)src='(.*)/embed.html\?autoplay(?:.*)&token=(.*)'></iframe>"
    matches = re.search(regex, startContent)
    if matches:
        # print ("Match was found at {start}-{end}: {match}".format(start = matches.start(), end = matches.end(), match = matches.group()))
        # for groupNum in range(0, len(matches.groups())):
        #     groupNum = groupNum + 1
        #     print ("Group {groupNum} found at {start}-{end}: {group}".format(groupNum = groupNum, start = matches.start(groupNum), end = matches.end(groupNum), group = matches.group(groupNum)))

        netLoc = matches.group(1)
        accessToken = matches.group(2)
        # camName = netLoc.split("-")[-1]
        # logger.debug(f"****camName: {camName}")

    else:
        logger.critical(f"String NOT found looking for '{regex}'; exiting")
        logger.debug(f"Content received is:\n{startContent}")
        raise HPatrolError("Access token NOT found during parse")

    logger.info("Successfully composed URL from where to get the playlist")
    composed = f"{netLoc}/index.fmp4.m3u8?token={accessToken}"

    return composed
