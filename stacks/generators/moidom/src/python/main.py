"""
Note that many things are hardcoded because this handles a specific domain

Can use these command line parameters when run as a script
    STILLS - to generate the still image aimpoints
    VIDEOS - to generate the video aimpoints
    BOTH - to generate both

When you specify any of the above parameters, this will force the
rewrite of the aimpoints of that type (whether STILLS, VIDEOS or
BOTH). In this case, there is no comparison with the master list.

If you do not specify a parameter, this script will behave like the
lambda version. If run as a lambda, the code behaves as follows:

This code will compare the current list of IDs (and other info) with a
master list in the metadata folder on S3. If there is no master list,
it will create one and store it in the metadata folder.

For still images, if an ID is added, deleted, or the URL is modified,
this script will also re-write the aimpoints. 
For videos, if an ID is added, deleted, or the URL is modified,
this script will also re-write the aimpoints, provided
one of the IDs in the selections file was affected.
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
from orangeUtils.awsUtils import SSMutils
from orangeUtils.auditUtils import AuditLogLevel


logger = logging.getLogger()

# Constants
BOTH = "both"
STILLS = "stills"
VIDEOS = "videos"
DOMAIN = "moidom-stream.ru"
START_URL = "https://moidom.citylink.pro"


def execute(argVal, upSince, manualRun):
    # Identify ourselves for the audit logs
    GLOBALS.taskName = "Generator"
    GLOBALS.subtaskName = "MoidomStream"

    mainUrl = START_URL
    selectionsFile = f"selected-{DOMAIN}_Videos.json"
    selectionsStillsFile = f"selected-{DOMAIN}_Stills.json"

    # Get the list of selected IDs
    # First thing, to make sure we have our selection files before hitting servers
    try:
        videosSelection = hput.getSelection(selectionsFile)
        stillsSelection = hput.getSelection(selectionsStillsFile)
    except HPatrolError:
        return False

    try:
        population = _getPopulation(mainUrl)
    except HPatrolError:
        return False
    if not population:
        logger.error("No population found")
        return False

    # Handle the STILLS first
    stillsStructTitles = (
          "ID"
        , "CityName"
        , "CityKey"
        , "CamId"
        , "CamName"
        , "Latitude"
        , "Longitude"
        , "ImageURL"
        )
    stillsStructKeys = (
          "id"
        , "cityName"
        , "cityKey"
        , "camId"
        , "name"
        , "latitude"
        , "longitude"
        , "imageUrl"
        )
    stillsApTemplate = _getStillsApTemplate()
    stillsDomainFolder = comp.getDomainFolder(stillsApTemplate)

    # Now handle the VIDEOS
    videosStructTitles = (
          "ID"
        , "City"
        , "Name"
        , "PlaylistURL"
        , "Longitude"
        , "Latitude"
        )
    videosStructKeys = (
          "id"
        , "cityName"
        , "name"
        , "videoUrl"
        , "longitude"
        , "latitude"
        )
    videosApTemplate = _getVideosApTemplate()
    vidsDomainFolder = comp.getDomainFolder(videosApTemplate)

    # If running as a script (manually), comparison is NOT done but aimpoints creation is
    # If run as a lambda, comparison *is* done, and the master file and the
    # aimpoint files are re-created as necessary
    writeStills = False
    writeVideos = False
    if manualRun:
        if argVal == STILLS or argVal == BOTH:
            writeStills = True
        if argVal == VIDEOS or argVal == BOTH:
            writeVideos = True
    else:
        try:
            writeStills = comp.writeAPs(
                    upSince,
                    population,
                    (stillsStructKeys, stillsStructTitles),
                    stillsDomainFolder,
                    "rptMoidomStillsMasterIdList",
                    selectedList=stillsSelection)
        except HPatrolError:
            logger.exception("Unable to do ID comparison for STILLS")
            return False

        try:
            writeVideos = comp.writeAPs(
                    upSince,
                    population,
                    (videosStructKeys, videosStructTitles),
                    vidsDomainFolder,
                    "rptMoidomVideosMasterIdList",
                    selectedList=videosSelection)
        except HPatrolError:
            logger.exception("Unable to do ID comparison for VIDEOS")
            return False

    if writeStills:
        try:
            _doStillCams(population, stillsSelection, stillsApTemplate)
        except HPatrolError:
            return False

    if writeVideos:
        try:
            _doVideoCams(population, videosSelection, videosApTemplate)
        except HPatrolError:
            return False

    return True


def _getPopulation(url):
    logger.info("Getting target population")
    cameraPopulation = []
    allCities = []

    # Get all cities in each region
    if GLOBALS.useTestData:
        testFile = "regions.json"
        logger.debug(f"Reading from test file '{testFile}'")
        with open(f"{GLOBALS.testResources}/{testFile}", 'r') as f:
            regions = json.load(f)
    else:
        try:
            regions = GLOBALS.netUtils.get(f"{url}/web/api/v2/regions")
            regions = regions.json()
        except Exception:
            raise ConnectionError(f"URL access attempt failed for: {url}/web/api/v2/regions")

    for region in regions:
        try:
            cities = region["cities"]
        except KeyError:
            continue
        allCities.extend(cities)

    for city in allCities:
        if GLOBALS.useTestData:
            try:
                testFile = f"cityCams{city["id"]}.json"
                logger.debug(f"Reading from test file '{testFile}'")
                with open(f"{GLOBALS.testResources}/{testFile}", 'r') as f:
                    cityCams = json.load(f)
            except FileNotFoundError:
                logger.error(f"Test file '{testFile}' not found")
                cityCams = []
        else:
            # Fake more human-like interaction
            ut.randomSleep(floor=1, ceiling=5)
            try:
                cityCams = GLOBALS.netUtils.get(f"{url}/web/api/v2/cameras/public/map/{city["id"]}", headers=config["sessionHeaders"])
                cityCams = cityCams.json()
            except Exception:
                raise ConnectionError(f"URL access failed from {GLOBALS.perceivedIP} attempting {url}/web/api/v2/cameras/public/map/{city["id"]}")

        cityKey = city["key"].replace("_", "-")
        cityName = city["name"]
        for cam in cityCams:
            cityCam = {
                "id": f"{cam["id"]}-{cityKey}", # DeviceID we use in aimpoints
                "cityName": cityName,
                "cityKey": cityKey,
                "camId": cam["id"], # Individual camID from the source
                "name": cam["name"],
                "latitude": cam["latitude"],
                "longitude": cam["longitude"],
                "imageUrl": cam["img"],
                "videoUrl": f"{START_URL}/web/api/v2/camera/{cam["id"]}/playlist"
            }
            cameraPopulation.append(cityCam)
    return cameraPopulation


def _doStillCams(camPopulation, selection, apTemplate):
    theKey = f"{DOMAIN}_Stills-autoParsed"
    aimpointDir = f"{GLOBALS.targetFiles}/{theKey}"
    monitoredDir = f"{GLOBALS.monitorTrgt}/{theKey}"
    GLOBALS.S3utils.deleteEntireKey(config["defaultWrkBucket"], aimpointDir)
    GLOBALS.S3utils.deleteEntireKey(config["defaultWrkBucket"], monitoredDir)

    # Loop through the cams
    for idx, cam in enumerate(camPopulation, start=1):
        # Don't go through everything if we're not on PROD
        if not GLOBALS.onProd and idx == 6:
            logger.debug(f"Not running on PROD; exiting at population device #{idx}")
            break

        if cam["id"] in selection:
            newAimpoint = apTemplate.copy()
            deviceId = cam["id"]
            selectionState = selection[deviceId] if isinstance(selection[deviceId], str) else selection[deviceId]["monitoringData"]["selectionsState"]

            logger.info(f"Creating JSON file for ID:{deviceId}")
            newAimpoint["deviceID"] = deviceId
            newAimpoint["longLat"] = [float(cam["longitude"]), float(cam["latitude"])]
            newAimpoint["proxy"] = config["proxy"]

            newAimpoint = hput.mergeSelections(
                selected=selection[deviceId], baseTemplate=newAimpoint
            )
            outFile = os.path.join(config["workDirectory"], f"{deviceId}.json")
            try:
                ut.writeJsonDataToFile(newAimpoint, outFile)
            except Exception as err:
                logger.exception(f"Error creating aimpoint file:::{err}")
                return False

            s3Dir = aimpointDir
            if "monitor" in selectionState:
                s3Dir = monitoredDir

            result = GLOBALS.S3utils.pushToS3(outFile,
                                s3Dir,
                                config["defaultWrkBucket"],
                                s3BaseFileName=f"{deviceId}.json",
                                deleteOrig=GLOBALS.onProd,
                                extras={"ContentType": "application/json"})
    return True


def _doVideoCams(allCamsDict, selection, apTemplate):
    theKey = f"{DOMAIN}_Videos-autoParsed"
    aimpointDir = f"{GLOBALS.targetFiles}/{theKey}"
    monitoredDir = f"{GLOBALS.monitorTrgt}/{theKey}"
    GLOBALS.S3utils.deleteEntireKey(config["defaultWrkBucket"], aimpointDir)
    GLOBALS.S3utils.deleteEntireKey(config["defaultWrkBucket"], monitoredDir)

    # Loop through the cams
    for idx, aCamDict in enumerate(allCamsDict, start=1):
        # Don't go through everything if we're not on PROD
        if not GLOBALS.onProd and idx == 6:
            logger.debug(f"Not running on PROD; exiting at population device #{idx}")
            break

        theID = aCamDict["id"]
        if theID in selection:
            newAimpoint = apTemplate.copy()
            try:
                selectionState = selection[theID] if isinstance(selection[theID], str) else selection[theID]["monitoringData"]["selectionsState"]
            except KeyError as err:
                logger.critical(f"Id '{theID}' in the selections file is missing element {err}")
                raise

            logger.info(f"Creating JSON file for ID: {theID}")
            newAimpoint["deviceID"] = theID
            newAimpoint["accessUrl"] = aCamDict["videoUrl"]
            newAimpoint["longLat"] = [float(aCamDict["longitude"]), float(aCamDict["latitude"])]
            newAimpoint["proxy"] = config["proxy"]

            newAimpoint["transcodeExt"] = None
            if selectionState == "mp4" or selectionState == "monitor-mp4":
                newAimpoint["transcodeExt"] = "mp4"

            newAimpoint = hput.mergeSelections(
                selected=selection[theID], baseTemplate=newAimpoint
            )

            outFile = os.path.join(config["workDirectory"], f"{theID}.json")
            try:
                ut.writeJsonDataToFile(newAimpoint, outFile)
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


def _getStillsApTemplate():
    stillsApTemplate = {
          "deviceID": "SETLATER"
        , "collEnabled": True
        , "decoy": False
        , "collRegions": ["Europe (Stockholm)"]
        , "proxy": None
        , "collectionType": "MSTLLS"
        , "accessUrl": "SETBYADDON"
        , "urlTemplate": "https://moidom.citylink.pro/web/api/v2/camera/{ID}/screenshot?datetime={YYYY}-{MM}-{DD}T{hh}:{mm}:00-00:00"
        , "pollFrequency": 300
        , "filenameBase": "moidomStream-{deviceID}"
        , "finalFileSuffix": "_{year}-{month}-{day}"
        , "bucketPrefixTemplate": "ru/moidomStreamStills/{year}/{month}/{deviceID}"
        , "longLat": "SETLATER"
        , "headers": {
              "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:91.0) Gecko/20100101 Firefox/91.0"
            , "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
        	, "Accept-Encoding": "gzip, deflate"
            , "Accept-Language": "en-US,en;q=0.5"
            , "Cache-Control": "max-age=0"
            , "Connection": "keep-alive"
        }
        , "monitoringData": {"monitorFrequency": 2}
        , "devNotes": {
              "givenURL": "https://moidom.citylink.pro/pz"
            , "startedOn": "November 2022"
            , "setBy": "who originally worked it"
            , "missionTLDN": "ru"
        }
    }
    return stillsApTemplate


def _getVideosApTemplate():
    videosApTemplate = {
          "deviceID": "SETLATER"
        , "collEnabled": True
        , "decoy": "SETLATER"
        , "collRegions": ["United States (N. Virginia)"]
        , "proxy": None
        , "collectionType": "MOIDOM"
        , "accessUrl": "SETLATER"
        , "pollFrequency": 30
        , "waitFraction": 0.6
        , "singleCollector": True
        , "concatenate": False
        , "transcodeExt": "SETLATER"
        , "filenameBase": "{deviceID}"
        , "finalFileSuffix": "_{year}-{month}-{day}-{hour}-{mins}-{secs}"
        , "bucketPrefixTemplate": "ru/moidomStreamVids/{deviceID}/{year}/{month}/{day}"
        , "longLat": "SETLATER"
        , "headers": {
              "Host": "moidom.citylink.pro"
            , "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:149.0) Gecko/20100101 Firefox/149.0"
            , "Accept": "*/*"
            , "Accept-Language": "en-US,en;q=0.9"
            , "Accept-Encoding": "gzip, deflate, br, zstd"
            , "Referer": "https://moidom.citylink.pro/"
            , "Connection": "keep-alive"
            , "Sec-Fetch-Dest": "empty"
            , "Sec-Fetch-Mode": "cors"
            , "Sec-Fetch-Site": "same-origin"
            }
        , "monitoringData": {"monitorFrequency": 2}
        , "devNotes": {
              "givenURL": "https://moidom.karelia.pro"
            , "startedOn": "November 2022"
            , "setBy": "who originally worked it"
            , "missionTLDN": "ru"
            }
        }
    return videosApTemplate


def lambdaHandler(event, context):
    # Pre-set values in case execution is interrupted
    dataLevel = AuditLogLevel.INFO
    systemLevel = AuditLogLevel.INFO
    exitMessage = "Exit with errors"

    upSince = processInit.preFlightSetup()
    if event["envCmd"] == "forwardDeployed":
        proxyUrl = _getProxyUrl()
        config["proxy"] = proxyUrl
    processInit.initSessionObject(config["sessionHeaders"])
    if not processInit.initialize():
        logger.error("Failed to initialize")
        exit(1)

    # Capture our ARN for later use
    GLOBALS.myArn = context.invoked_function_arn

    wasGoodRun = False
    try:
        if execute("", upSince, False):
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
        description='Aimpoint generator for videos or stills',
        formatter_class=argparse.RawTextHelpFormatter
    )

    theChoices = [STILLS, VIDEOS, BOTH]
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
        execute(argVal, upSince, True)
    else:
        execute("", upSince, False)

    nownow = int(time.time())
    logger.info(f"Process clocked at {str(dt.timedelta(seconds=nownow-upSince))}")

    toPrint = f"Exiting Process: {threading.main_thread().name}"
    logger.info(f"= {toPrint} =")
    logger.info(f"=={'=' * len(toPrint)}==")


def _getProxyUrl() -> str:
    ssmUtils = SSMutils()
    params = ssmUtils.getParameterValues(prefix="/wormhole/proxies/commercial_proxy_")
    # Can't use GLOBALS.onProd here because it hasn't been set yet (it's set on processInit.initialize())
    if config["mode"] == "prod":
        # This proxy must ONLY be used in the Stargate environment!!!
        proxyUrl = params["/wormhole/proxies/commercial_proxy_url_template"].format(
            params["/wormhole/proxies/commercial_proxy_credentials"],
            "ru",
            "zhigulevsk",
            params["/wormhole/proxies/commercial_proxy_server"],
            params["/wormhole/proxies/commercial_proxy_port"]
        )
        # 20260723: NOT using a random city for now
        # ).replace("_city-", "") # Use random city
        proxy = "http://" + proxyUrl
    else:
        logger.warning("System mode is not 'PROD', proxy will not be added to aimpoints")
        proxy = ""

    return proxy
