"""
Module to create the aimpoints

If run as a script, use the command line parameter:

VIDEOS - to generate the video aimpoints

When you specify the above parameter, this will force the rewrite of the VIDEOS aimpoints.
In this case, there is no comparison with the 'Master' list.

If you do not specify a parameter, this script will behave like the lambda version.
If run as a lambda, this code behaves as follows:

It visits the target site and produces a TAB-delimited list of camera IDs and other pertinent information from the JSON found.
Pertinent data includes camera ID (cameraNum), name, longitude, latitude, etc.

This code will compare the current list of IDs (and other info) with a 'Master' list in the 'metadata' folder on S3.
If there is no master list found, this code will create one and store it in the 'metadata' folder.

If an ID is added, deleted, or the imageURL for an ID is modified, this script will also re-write the VIDEOS aimpoints.

"""


# External libraries import statements
import os
import time
import json
import logging
import argparse
import threading
import datetime as dt
from pathlib import Path


# This application's import statements
import processInit
import systemSettings
from exceptions import *
import comparitor as comp
import superGlblVars as GLOBALS
from superGlblVars import config
from orangeUtils import auditUtils
from orangeUtils import utils as ut
from utils import hPatrolUtils as hput
from orangeUtils.auditUtils import AuditLogLevel


logger = logging.getLogger()

# Constants
DOMAIN = "hngscloud.com"
START_URL = "https://weixin.hngscloud.com"
IDS_URL = "https://weixin.hngscloud.com/camera/search?zoomLevel=7&sbMapLevel=7&northEast=122.575596,38.573911&southWest=104.997471,29.017719&searchBlock=&choice="


def execute(upSince, isScript):
    # Identify ourselves for the audit logs
    GLOBALS.taskName = "Generator"
    GLOBALS.subtaskName = "Hngscloud"

    # Get the URLs and other info for the critical IDs
    logger.info("Getting video image IDs dictionaries")
    try:
        imageIdsInfo = _getTargetList()
    except HPatrolError:
        return False

    if not imageIdsInfo:
        logger.exception("No image dictionaries returned")
        return False

    logger.info(f"Number of IDs returned: {len(imageIdsInfo)}")

    # Get the list of selected IDs
    selectionsFile = f"selected-{DOMAIN}.json"
    try:
        videosSelection = hput.getSelection(selectionsFile)
    except HPatrolError as err:
        return False

    structTitles = (
          "ID"
        , "Name"
        , "Road"
        , "Region"
        , "On Line"
        , "Longitude"
        , "Latitude"
        )
    structKeys = (
          "key"
        , "name"
        , "road"
        , "region"
        , "online"
        , "longitude"
        , "latitude"
        )

    apTemplate = _getApTemplate()
    domainFolder = comp.getDomainFolder(apTemplate)

    # If running as a script, comparison is NOT done but aimpoints creation is.
    # If run as a lambda, comparison *is* done, and the master file and the
    # aimpoint files are re-created as necessary
    shouldWriteAimpoints = False
    if isScript:
        shouldWriteAimpoints = True
    else:
        try:
            shouldWriteAimpoints = comp.writeAPs(
                    upSince,
                    imageIdsInfo,
                    (structKeys, structTitles),
                    domainFolder,
                    "rptHngscloudMasterIdList",
                    selectedList=videosSelection)
        except HPatrolError:
            logger.exception("Unable to do ID comparison")
            return False

    if shouldWriteAimpoints:
        try:
            _doVideoCams(imageIdsInfo, videosSelection, apTemplate)
        except HPatrolError:
            return False

    return True


