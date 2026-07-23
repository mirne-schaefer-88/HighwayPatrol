"""
Aimpoint generator for itsVideo
Many things in this script are hardcoded because it's for a specific domain

"""

# External libraries import statements
import os
import json
import time
import logging
import threading
import datetime as dt
from pathlib import Path


# This application's import statements
import processInit
import systemSettings
from exceptions import *
import superGlblVars as GLOBALS
from superGlblVars import config
from orangeUtils import utils as ut
from utils import hPatrolUtils as hput


logger = logging.getLogger()

# Constants
DOMAIN = "itsvideo.com"
START_URL = "https://datahub-v2-s3.arlingtonva.us/Uploads/AutomatedJobs/Traffic+Cameras.json"


def execute():
    # Identify ourselves for the audit logs
    GLOBALS.taskName = "Generator"
    GLOBALS.subtaskName = "Itsvideo"

    try:
        population = _getPopulation()
    except Exception as err:
        logger.exception(f"Error getting target list:::{err}")
        return False

    selectionsFile = f"selected-{DOMAIN}.json"
    try:
        allSelected = hput.getSelection(selectionsFile)
    except HPatrolError as err:
        # logger.error(err)
        return False

    theKey = f"taffy/{DOMAIN}-autoParsed"
    aimpointDir = f"{GLOBALS.targetFiles}/{theKey}"
    monitoredDir = f"{GLOBALS.monitorTrgt}/{theKey}"
    GLOBALS.S3utils.deleteEntireKey(config["defaultWrkBucket"], aimpointDir)
    GLOBALS.S3utils.deleteEntireKey(config["defaultWrkBucket"], monitoredDir)

    # logger.debug(f"allSelected={allSelected}") 
    apTemplate = {
          "deviceID": "SETLATER"
        , "collEnabled": True
        , "decoy": "SETLATER"
        , "collRegions": ["United States (N. Virginia)"]
        , "collectionType": "M3U"
        , "accessUrl": "SETLATER"
        , "pollFrequency": 30
        , "concatenate": False
        , "transcodeExt": None
        , "longLat": [0, 0]
        , "filenameBase": "{deviceID}"
        , "finalFileSuffix": "_{epoch}"
        , "bucketPrefixTemplate": "SETLATER"
        , "wrkBucket": "taffy-ch-prod"
        , "dstBucket": "taffy-ch-prod"
        , "headers": {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:91.0) Gecko/20100101 Firefox/91.0"
            , "Accept": "*/*"
        	, "Accept-Encoding": "gzip, deflate, br"
        	, "Accept-Language": "en-US,en;q=0.9"
            , "sec-ch-ua-mobile": "?0"
            , "Sec-Fetch-Dest": "empty"
            , "Sec-Fetch-Mode": "cors"
            , "Sec-Fetch-Site": "same-origin"
            , "Connection": "keep-alive"
            , "DNT": "1"
            }
        , "devNotes": {
              "givenURL": "Several from https://www.arlingtonva.us/Government/Programs/Transportation/Live-Traffic-Cameras"
            , "startedOn": "November 2022"
            , "missionTLDN": "taffy"
            , "setBy": "who originally worked it"
            }
        }

    for idx, aCam in enumerate(allSelected, start=1):
        try:
            selectionState = allSelected[aCam] if isinstance(allSelected[aCam], str) else allSelected[aCam]["monitoringData"]["selectionsState"]
        except KeyError as e:
            logger.error(f"Missing key {e} for id {aCam} in selections file; skipping")
            continue
        # Don't go through everything if we're not on PROD
        if not GLOBALS.onProd and idx == 5:
            logger.debug(f"Not running on PROD; exiting at device #{idx}")
            break

        # Ignore disabled IDs
        if selectionState == "off":
            continue

        if selectionState == "decoy" or selectionState == "monitor-decoy":
            apTemplate["decoy"] = True
        else:
            apTemplate["decoy"] = False

        for anEntry in population:
            if aCam == anEntry["Camera Site"]:
                if anEntry["STATUS"] != "ONLINE":
                    logger.warning(f"====> NOTE: Requested device is currently offline: {aCam}")
                    continue

                logger.info(f"Creating JSON file for ID:{aCam}")
                # Strangely the actual port numbers are +10 from those specified in the file
                portNum = int(anEntry["port"]) + 10
                apTemplate["deviceID"] = aCam
                apTemplate["accessUrl"] = f"https://itsvideo.arlingtonva.us:{portNum}/live/{aCam}.stream/playlist.m3u8"
                apTemplate["bucketPrefixTemplate"] = f"va/{aCam}/{{year}}/{{month}}/{{day}}"

                # logger.debug(apTemplate)
                outFile = os.path.join(config["workDirectory"], f"{aCam}.json")
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
                                        s3BaseFileName=f"va{aCam.capitalize()}.json",
                                        deleteOrig=GLOBALS.onProd,
                                        extras={'ContentType': 'application/json'})
    return True


def _getPopulation():
    # Get the entire population of possible devices
    anUrl = START_URL

    if GLOBALS.useTestData:
        testFile = "itsVideoPopulation.json"
        logger.debug(f"Reading from test file '{testFile}'")
        with open(f"{GLOBALS.testResources}/{testFile}", 'r', encoding="utf-8") as f:
            fileContents = f.read()
    else:
        logger.info(f"Getting page '{anUrl}'")
        try:
            r = GLOBALS.netUtils.get(anUrl, headers=config["sessionHeaders"])
        except Exception:
            raise HPatrolError(f"URL access failed from {GLOBALS.perceivedIP} attempting {anUrl}")
        fileContents = r.text

    try:
        population = json.loads(fileContents)
        logger.info("Obtained camera population data")
    except Exception:
        logger.debug(f"Content received is:\n{fileContents}")
        raise

    return population


if __name__ == "__main__":
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

    try:
        wasGoodRun = execute()
    except Exception as err:
        logger.exception(err)

    nownow = int(time.time())
    logger.info(f"Process clocked at {str(dt.timedelta(seconds=nownow-upSince))}")

    toPrint = f"Exiting Process: {threading.main_thread().name}"
    logger.info(f"= {toPrint} =")
    logger.info(f"=={'=' * len(toPrint)}==")
