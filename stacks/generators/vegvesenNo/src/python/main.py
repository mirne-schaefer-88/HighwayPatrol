"""
Module to create aimpoints

Function retrieves the main configuration file, parses it and creates subsequent JSON files

Can be run as a stand-alone python script to test
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
DOMAIN_VIDEOS = "vegvesenNoVideos"
DOMAIN_STILLS = "vegvesenNoStills"
START_URL = "http://www.vegvesen.no"


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

    try:
        wasGoodRun = False

        # Execute!
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


def execute(upSince, isScript):
    # Identify ourselves for the audit logs
    GLOBALS.taskName = "Generator"
    GLOBALS.subtaskName = "VegvesenNo"

    try:
        population = _getPopulation()
    except Exception as err:
        logger.exception(f"Error getting target list:::{err}")
        return False

    selectedCamerasFile = f"selected-{DOMAIN_VIDEOS}.json"
    try:
        videosSelection = hput.getSelection(selectedCamerasFile)
    except HPatrolError as err:
        return False

    structTitles = (
          "ID"
        , "PlaceName"
        , "Road"
        , "County"
        , "CountyNumber"
        , "Weatherforcast"
        , "Info"
        , "Mt"
        , "MeasuringStationSums"
        , "VideoFormat"
        , "VideoURL"
        , "VideoDescription"
        , "FrameRate"
        , "Longitude"
        , "Latittude"
        , "CameraStatus"
        , "CameraNumber"
        , "RoadLinkSequence"
        , "RoadLinkPosition"
        )
    structKeys = (
          "key"
        , "stedsnavn"
        , "veg"
        , "fylke"
        , "fylkesnummer"
        , "vaervarsel"
        , "info"
        , "moh"
        , "maalestasjonsnummer"
        , "videoformat"
        , "videoUrl"
        , "videobeskrivelse"
        , "bildefrekvens"
        , "lengdegrad"
        , "breddegrad"
        , "kameraStatus"
        , "kameraNummer"
        , "veglenkeSekvens"
        , "veglenkePosisjon"
        )

    stillsApTemplate = _getStillsApTemplate()
    videoApTemplate = _getVideoApTemplate()
    mtdtKeys = _getDomainFolders(videoApTemplate)

    # If running as a script, comparison is NOT done but aimpoints creation is
    # If run as a lambda, comparison *is* done, and the master file and the
    # aimpoint files are re-created as necessary
    shouldWriteAimpoints = False
    if isScript:
        shouldWriteAimpoints = True
    else:
        try:
            for mtdtKey in mtdtKeys:
                shouldWriteAimpoints = comp.writeAPs(
                    upSince,
                    population,
                    (structKeys, structTitles),
                    mtdtKey,
                    "rptVegvesenNoMasterIdList",
                    selectedList=videosSelection)
        except HPatrolError:
            logger.exception("Unable to do ID comparison")
            return False

    if shouldWriteAimpoints:
        try:
            logger.info("Processing for videos")
            _doVideos(population, videoApTemplate)

            logger.info("Processing for still images")
            _doStillCams(population, stillsApTemplate)
        except HPatrolError:
            return False

    return True


def _doVideos(allCams, apTemplate):
    selectionFile = f"selected-{DOMAIN_VIDEOS}.json"
    selection = hput.getSelection(selectionFile)
    # logger.debug(f"selection={selection}")

    theKey = f"{DOMAIN_VIDEOS}-autoParsed"
    aimpointDir = f"{GLOBALS.targetFiles}/{theKey}"
    monitoredDir = f"{GLOBALS.monitorTrgt}/{theKey}"
    GLOBALS.S3utils.deleteEntireKey(config["defaultWrkBucket"], aimpointDir)
    GLOBALS.S3utils.deleteEntireKey(config["defaultWrkBucket"], monitoredDir)

    # Keep track of how many aimpoints we actually make
    aCounter = 0
    expected = len(selection)
    logger.info(f"Creating aimpoint files on {expected} devices")
    for aCam in allCams:
        if _allVideoInputsValid(aCam):
            theID = str(aCam["key"])
            # logger.info(f"Looking at ID:{theID}")
            if theID in selection:
                try:
                    selectionState = selection[theID] if isinstance(selection[theID], str) else selection[theID]["monitoringData"]["selectionsState"]
                except KeyError as e:
                    logger.error(f"Missing key {e} for id {theID} in selections file; skipping")
                    continue
                logger.info(f"Creating JSON file for ID:{theID}")
                apTemplate["deviceID"] = theID
                apTemplate["accessUrl"] = aCam["videoUrl"]
                apTemplate["longLat"] = [aCam["lengdegrad"], aCam["breddegrad"]]
                apTemplate["bucketPrefixTemplate"] = f"nor/{theID}/{{year}}/{{month}}/{{day}}"
                if selectionState == "decoy" or selectionState == "monitor-decoy":
                    apTemplate["decoy"] = True
                else:
                    apTemplate["decoy"] = False
               # logger.debug(apTemplate)

                outFile = os.path.join(config["workDirectory"], f"{theID}.json")
                try:
                    ut.writeJsonDataToFile(apTemplate, outFile)
                except Exception as err:
                    logger.exception(f"Error creating aimpoint file:::{err}")
                    raise HPatrolError("Error creating aimpoint file")

                s3Dir = aimpointDir
                if "monitor" in selectionState:
                    s3Dir = monitoredDir

                if GLOBALS.S3utils.pushToS3(outFile,
                                        s3Dir,
                                        config["defaultWrkBucket"],
                                        s3BaseFileName=f"{theID}.json",
                                        deleteOrig=GLOBALS.onProd,
                                        extras={"ContentType": "application/json"}):
                    aCounter += 1

    if aCounter != expected:
        logger.warning(f"Created {aCounter} aimpoints out of the expected {expected}")


def _doStillCams(allCams, apTemplate):
    selectionFile = f"selected-{DOMAIN_STILLS}.json"
    selection = hput.getSelection(selectionFile)
    # logger.debug(f"selection={selection}")

    theKey = f"{DOMAIN_STILLS}-autoParsed"
    aimpointDir = f"{GLOBALS.targetFiles}/{theKey}"
    monitoredDir = f"{GLOBALS.monitorTrgt}/{theKey}"
    GLOBALS.S3utils.deleteEntireKey(config["defaultWrkBucket"], aimpointDir)
    GLOBALS.S3utils.deleteEntireKey(config["defaultWrkBucket"], monitoredDir)

    # Keep track of how many aimpoints we actually make
    aCounter = 0
    expected = len(selection)
    logger.info(f"Creating aimpoint files on {expected} devices")
    for aCam in allCams:
        if _allInputsValid(aCam):
            theID = str(aCam["key"])
            # logger.info(f"Looking at ID:{theID}")
            if theID in selection:
                try:
                    selectionState = selection[theID] if isinstance(selection[theID], str) else selection[theID]["monitoringData"]["selectionsState"]
                except KeyError as e:
                    logger.error(f"Missing key {e} for id {theID} in selections file; skipping")
                    continue
                logger.info(f"Creating JSON file for ID:{theID}")
                apTemplate["deviceID"] = theID
                apTemplate["longLat"] = [aCam["lengdegrad"], aCam["breddegrad"]]
                apTemplate["accessUrl"] = f"https://webkamera.atlas.vegvesen.no/public/kamera?id={theID}"
                apTemplate["bucketPrefixTemplate"] = f"stills/{{year}}/{{month}}/norStills{theID}"
                if selectionState == "decoy" or selectionState == "monitor-decoy":
                    apTemplate["decoy"] = True
                else:
                    apTemplate["decoy"] = False
                # logger.debug(apTemplate)
                outFile = os.path.join(config["workDirectory"], f"{theID}.json")
                try:
                    ut.writeJsonDataToFile(apTemplate, outFile)
                except Exception as err:
                    logger.exception(f"Error creating aimpoint file:::{err}")
                    raise HPatrolError("Error creating aimpoint file")

                s3Dir = aimpointDir
                if "monitor" in selectionState:
                    s3Dir = monitoredDir

                if GLOBALS.S3utils.pushToS3(outFile,
                                        s3Dir,
                                        config["defaultWrkBucket"],
                                        s3BaseFileName=f"{theID}.json",
                                        deleteOrig=GLOBALS.onProd,
                                        extras={"ContentType": "application/json"}):
                    aCounter += 1

    if aCounter != expected:
        logger.warning(f"Created {aCounter} aimpoints out of the expected {expected}")


def _getPopulation():
    # These page requests are just to simulate a human going to the site
    # No real use for this data since the data needed is in the JSON obtained later
    # This also serves for the future for if/when the site changes; so the developer sees
    # what the site looked like at this time during development
    firstUrl = START_URL
    if GLOBALS.useTestData:
        testFile = "01-FirstPage.html"
        logger.debug(f"Reading from test file '{testFile}'")
        with open(f"{GLOBALS.testResources}/{testFile}", "r") as f:
            respText = f.read()
    else:
        logger.info(f"Getting page '{firstUrl}'")
        try:
            r = GLOBALS.netUtils.get(firstUrl, headers=config["sessionHeaders"])
        except Exception:
            raise ConnectionError(f"URL access failed from {GLOBALS.perceivedIP} attempting {firstUrl}") from None
        ut.randomSleep(floor=2, ceiling=8)

    anUrl = "https://www.vegvesen.no/trafikkinformasjon/reiseinformasjon/"
    if GLOBALS.useTestData:
        testFile = "02-Menu.html"
        logger.debug(f"Reading from test file '{testFile}'")
        with open(f"{GLOBALS.testResources}/{testFile}", "r") as f:
            respText = f.read()
    else:
        logger.info(f"Getting page '{anUrl}'")
        try:
            r = GLOBALS.netUtils.get(anUrl)
        except Exception:
            raise ConnectionError(f"URL access failed from {GLOBALS.perceivedIP} attempting {anUrl}") from None
        ut.randomSleep(floor=2, ceiling=8)

    anUrl = "https://www.vegvesen.no/trafikkinformasjon/reiseinformasjon/webkamera/"
    if GLOBALS.useTestData:
        testFile = "03-Map.html"
        logger.debug(f"Reading from test file '{testFile}'")
        with open(f"{GLOBALS.testResources}/{testFile}", "r") as f:
            respText = f.read()
    else:
        logger.info(f"Getting page '{anUrl}'")
        try:
            response = GLOBALS.netUtils.get(anUrl)
        except Exception:
            raise ConnectionError(f"URL access failed from {GLOBALS.perceivedIP} attempting {anUrl}") from None

        # Make sure we request JSON response
        newHeaders = response.request.headers
        newHeaders["Accept"]="application/json"
        # logger.debug(f"newHeaders:\n{newHeaders}")
        ut.randomSleep(floor=2, ceiling=8)


    # Now get the actual data wanted
    anUrl = "https://webkamera.atlas.vegvesen.no/public/kameradata"
    if GLOBALS.useTestData:
        testFile = "norwayVegvesen.json"
        logger.debug(f"Reading from test file '{testFile}'")
        with open(f"{GLOBALS.testResources}/{testFile}", "r") as f:
            respText = f.read()
    else:
        logger.info(f"Getting page '{anUrl}'")
        try:
            r = GLOBALS.netUtils.get(anUrl, headers=newHeaders)
        except Exception:
            raise ConnectionError(f"URL access failed from {GLOBALS.perceivedIP} attempting {anUrl}") from None
        respText = r.text

    try:
        allCams = json.loads(respText)
        logger.info("Obtained camera population data")
        # logger.debug(allCams)

        dictList = []
        for cam in allCams:
            id = cam["id"]
            placeName = cam["stedsnavn"]
            road = cam["veg"]
            county = cam["fylke"]
            countyNumber = cam["fylkesnummer"]
            weatherForcast = cam["vaervarsel"]
            info = cam["info"]
            mt = cam["moh"]
            measuringStationSums = cam["maalestasjonsnummer"]
            videoFormat = cam["videoformat"]
            videoUrl = cam["videoUrl"]
            videoDescription = cam["videobeskrivelse"]
            frameRate = cam["bildefrekvens"]
            latitude = cam["breddegrad"]
            longitude = cam ["lengdegrad"]
            cameraStatus = cam["kameraStatus"]
            cameraNumber = cam["kameraNummer"]
            roadLinkSequence = cam["veglenkeSekvens"]
            roadLinkPosition = ["veglenkePosisjon"]

            camDict = {
                  "key": id
                , "stedsnavn": placeName
                , "veg": road
                , "fylke": county
                , "fylkesnummer": countyNumber
                , "vaervarsel": weatherForcast
                , "info": info
                , "moh": str(mt)
                , "maalestasjonsnummer": measuringStationSums
                , "videoformat": videoFormat
                , "videoUrl": videoUrl
                , "videobeskrivelse": videoDescription
                , "bildefrekvens": str(frameRate)
                , "lengdegrad": str(longitude)
                , "breddegrad": str(latitude) 
                , "kameraStatus": cameraStatus
                , "kameraNummer": str(cameraNumber)
                , "veglenkeSekvens": roadLinkSequence
                , "veglenkePosisjon": roadLinkPosition
            }
            
            dictList.append(camDict)

        logger.info(f"Total IDs: {len(dictList)}")
        return dictList

    except Exception:
        logger.debug(f"Content received is:\n{respText}")
        raise


def _allVideoInputsValid(camSpec):
    if camSpec["videoUrl"] == "":
        return False

    return _allInputsValid(camSpec)


def _allInputsValid(camSpec):
    if camSpec["key"] == "":
        return False
    if camSpec["lengdegrad"] == "":
        return False
    if camSpec["breddegrad"] == "":
        return False

    return True


def _getVideoApTemplate():
    videoApTemplate = {
          "deviceID": "SETLATER"
        , "collEnabled": True
        , "decoy": "SETLATER"
        , "collRegions": ["Europe (Stockholm)"]
        , "collectionType": "M3U"
        , "accessUrl": "SETLATER"
        , "pollFrequency": 20
        , "concatenate": False
        , "transcodeExt": "mp4"
        , "filenameBase": "{deviceID}"
        , "finalFileSuffix": "_{year}-{month}-{day}-{hour}-{mins}-{secs}"
        , "bucketPrefixTemplate": "nor/{deviceID}/{year}/{month}/{day}"
        , "deliveryKey": ["norData"]
        , "longLat": "SETLATER"
        , "headers": {
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:91.0) Gecko/20100101 Firefox/91.0"
            , "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
        	, "Accept-Encoding": "gzip, deflate, br"
            , "Sec-Fetch-Dest": "empty"
            , "Sec-Fetch-Mode": "cors"
            , "Connection": "keep-alive"
            , "Referer": "https://www.vegvesen.no/"
            , "DNT": "1"
            }
        , "devNotes": {
              "givenURL": "https://www.vegvesen.no"
            , "startedOn": "July 2022"
            , "missionTLDN": "no"
            , "setBy": "who originally worked it"
            }
        }
    return videoApTemplate


def _getStillsApTemplate():
    stillsApTemplate = {
          "deviceID": "SETLATER"
        , "collEnabled": True
        , "decoy": "SETLATER"
        , "collRegions": ["Europe (Stockholm)"]
        , "collectionType": "STILLS"
        , "accessUrl": "SETLATER"
        , "pollFrequency": 180
        , "filenameBase": "norStills{deviceID}"
        , "finalFileSuffix": "_{year}_{month}_{day}"
        , "bucketPrefixTemplate": "stills/{year}/{month}/norStills{deviceID}"
        , "longLat": "SETLATER"
        , "deliveryKey": "norData"
        , "headers": {
              "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:91.0) Gecko/20100101 Firefox/91.0"
            , "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
            , "Accept-Encoding": "gzip, deflate, br"
            , "Accept-Language": "en-US,en;q=0.5"
            , "Host": "webkamera.atlas.vegvesen.no"
            , "Sec-Fetch-Dest": "document"
            , "Sec-Fetch-Mode": "navigate"
            , "Sec-Fetch-Site": "none"
            , "Connection": "keep-alive"
            , "Referer": "https://www.vegvesen.no/"
            , "DNT": "1"
        }
        , "devNotes": {
              "givenURL": "https://www.vegvesen.no"
            , "startedOn": "August 2022"
            , "missionTLDN": "no"
            , "setBy": "who originally worked it"
        }
    }
    return stillsApTemplate


def _getDomainFolders(ap):
    countryCode = ap["bucketPrefixTemplate"].split("/{deviceID}")[0]
    deliveryDirs = []
    for key in ap["deliveryKey"]:
        deliveryDirs.append(f"{key}/{countryCode}")
    return deliveryDirs


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Aimpoint generator for videos",
        formatter_class=argparse.RawTextHelpFormatter
    )
    theChoices = ["stills", "videos", "both"]
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
    # This is the equivalent of doing: $ export no_proxy=169.254.169.254
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