def _getTargetList():
    if GLOBALS.useTestData:
        testFile = "hngscloudIdsPage.json"
        logger.debug(f"Reading from test file '{testFile}'")
        with open(f"{GLOBALS.testResources}/{testFile}", 'r') as f:
            pageContent = f.read()
    else:
        # First visit the main site
        try:
            resp = GLOBALS.netUtils.get(START_URL, headers=config["sessionHeaders"])
        except ConnectionError:
            raise HPatrolError(f"URL access attempt failed for: {START_URL}") from None

        # Visit the cameras ID site and retrieve JSON containing the ID and related info
        headersDict = config["sessionHeaders"]
        headersDict["DNT"] = "1"
        headersDict["Host"] = "weixin.hngscloud.com"
        headersDict["Referer"] = "https://weixin.hngscloud.com/"
        headersDict["Sec-Fetch-Dest"] = "empty"
        headersDict["Sec-Fetch-Mode"] = "cors"
        headersDict["Sec-Fetch-Site"] = "same-origin"
        try:
            idsPageResp = GLOBALS.netUtils.get(IDS_URL, headers=headersDict)
        except Exception:
            raise HPatrolError(f"URL access failed for: {IDS_URL}") from None

        # Retrieve the HTML text containing ID info
        pageContent = idsPageResp.text

    try:
        cameraDict = json.loads(pageContent)
        logger.info("Obtained IDs Page JSON data")
    except Exception:
        logger.debug(f"Content received is:\n{pageContent}")
        raise

    try:
        cameraCode = cameraDict["code"]
    except KeyError:
        raise HPatrolError(f"'code' key missing from returned JSON")

    if cameraCode != 200:
        raise HPatrolError(f"Wrong code value returned: {cameraCode}")

    try:
        camIdsDictList = cameraDict["data"]
    except KeyError:
        raise HPatrolError(f"'data' key missing from returned JSON")

    retDictList = []
    for idsDict in camIdsDictList:
        try:
            theRoad = idsDict["road"]
            onLine = idsDict["online"]
            theID = idsDict["cameraNum"]
            theLat = idsDict["latitude"]
            theLong = idsDict["longitude"]
            theName = idsDict["cameraName"]
            theRegion = idsDict["regionName"]
        except KeyError:
            logger.warning(f"Key error in JSON for aCamDict: {idsDict}")
            continue

        # Sometimes the Region is blank
        if not theRegion:
            theRegion = "None"
        
        # Sometimes we get newlines in our text. This will mess up
        # the format of our output .tsv report
        if theRoad:
            theRoad = theRoad.replace("\n", " ")
        if theName:
            theName = theName.replace("\n", " ")
        if theRegion:
            theRegion = theRegion.replace("\n", " ")

        camDict = {
            "key": theID,
            "name": theName,
            "road": theRoad,
            "online": str(onLine),
            "region": theRegion,
            "longitude": theLong,
            "latitude": theLat
        }
        retDictList.append(camDict)

    return retDictList


def _allInputsValid(camSpec):
    if camSpec.get('key', "") == "":
        return False
    if camSpec.get('name', "") == "":
        return False
    if camSpec.get('road', "") == "":
        return False
    if camSpec.get('online', "") == "":
        return False
    if camSpec.get('region', "") == "":
        return False
    if camSpec.get('latitude', "") == "":
        return False
    if camSpec.get('longitude', "") == "":
        return False

    return True


