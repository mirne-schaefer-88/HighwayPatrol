"""
Module to create the JSON aimpoints.

Can be run as a stand-alone python script to test
"""

# External libraries import statements
import os
import time
import logging
import threading
import xmltodict
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
DOMAIN = "traffic.td.gov.hk"
START_URL = "https://static.data.gov.hk/td/traffic-snapshot-images/code/Traffic_Camera_Locations_En.xml"


def _getPopulation(url):
    logger.info("Getting target population")

    if GLOBALS.useTestData:
        testFile = "Traffic_Camera_Locations_En_20230315.xml"
        logger.debug(f"Reading from test file '{testFile}'")
        camsFile = f"{GLOBALS.testResources}/{testFile}"

    else:
        filename = GLOBALS.netUtils.downloadFile(url)
        camsFile = f"{config["workDirectory"]}/{filename}"

    xmlFile = open(camsFile, 'r').read()
    dataDict = xmltodict.parse(xmlFile)
    allCams = dataDict["image-list"]["image"]

    logger.info(f"Cameras available to query: {len(allCams)}")

    if not allCams:
        logger.error("Could not grab all cameras available!")
        raise HPatrolError("No cameras available found")

    # logger.info(f"POPULATION: {allCams}")
    return allCams


def execute(upSince):
    # Identify ourselves for the audit logs
    GLOBALS.taskName = "Generator"
    GLOBALS.subtaskName = "TdGovHk"

    selectionsFile = f"selected-{DOMAIN}.json"
    populationUrl = START_URL

    try:
        population = _getPopulation(populationUrl)
    except Exception as err:
        logger.exception(f"Error getting target list:::{err}")
        return False

    try:
        selection = hput.getSelection(selectionsFile)
        # logger.debug(f"selection={selection}")
    except HPatrolError as err:
        # logger.error(err)
        return False

    structTitles = (
          "ID"
        , "ImageURL"
        , "Region"
        , "District"
        , "Longitude"
        , "Latitude"
        , "Description"
        )
    structKeys = (
          "key"
        , "url"
        , "region"
        , "district"
        , "longitude"
        , "latitude"
        , "description"
        )

    apTemplate = _getApTemplate()
    try:
        # TODO: Separate report creation from writing aimpoint decision
        #       shouldWriteAimpoints should be picked only once
        for mtdtKey in apTemplate["deliveryKey"]:
            shouldWriteAimpoints = comp.writeAPs(
                upSince,
                population,
                (structKeys, structTitles),
                mtdtKey,
                "rptXenonMasterIdList",
                selectedList=selection)
    except HPatrolError:
        logger.exception("Unable to do ID comparison")
        return False

    if shouldWriteAimpoints:
        try:
            _doStillCams(population, selection, apTemplate)
        except HPatrolError as err:
            # logger.error(err)
            return False

    return True


def _allInputsValid(camSpec):
    if camSpec["key"] == "":
        return False
    if camSpec["region"] == "":
        return False
    if camSpec["district"] == "":
        return False
    if camSpec["easting"] == "":
        return False
    if camSpec["northing"] == "":
        return False
    if camSpec["latitude"] == "":
        return False
    if camSpec["longitude"] == "":
        return False
    if camSpec["url"] == "":
        return False

    return True


def _doStillCams(allCams, selection, apTemplate):
    theKey = f"{DOMAIN}-autoParsed"
    aimpointDir = f"{GLOBALS.targetFiles}/{theKey}"
    monitoredDir = f"{GLOBALS.monitorTrgt}/{theKey}"
    GLOBALS.S3utils.deleteEntireKey(config["defaultWrkBucket"], aimpointDir)
    GLOBALS.S3utils.deleteEntireKey(config["defaultWrkBucket"], monitoredDir)

    # Loop through the cams
    for aCam in allCams:
        if _allInputsValid(aCam):
            theID = str(aCam["key"])
            if theID in selection:
                try:
                    selectionState = selection[theID] if isinstance(selection[theID], str) else selection[theID]["monitoringData"]["selectionsState"]
                except KeyError as e:
                    logger.error(f"Missing key {e} for id {theID} in selections file; skipping")
                    continue
                logger.info(f"Creating JSON file for ID:{theID}")
                apTemplate["deviceID"] = theID
                apTemplate["longLat"] = [float(aCam["longitude"]), float(aCam["latitude"])]
                apTemplate["accessUrl"] = aCam["url"]
                if selectionState == "decoy" or selectionState == "monitor-decoy":
                    apTemplate["decoy"] = True
                else:
                    apTemplate["decoy"] = False
                outFile = os.path.join(config["workDirectory"], f"{theID}.json")
                try:
                    ut.writeJsonDataToFile(apTemplate, outFile)
                except Exception as err:
                    logger.exception(f"Error creating aimpoint file:::{err}")
                    raise HPatrolError("Error creating aimpoint file")

                s3Dir = aimpointDir
                if "monitor" in selectionState:
                    s3Dir = monitoredDir

                result = GLOBALS.S3utils.pushToS3(outFile,
                                        s3Dir,
                                        config["defaultWrkBucket"],
                                        s3BaseFileName=f"{theID}.json",
                                        deleteOrig=GLOBALS.onProd,
                                        extras={"ContentType": "application/json"})


def _getApTemplate():
    apTemplate = {
          "deviceID": "SETLATER"
        , "collEnabled": True
        , "decoy": "SETLATER"
        , "collRegions": ["Asia Pacific (Seoul)"]
        , "collectionType": "STILLS"
        , "accessUrl": "SETLATER"
        , "pollFrequency": 40
        , "filenameBase": "xenon{deviceID}"
        , "finalFileSuffix": "_{year}_{month}_{day}"
        , "bucketPrefixTemplate": "{year}/{month}/{day}"
        , "deliveryKey": ["xenon"]
        , "longLat": "SETLATER"
        , "headers": {
              "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:91.0) Gecko/20100101 Firefox/91.0"
            , "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
            , "Accept-Encoding": "gzip, deflate, br"
            , "Accept-Language": "en-US,en;q=0.5"
            , "Host": "traffic.td.gov.hk"
            , "Sec-Fetch-Dest": "document"
            , "Sec-Fetch-Mode": "navigate"
            , "Sec-Fetch-Site": "none"
            , "Connection": "keep-alive"
            , "DNT": "1"
        }
        , "devNotes": {
              "givenURL": "https://traffic.td.gov.hk/"
            , "startedOn": "Dec 2023 on HP; overall Oct 1st, 2021 under task Xenon"
            , "missionTLDN": "hk"
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

    try:
        wasGoodRun = False

        # Execute!
        if execute(upSince):
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

    execute(upSince)

    nownow = int(time.time())
    logger.info(f"Process clocked at {str(dt.timedelta(seconds=nownow-upSince))}")

    toPrint = f"Exiting Process: {threading.main_thread().name}"
    logger.info(f"= {toPrint} =")
    logger.info(f"=={'=' * len(toPrint)}==")
