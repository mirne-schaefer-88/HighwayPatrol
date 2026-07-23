"""
Module to create the aimpoints

If run as a script, use the command line parameter:

STILLS - to generate the still image aimpoints

When you specify the above parameter, this will force the rewrite
of the STILLS aimpoints. In this case, there is no comparison with
the 'Master' list.

If you do not specify a parameter, this script will behave like the
lambda version. If run as a lambda, this code behaves as follows:

This code will compare the current list of IDs (and other info) with a
'Master' list in the 'metadata' folder on s3. If there is no master list
found, this code will create one and store it in the 'metadata' folder.

If an ID is added, deleted, or the imageURL for an ID is modified,
this script will also re-write the STILLS aimpoints.
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
DOMAIN = "digitraffic.fi"
START_URL = "https://tie.digitraffic.fi/api/weathercam/v1/stations"


def execute(upSince, isScript):
    # Identify ourselves for the audit logs
    GLOBALS.taskName = "Generator"
    GLOBALS.subtaskName = "DigitrafficFi"

    # Get the URLs and other info for the critical IDs
    logger.info("Getting still image IDs dictionaries")
    imageIdsInfo = _getTargetList()
    if not imageIdsInfo:
        logger.exception("No image dictionaries returned")
        return False

    # Note that in this generator, there is no list of selected IDs - all found IDs are used
    structTitles = (
          "ID"
        , "ImageURL"
        , "StationName"
        , "Latitude"
        , "Longitude"
        )
    structKeys = (
          "key"
        , "url"
        , "stationName"
        , "latitude"
        , "longitude"
        )

    apTemplate = _getApTemplate()
    domainFolder = apTemplate["deliveryKey"]

    # If running as a script, comparison is NOT done but aimpoints creation is
    # If run as a lambda, comparison *is* done, and the master file and the
    # aimpoint files are re-created as necessary
    shouldWriteAimpoints = False
    if isScript:
        shouldWriteAimpoints = True
    else:
        fluorineBucket = hput.pickBestBucket(apTemplate, "dstBucket")
        try:
            shouldWriteAimpoints = comp.writeAPs(
                upSince,
                imageIdsInfo,
                (structKeys, structTitles),
                domainFolder,
                "rptFluorineMasterIdList",
                bucketName=fluorineBucket)
        except HPatrolError:
            logger.exception("Unable to do ID comparison")
            return False

    if shouldWriteAimpoints:
        logger.info("Processing for still images")
        try:
            _doStillCams(imageIdsInfo, apTemplate)
        except HPatrolError:
            return False
    else:
        logger.info("Not re-writing STILLS aimpoints")

    return True


def _getTargetList():
    if GLOBALS.useTestData:
        testFile = "fluorineMainPage.json"
        logger.debug(f"Reading from test file '{testFile}'")
        with open(f"{GLOBALS.testResources}/{testFile}", 'r') as f:
            pageContent = f.read()
    else:
        # First visit the main site
        try:
            resp = GLOBALS.netUtils.get(START_URL, headers=config["sessionHeaders"])
        except Exception:
            raise ConnectionError(f"URL access attempt failed for: {START_URL}")

        # Retrieve the HTML text containing ID info
        pageContent = resp.text

    try:
        mainPageDict = json.loads(pageContent)
        logger.info("Obtained Regions JSON data")
    except Exception:
        logger.debug(f"Content received is:\n{pageContent}")
        raise

    dictType = mainPageDict.get("type", None)
    if not dictType or dictType != "FeatureCollection":
        logger.debug("Main page dictionary not a feature collection")
        raise

    if not "features" in mainPageDict:
        logger.debug("FEATURES key not in main page dictionary")
        raise

    dictList = []
    for feature in mainPageDict["features"]:
        featureType = feature.get("type", "")
        if not featureType or featureType != "Feature":
            continue

        latitude = ""
        longitude = ""
        geometry = feature.get("geometry", "")
        if geometry:
            geometryType = geometry.get("type", "")
            if geometryType and geometryType == "Point":
                coordList = geometry.get("coordinates", [])
                if coordList:
                    latitude = coordList[0]
                    longitude = coordList[1]
        
        properties = feature.get("properties", "")
        if not properties:
            continue
        
        stationId = properties.get("id")
        stationName = properties.get("name", "")
        # collectionStatus = properties.get("collectionStatus", "")
        # dataUpdatedTime = properties.get("dataUpdatedTime", "")
        presets = properties.get("presets", [])
        if not presets:
            continue
        
        for preset in presets:
            presetId = preset.get("id", "")
            inCollection = preset.get("inCollection", False)
            if not inCollection:
                continue
 
            presetDict = {
                "key": presetId,
                "url": "https://weathercam.digitraffic.fi/" + presetId + ".jpg",
                "stationId": stationId,
                "stationName": stationName,
                "longitude": str(longitude),
                "latitude": str(latitude),
            }
            dictList.append(presetDict)

    logger.info(f"Total IDs: {len(dictList)}")
    return dictList


def _doStillCams(allCamsDict, apTemplate):
    theKey = f"{DOMAIN}-autoParsed"
    aimpointDir = f"{GLOBALS.targetFiles}/{theKey}"

    GLOBALS.S3utils.deleteEntireKey(config["defaultWrkBucket"], aimpointDir)

    # Loop through the cams
    outFileList = []
    prevStationId = ""
    for idx, aCamDict in enumerate(allCamsDict, start=1):
        # Don't go through everything if we're not on PROD
        if not GLOBALS.onProd and idx == 6:
            logger.debug(f"Not running on PROD; exiting at device #{idx}")
            break
        
        theID = aCamDict["key"]
        theImageUrl = aCamDict["url"]
        theStationId = aCamDict["stationId"]
        theStationName = aCamDict["stationName"]
        theLatitude = aCamDict["latitude"]
        theLongitude = aCamDict["longitude"]

        if not prevStationId:
            prevStationId = theStationId
            apTemplate["deviceIdList"] = []
            apTemplate["accessUrlList"] = []
            apTemplate["filenameBaseList"] = []

        if theStationId != prevStationId:
            deviceId = apTemplate["deviceID"]
            outFile = os.path.join(config["workDirectory"], f"{deviceId}.json")
            try:
                ut.writeJsonDataToFile(apTemplate, outFile)
            except Exception as err:
                logger.exception(f"Error creating aimpoint file:::{err}")
                return False
            outFileList.append((deviceId, outFile))
            prevStationId = theStationId
            apTemplate["deviceIdList"] = []
            apTemplate["accessUrlList"] = []
            apTemplate["filenameBaseList"] = []

        apTemplate["deviceID"] = theStationId
        apTemplate["deviceIdList"].append(theID)
        apTemplate["accessUrl"] = theImageUrl
        apTemplate["accessUrlList"].append(theImageUrl)
        apTemplate["filenameBase"] = f"fluorine-{theID}"
        apTemplate["filenameBaseList"].append(f"fluorine-{theID[:-2]}-{theID[-2:]}")
        apTemplate["longLat"] = [float(theLongitude), float(theLatitude)]
        apTemplate["bucketPrefixTemplate"] = f"{theStationId}/{{year}}/{{month}}"

        apTemplate["devNotes"]["stationID"] = theStationId
        apTemplate["devNotes"]["stationName"] = theStationName

    deviceId = apTemplate["deviceID"]
    outFile = os.path.join(config["workDirectory"], f"{deviceId}.json")
    try:
        ut.writeJsonDataToFile(apTemplate, outFile)
    except Exception as err:
        logger.exception(f"Error creating aimpoint file:::{err}")
        return False
    outFileList.append((deviceId, outFile))

    for tpl in outFileList:
        deviceId = tpl[0]
        outFile  = tpl[1]
        result = GLOBALS.S3utils.pushToS3(outFile,
                                aimpointDir,
                                config["defaultWrkBucket"],
                                s3BaseFileName=f"{deviceId}.json",
                                deleteOrig=GLOBALS.onProd,
                                extras={"ContentType": "application/json"})
    return True


def _getApTemplate():
    apTemplate = {
          "deviceID": "SETLATER"
        , "deviceIdList": "SETLATER"
        , "collEnabled": True
        , "collRegions": ["Europe (Stockholm)"]
        , "collectionType": "FSTLLS"
        , "accessUrl": "SETLATER"
        , "accessUrlList": "SETLATER"
        , "pollFrequency": 300
        , "filenameBase": "SETLATER"
        , "filenameBaseList": "SETLATER"
        , "finalFileSuffix": "_{year}-{month}-{day}"
        , "dstBucket": "fluorine-ch-prod"
        , "deliveryKey": "digitraffic"
        , "bucketPrefixTemplate": "SETLATER"
        , "longLat": "SETLATER"
        , "headers": {
              "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:91.0) Gecko/20100101 Firefox/91.0"
            , "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
        	, "Accept-Encoding": "gzip, deflate"
            , "Accept-Language": "en-US,en;q=0.5"
            , "Cache-Control": "max-age=0"
            , "Connection": "keep-alive"
        }
        , "devNotes": {
              "givenURL": "https://tie.digitraffic.fi/api/weathercam/v1/stations"
            , "startedOn": "December 2023"
            , "stationID": "SETLATER"
            , "stationName": "SETLATER"
            , "missionTLDN": "ru"
            , "setBy": "who originally worked it"
        }
    }
    return apTemplate


def lambdaHandler(event, context):
    # Pre-set values in case execution is interrupted
    dataLevel = AuditLogLevel.INFO
    systemLevel = AuditLogLevel.INFO
    exitMessage = "Exit with errors"

    upSince = processInit.preFlightSetup()
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
        description="Aimpoint generator for stills",
        formatter_class=argparse.RawTextHelpFormatter
    )
    theChoices = ["stills"]
    parser.add_argument("task",
                        help="task to execute",
                        choices=theChoices,
                        type=str.lower,
                        nargs="?",
                        const=""
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