def _doVideoCams(allCamsDict, selection, apTemplate):
    theKey = f"{DOMAIN}-autoParsed"
    aimpointDir = f"{GLOBALS.targetFiles}/{theKey}"
    monitoredDir = f"{GLOBALS.monitorTrgt}/{theKey}"
    GLOBALS.S3utils.deleteEntireKey(config["defaultWrkBucket"], aimpointDir)
    GLOBALS.S3utils.deleteEntireKey(config["defaultWrkBucket"], monitoredDir)

    # Loop through the cams
    for idx, aCam in enumerate(allCamsDict, start=1):
        # Don't go through everything if we're not on PROD
        if not GLOBALS.onProd and idx == 6:
            logger.debug(f"Not running on PROD; exiting at device #{idx}")
            break

        if not _allInputsValid(aCam):
            logger.info(f"Invalid data on '{aCam}'; continuing")
            continue

        theID = str(aCam["key"])
        if theID in selection:
            try:
                selectionState = selection[theID] if isinstance(selection[theID], str) else selection[theID]["monitoringData"]["selectionsState"]
            except KeyError as e:
                logger.error(f"Missing key {e} for id {theID} in selections file; skipping")
                continue
            theName = aCam["name"]
            theRoad = aCam["road"]
            onLine = aCam["online"]
            theRegion = aCam["region"]
            theLat  = aCam["latitude"]
            theLong = aCam["longitude"]

            logger.info(f"Creating JSON file for ID:{theID}")
            apTemplate["deviceID"] = theID
            apTemplate["longLat"] = [theLong, theLat]
            apTemplate["bucketPrefixTemplate"] = f"cn/hngscloud/{theID}/{{year}}/{{month}}/{{day}}"
            
            apTemplate["collEnabled"] = False
            if onLine == "1":
                apTemplate["collEnabled"] = True

            apTemplate["decoy"] = False
            if selection[theID] == "decoy" or selection[theID] == "monitor-decoy":
                apTemplate["decoy"] = True

            apTemplate["devNotes"]["road"] = theRoad
            apTemplate["devNotes"]["name"] = theName
            apTemplate["devNotes"]["region"] = theRegion

            outFile = os.path.join(config["workDirectory"], f"{theID}.json")
            try:
                ut.writeJsonDataToFile(apTemplate, outFile)
            except Exception as err:
                logger.exception(f"Error creating aimpoint file:::{err}")
                return False

            s3Dir = aimpointDir
            if "monitor" in selectionState:
                s3Dir = monitoredDir

            result = GLOBALS.S3utils.pushToS3(outFile,
                                                s3Dir,
                                                config["defaultWrkBucket"],
                                                s3BaseFileName=f"{theID}.json",
                                                deleteOrig=GLOBALS.onProd,
                                                extras={'ContentType': 'application/json'})

    return True


def _getApTemplate():
    apTemplate = {
          "deviceID": "SETLATER"
        , "collEnabled": "SETLATER"
        , "decoy": "SETLATER"
        , "collRegions": ["United States (N. Virginia)"]
        , "proxy": "hk-protonvpn.flurri.dom:3128"
        , "collectionType": "HNGCLD"
        , "accessUrl": START_URL
        , "pollFrequency": 10
        , "waitFraction": 0.5
        , "singleCollector": True
        , "concatenate": False
        , "transcodeExt": "mp4"
        , "filenameBase": "{deviceID}"
        , "finalFileSuffix": "_{year}-{month}-{day}-{hour}-{mins}"
        , "longLat": "SETLATER"
        , "bucketPrefixTemplate": "cn/hngscloud/{deviceID}/{year}/{month}/{day}"
        # NOTE: Headers below are suitable for accessing manifest and .ts locations
        # except that the "Host" needs to be supplied.
        , "headers": {
              "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:91.0) Gecko/20100101 Firefox/91.0"
            , "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
        	, "Accept-Encoding": "gzip, deflate"
            , "Accept-Language": "en-US,en;q=0.5"
            , "Cache-Control": "max-age=0"
            , "Connection": "keep-alive"
            , "DNT" : "1"
            , "Origin" : "https://weixin.hngscloud.com"
            , "Referer" : "https://weixin.hngscloud.com/"
            , "Sec-Fetch-Dest" : "empty"
            , "Sec-Fetch-Mode" : "cors"
            , "Sec-Fetch-Site" : "same-site"
            }
        , "devNotes": {
              "givenUrl": "https://weixin.hngscloud.com"
            , "startedOn": "January 2023"
            , "road": "SETLATER"
            , "region": "SETLATER"
            , "name": "SETLATER"
            , "setBy": "who originally worked it"
            , "missionTLDN": "cn"
            , "freqNote": "Playlist file points to about 10 seconds worth of data"
            , "singleNote": "playlist contains up to 5 .ts URLs each pointing to ~2 seconds of data"
            }
        }
    return apTemplate


