"""
Aimpoint generator
Many things are hardcoded because this handles a specific domain

"""

# External libraries import statements
import os
import re
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
DOMAIN = "astrakhan"
START_URL = "https://live.astrakhan.ru/"


def lambdaHandler(event: dict, context: dict) -> dict:
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
        if execute(upSince):
            wasGoodRun = True
            exitMessage = "Normal Execution"

    except Exception as e:
        logger.exception(f"UNHANDLED EXCEPTION CAUGHT:::{e}")
        systemLevel = AuditLogLevel.CRITICAL
        wasGoodRun = False
        dataLevel = None

    finally:
        nownow = int(time.time())
        logger.info(
            f"Process clocked at {str(dt.timedelta(seconds=nownow-upSince))}"
        )
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
            leaveDatetime=dt.datetime.fromtimestamp(nownow)
        )

    toPrint = "Exiting Process"
    logger.info(f"= {toPrint} =")
    logger.info(f"=={'=' * len(toPrint)}==")

    return {"status": wasGoodRun}


def _getPopulation():
    mainSiteUrl = START_URL
    logger.info("Getting target population")

    if GLOBALS.useTestData:
        testFile = "astrakhanCams.html"
        logger.debug(f"Reading from test file '{testFile}'")
        with open(f"{GLOBALS.testResources}/{testFile}", "r") as f:
            pageContent = f.read()
    else:
        logger.info(f"Getting page '{mainSiteUrl}'")
        reqHeaders = config["sessionHeaders"]
        reqHeaders.update(
            {
                "Accept": "*/*",
                "Accept-Language": "en-US,en;q=0.5",
                "Accept-Encoding": "gzip, deflate, br",
                "Referer": "https://live.astrakhan.ru/map/",
                "DNT": "1",
                "Sec-GPC": "1",
                "Connection": "keep-alive",
                "Sec-Fetch-Dest": "script",
                "Sec-Fetch-Mode": "no-cors",
                "Sec-Fetch-Site": "same-origin",
                "TE": "trailers"
            }
        )

        # Hit the main site before grabbing cam data to mimic human interaction
        try:
            throwAway = GLOBALS.netUtils.get(mainSiteUrl, headers=config["sessionHeaders"])
        except Exception:
            raise ConnectionError(f"URL access attempt failed for: {mainSiteUrl}")
        ut.randomSleep(floor=10, ceiling=60)   
        try:
            throwAway = GLOBALS.netUtils.get(f"{mainSiteUrl}/map/", headers=config["sessionHeaders"])
        except Exception:
            raise ConnectionError(f"URL access attempt failed for: {mainSiteUrl}")

        # Grab the cameras and metadata
        astrakhanCameraUrl = f"{mainSiteUrl}/map/cams.php"
        try:
            r = GLOBALS.netUtils.get(astrakhanCameraUrl, headers=reqHeaders)
        except Exception:
            raise HPatrolError(
                f"URL access failed from {GLOBALS.perceivedIP} attempting {astrakhanCameraUrl}"
            )
        pageContent = r.text

    jsonPattern = (
        r"((?<=window\.GM\.CAMS)\s*=\s*)(.*?)(?=\s*;\s*window\.GM\.CAMS\.usedTags)"
    )
    match = re.search(jsonPattern, pageContent, re.S)
    if match:
        camData = match.group(2)
        jsonCamData = json.loads(camData)
        extractedCamData = jsonCamData["city"]
    else:
        raise HPatrolError(
            f"No match found searching {pageContent} with {jsonPattern}"
        )
    camList = []
    camIds = extractedCamData.keys()
    for camId in camIds:
        camObj = {}
        cam = extractedCamData[camId]
        camObj["id"] = camId
        camObj["lat"] = cam["coordinates"][0]
        camObj["lng"] = cam["coordinates"][1]
        camObj["address"] = cam["address"]
        camObj["sources"] = cam["sources"]
        camObj["tags"] = cam["tags"]
        camObj["tagsName"] = cam["tags_name"]
        camList.append(camObj)
    cameraPopulation = json.dumps(camList, ensure_ascii=False, indent=4)
    logger.info("Obtained camera JSON data")

    return cameraPopulation


