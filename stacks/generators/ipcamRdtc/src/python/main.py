"""
Aimpoint generator
Many things are hardcoded because this handles a specific domain

"""

# External libraries import statements
import os
import time
import logging
import argparse
import datetime
import xmltodict
import threading
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
DOMAIN = "rdtc"
START_URL = "https://www.city-n.ru/tpl/cam_list.xml?{epoch}"


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
        logger.info(f"Process clocked at {str(datetime.timedelta(seconds=nownow-upSince))}")
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
            enterDatetime=datetime.datetime.fromtimestamp(upSince),
            leaveDatetime=datetime.datetime.fromtimestamp(nownow)
        )

    toPrint = "Exiting Process"
    logger.info(f"= {toPrint} =")
    logger.info(f"=={'=' * len(toPrint)}==")

    return {"status": wasGoodRun}


def _getPopulation():
    logger.info("Getting target population")

    if GLOBALS.useTestData:
        testFile = "rdtc.xml"
        logger.debug(f"Reading from test file '{testFile}'")
        with open(f"{GLOBALS.testResources}/{testFile}", "r") as f:
            response = f.read()
    else:
        try:
            currentEpoch = int(time.time() * 1000)
            startUrl = START_URL.format(epoch=currentEpoch)
            response = GLOBALS.netUtils.get(startUrl)
            response = response.text
        except Exception:
            logger.warning(f"URL access to {startUrl} failed from: {GLOBALS.perceivedIP}")
            raise HPatrolError("URL access failed")
    camsData = xmltodict.parse(response, attr_prefix='')

    try:
        allCams = camsData["cam_list"]["cam"]
    except KeyError as k:
        logger.warning(f"Camera list not found; missing key '{k}'")
        raise HPatrolError("Camera list not found")
    if not allCams:
        logger.warning(f"Empty list: no cameras found from {startUrl}")
        raise HPatrolError("No cameras found")

    return allCams


def _doVideos(allCams, selection, apTemplate):
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

        camId = str(cam["cam_id"])
        if camId in selection:
            try:
                selectionState = selection[camId] if isinstance(selection[camId], str) else selection[camId]["monitoringData"]["selectionsState"]
            except KeyError as e:
                logger.error(f"Missing key {e} for id {camId} in selections file; skipping")
                continue
            if selection[camId] == "off":
                continue

            logger.info(f"Creating JSON file for ID: {camId}")
            apTemplate["deviceID"] = camId
            apTemplate["longLat"] = [float(cam["gis_x"]), float(cam["gis_y"])]

            if selectionState == "decoy" or selectionState == "monitor-decoy":
                apTemplate["decoy"] = True
            else:
                apTemplate["decoy"] = False

            outFile = os.path.join(config["workDirectory"], f"{camId}.json")
            try:
                ut.writeJsonDataToFile(apTemplate, outFile)
            except Exception as err:
                logger.exception(f"Error creating aimpoint file:::{err}")
                continue

            s3Dir = aimpointDir
            if "monitor" in selectionState:
                s3Dir = monitoredDir

            GLOBALS.S3utils.pushToS3(
                outFile,
                s3Dir,
                config["defaultWrkBucket"],
                deleteOrig=GLOBALS.onProd,
                s3BaseFileName=f"{camId}.json",
                extras={"ContentType": "application/json"}
            )
            counter += 1


def execute(upSince: int) -> bool:
    GLOBALS.taskName = "Generator"
    GLOBALS.subtaskName = "IpcamRdtc"
    try:
        cameraPop = _getPopulation()
    except HPatrolError as err:
        logger.exception(f"Error getting target population:::{err}")
        return False
    selectionsFile = f"selected-{DOMAIN}.json"
    try:
        selection = hput.getSelection(selectionsFile)
    except HPatrolError:
        return False
    
    structTitles = ("Title"
                    , "CamId"
                    , "PosX"
                    , "PosY"
                    , "Longitude"
                    , "Latitude"
                    , "Angle"
                    , "Dimension"
                    , "Source"
                    , "PicUrl")
    structKeys = ("title"
                  , "cam_id"
                  , "pos_x"
                  , "pos_y"
                  , "gis_x"
                  , "gis_y"
                  , "angle"
                  , "dimension"
                  , "source"
                  , "pic_url")
    apTemplate = _getApTemplate()
    domainFolder = comp.getDomainFolder(apTemplate)
    try:
        comp.writeAPs(
            upSince,
            cameraPop,
            (structKeys, structTitles),
            domainFolder,
            "rptRdtcMasterIdList",
            selectedList=selection
        )
    except HPatrolError:
        logger.exception("Unable to do ID comparison")
    try:
        _doVideos(cameraPop, selection, apTemplate)
    except HPatrolError:
        return False
    return True


def _getApTemplate():
    apTemplate = {
          "deviceID": "SETLATER"
        , "collEnabled": True
        , "decoy": "SETLATER"
        , "collRegions": ["Europe (Frankfurt)"]
        , "collectionType": "RDTC"
        , "accessUrl": "https://www.city-n.ru"
        , "pollFrequency": 20
        , "concatenate": False
        , "transcodeExt": "mp4"
        , "longLat": "SETLATER"
        , "filenameBase": "rdtc{deviceID}"
        , "finalFileSuffix": "_{year}-{month}-{day}-{hour}-{mins}-{secs}"
        , "bucketPrefixTemplate": "ru/ipcamRdtc/rdtc{deviceID}/{year}/{month}/{day}"
        , "headers": {
              "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0"
            , "Accept": "*/*"
            , "Accept-Language": "en-US,en;q=0.5"
            , "Host": "ipcam.rdtc.ru"
            , "Connection": "keep-alive"
            , "Sec-Fetch-Dest": "empty"
            , "Sec-Fetch-Mode": "cors"
            , "Sec-Fetch-Site": "same-origin"
            , "Accept-Encoding": "gzip, deflate, br, zstd"
            , "DNT": "1"
            , "Sec-GPC": "1"
            }
        , "devNotes": {
              "givenUrl": "https://city-n.ru/road_cam.html"
            , "startedOn": "June 6, 2023"
            , "missionTLDN": "ru"
            , "setBy": "who originally worked it"
        }
    }
    return apTemplate


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Aimpoint generator for ipcam.rdtc domain"
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
    logger.info(f"Process clocked at {str(datetime.timedelta(seconds=now-upSince))}")
    toPrint = f"Exiting Process: {threading.main_thread().name}"
    logger.info(f"= {toPrint} =")
    logger.info(f"=={'=' * len(toPrint)}==")