def lambdaHandler(event, context):
    # Pre-set values in case execution is interrupted
    dataLevel = AuditLogLevel.INFO
    systemLevel = AuditLogLevel.INFO
    exitMessage = "Exit with errors"

    upSince = processInit.preFlightSetup()
    config["proxy"] = "hk-protonvpn.flurri.dom:3128"
    processInit.initSessionObject(config["sessionHeaders"])
    if not processInit.initialize():
        logger.error("Failed to initialize")
        exit(1)

    # Capture our ARN for later use
    GLOBALS.myArn = context.invoked_function_arn

    wasGoodRun = False
    try:
        if execute(upSince, False):
            wasGoodRun = True
            exitMessage = "Normal execution"

    except Exception as e:
        logger.exception(f"UNHANDLED EXCEPTION CAUGHT:::{e}")
        systemLevel = AuditLogLevel.CRITICAL
        wasGoodRun = False
        dataLevel = None

    finally:
        nownow = int(time.time())
        logger.info(f"Process clocked at {str(dt.timedelta(seconds=nownow-upSince))}")

        auditUtils.logFromLambda(
            event=event,
            msg=exitMessage,
            arn=GLOBALS.myArn,
            dataLevel=dataLevel,
            lambdaContext=context,
            ip=GLOBALS.perceivedIP,
            systemLevel=systemLevel,
            taskName=GLOBALS.taskName,
            stackName=GLOBALS.projectName,
            subtaskName=GLOBALS.subtaskName,
            enterDatetime=dt.datetime.fromtimestamp(upSince),
            leaveDatetime=dt.datetime.fromtimestamp(nownow),
            # **collectionSummaryArgs
            # collectionSummaryArgs1="some",
            # collectionSummaryArgs2="additional",
            # collectionSummaryArgs3="info"
            )

    toPrint = "Exiting Process"
    logger.info(f"= {toPrint} =")
    logger.info(f"=={'=' * len(toPrint)}==")

    return {"status": wasGoodRun}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description='Aimpoint generator for videos',
        formatter_class=argparse.RawTextHelpFormatter
    )
    theChoices = ["videos"]
    parser.add_argument('task',
                        help='task to execute',
                        choices=theChoices,
                        type=str.lower,
                        nargs='?',
                        const=''
                        )
    args = parser.parse_args()

    upSince = processInit.preFlightSetup()
    processInit.initSessionObject(config["sessionHeaders"])
    if not processInit.initialize():
        logger.error("Failed to initialize")
        exit(1)

    # Don't use proxy for AWS metadata; will timeout if proxy is tried
    # $ export no_proxy=169.254.169.254
    os.environ["no_proxy"] = f"{os.environ["no_proxy"]},169.254.169.254"

    # Create our ARN for later use
    from ec2_metadata import ec2_metadata as ec2    # Only for EC2 execution
    region = ec2.region
    accountId = ec2.account_id
    instanceId = ec2.instance_id

    arn = f"arn:aws:ec2:{region}:{accountId}:instance/{instanceId}"
    GLOBALS.myArn = arn

    # Set testResources directory
    scriptDir = Path(__file__).resolve().parents[2] # move up src/python/
    GLOBALS.testResources = f"{scriptDir}/{GLOBALS.testResources}"

    argVal = args.task
    if argVal:
        execute(upSince, True)
    else:
        execute(upSince, False)

    nownow = int(time.time())
    logger.info(f"Process clocked at {str(dt.timedelta(seconds=nownow-upSince))}")

    toPrint = f"Exiting Process: {threading.main_thread().name}"
    logger.info(f"= {toPrint} =")
    logger.info(f"=={'=' * len(toPrint)}==")