def _doVideos(allCams, selection, ap):
    theKey = f"{DOMAIN}-autoParsed"
    aimpointDir = f"{GLOBALS.targetFiles}/{theKey}"
    monitoredDir = f"{GLOBALS.monitorTrgt}/{theKey}"
    GLOBALS.S3utils.deleteEntireKey(config["defaultWrkBucket"], aimpointDir)
    GLOBALS.S3utils.deleteEntireKey(config["defaultWrkBucket"], monitoredDir)

    counter = 1
    for cam in allCams:
        if not GLOBALS.onProd and counter == 5:
            logger.debug(f"Not running on PROD; exiting at device #{counter}")
            break
        camId = str(cam["id"])
        protocol = "https://"
        urlEnd = "/tracks-v1/index.fmp4.m3u8"
        if camId in selection:
            try:
                selectionState = selection[camId] if isinstance(selection[camId], str) else selection[camId]["monitoringData"]["selectionsState"]
            except KeyError as e:
                logger.error(f"Missing key {e} for id {camId} in selections file; skipping")
                continue
            if selection[camId] == "off":
                continue
            logger.info(f"Creating JSON file for ID: {camId}")
            ap["deviceID"] = camId
            ap["longLat"] = [int(cam["lng"]), int(cam["lat"])]
            # Almost all the devices have an "HD" source, but a few only have "SD"
            try:
                camIdPath = cam["sources"]["HD"]["file"]
                baseUrl = cam["sources"]["HD"]["host"]
            except KeyError:
                camIdPath = cam["sources"]["SD"]["file"]
                baseUrl = cam["sources"]["SD"]["host"]
            ap["accessUrl"] = f"{protocol}{baseUrl}/{camIdPath}{urlEnd}"
            ap["headers"][
                "Referer"
            ] = f"{protocol}{baseUrl}/{camIdPath}/embed.html?autoplay=true"
            ap["headers"]["Host"] = baseUrl
            if selectionState == "decoy" or selectionState == "monitor-decoy":
                ap["decoy"] = True
            else:
                ap["decoy"] = False

            outFile = os.path.join(config["workDirectory"], f"{camId}.json")
            try:
                ut.writeJsonDataToFile(ap, outFile)
            except Exception as err:
                logger.exception(f"Error creating aimpoint file:::{err}")
                continue

            s3Dir = aimpointDir
            if "monitor" in selectionState:
                s3Dir = monitoredDir

            result = GLOBALS.S3utils.pushToS3(
                outFile,
                s3Dir,
                config["defaultWrkBucket"],
                deleteOrig=GLOBALS.onProd,
                s3BaseFileName=f"{camId}.json",
                extras={"ContentType": "application/json"}
            )
            counter += 1


def _getApTemplate():
    apTemplate = {
        "deviceID": "SETLATER",
        "collEnabled": True,
        "decoy": "SETLATER",
        "collRegions": ["Europe (Frankfurt)"],
        "collectionType": "M3U",
        "accessUrl": "SETLATER",
        "pollFrequency": 32,
        "concatenate": False,
        "transcodeExt": "mp4",
        "filenameBase": "{deviceID}",
        "finalFileSuffix": "_{year}-{month}-{day}-{hour}-{mins}-{secs}",
        "bucketPrefixTemplate": "ru/astrakhan/{deviceID}/{year}/{month}/{day}",
        "longLat": "SETLATER",
        "headers": {
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate, br",
            "Accept-Language": "en-US,en;q=0.5",
            "Connection": "keep-alive",
            "Host": "SETLATER", 
            "Referer": "SETLATER",
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:128.0) Gecko/20100101 Firefox/128.0",
            "DNT": "1"
        },
        "devNotes": {
            "givenUrl": "https://live.astrakhan.ru/map/",
            "startedOn": "02.12.2024",
            "setBy": "who originally worked it"
        }
    }
    return apTemplate


def execute(upSince: int) -> bool:
    GLOBALS.taskName = "Generator"
    GLOBALS.subtaskName = "astrakhan"
    try:
        cameraPopulation = _getPopulation()
    except HPatrolError as err:
        logger.exception(f"Error getting target population:::{err}")
        return False
    selectionsFile = f"selected-{DOMAIN}.json"
    try:
        selection = hput.getSelection(selectionsFile)
    except HPatrolError:
        return False

    population = json.loads(cameraPopulation)
    structTitles = ("ID", "Lat", "Lng", "Address", "Sources", "Tags", "Tags Name")
    structKeys = ("id", "lat", "lng", "address", "sources", "tags", "tagsName")
    apTemplate = _getApTemplate()
    domainFolder = comp.getDomainFolder(apTemplate)

    try:
        shouldWriteAimpoints = comp.writeAPs(
            upSince,
            population,
            (structKeys, structTitles),
            domainFolder,
            "rptAstrakhanMasterIdList",
            selectedList=selection
        )
    except HPatrolError:
        logger.exception("Unable to do ID comparison")
    if shouldWriteAimpoints:
        try:
            _doVideos(population, selection, apTemplate)
        except HPatrolError:
            return False
    return True


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Aimpoint generator for astrakhan.ru domain"
    )
    args = parser.parse_args()

    upSince = processInit.preFlightSetup()
    processInit.initSessionObject(config["sessionHeaders"])
    if not processInit.initialize():
        logger.error("Failed to initialize")
        exit(1)

    # No proxy for AWS metadata
    try:
        os.environ["no_proxy"] = f"{os.environ["no_proxy"]},169.254.169.254"
    except KeyError:
        os.environ["no_proxy"] = "169.254.169.254"

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

    execute(upSince)
    now = int(time.time())
    logger.info(f"Process clocked at {str(dt.timedelta(seconds=now-upSince))}")
    toPrint = f"Exiting Process: {threading.main_thread().name}"
    logger.info(f"= {toPrint} =")
    logger.info(f"=={'=' * len(toPrint)}==")
