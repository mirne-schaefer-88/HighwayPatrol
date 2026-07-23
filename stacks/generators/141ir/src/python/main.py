"""
Module to create the JSON aimpoints

The aimpoint needs the following:
    "accessUrl": <read tsv file, grab camera links (for different angles)>
    "longLat": <read tsv file and write>

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
DOMAIN = "141.ir"
START_URL = "https://api.141.ir/api/map_services/cameras"


def _getPopulation(url):
    logger.info("Getting target population")

    if GLOBALS.useTestData:
        testFile = "allIodineCameras.json"
        logger.debug(f"Reading from test file '{testFile}'")
        with open(f"{GLOBALS.testResources}/{testFile}", 'r') as f:
            camsDict = json.load(f)

    else:
        response = GLOBALS.netUtils.get(url)
        camsDict = response.json()

    try:
        allCams = camsDict["data"]
    except KeyError as err:
        logger.error(f"Element '{err}' missing in received data")
        logger.debug(f"Content received is:\n{response.text}")
        raise HPatrolError("Unexpected data received") from None
    except TypeError as err:
        logger.error(f"ERROR: '{err}'")
        logger.debug(f"Content received is:\n{response.text}")
        raise HPatrolError("Unexpected data received") from None

    logger.info(f"Cameras available to query: {len(allCams)}")
    # Some cams have newline and/or carriage return chars in them, which breaks the comparitor if left in
    allCams = [{"id":x[0], "lat":x[1].replace('\n', '').replace('\r', ''), "lon":x[2].replace('\n', '').replace('\r', '')} for x in allCams]

    if not allCams:
        logger.error("Could not grab all cameras available!")
        raise HPatrolError("No cameras available found")
    # logger.info(f"POPULATION: {allCams}")

    return allCams


def execute(upSince, event):
    # Identify ourselves for the audit logs
    GLOBALS.taskName = "Generator"
    GLOBALS.subtaskName = "141ir"

    selectionsFile = f"selected-{DOMAIN}.json"
    populationUrl = START_URL

    # If the generator is running NOT in WH, only create
    # aimpoints for post-processing and then return
    if event["envCmd"] == "postProcessings":
        postApTemplate = hput.getPostApTemplate(_getApTemplate(), "flynnl/mission_data/hp/stillsLz")
        try:
            selection = hput.getSelection(selectionsFile, includeOff=True)
            _doPostStillCams(selection, postApTemplate)
        except HPatrolError as err:
            # logger.error(err)
            return False
        return True

    # If the generator is running in WH, reach out to the web
    # to get population data and run the comparitor
    elif event["envCmd"] == "forwardDeployed":
        collApTemplate = _getCollApTemplate()
        try:
            population = _getPopulation(populationUrl)
        except HPatrolError:
            return False

        structTitles = (
              "ID"
            , "Latitude"
            , "Longitude"
            )
        structKeys = (
              "id"
            , "lat"
            , "lon"
            )
        domainFolder = comp.getDomainFolder(collApTemplate)
        try:
            selection = hput.getSelection(selectionsFile)
            # In forward deployed envs, selections file needs to be updated to prevent deletion
            hput.refreshSelectionsTimestamp(selectionsFile, upSince)
            throwAway = comp.writeAPs(
                upSince,
                population,
                (structKeys, structTitles),
                domainFolder,
                "rpt141irMasterIdList",
                selectedList=selection)
        except HPatrolError as err:
            # logger.error(err)
            logger.exception("Unable to do ID comparison")
            return False

        # Always re-write aimpoints to prevent them from being deleted in Vortex
        try:
            _doStillCams(population, selection, collApTemplate)
        except HPatrolError as err:
            # logger.error(err)
            return False

        return True

    # Default else
    # Should never get here, but just in case
    logger.error("Action not selected")
    return False


def _allInputsValid(camSpec):
    if camSpec["id"] == "":
        return False
    if camSpec["lat"] == "":
        return False
    if camSpec["lon"] == "":
        return False

    return True


def _doPostStillCams(selection, apTemplate):
    theKey = f"{DOMAIN}-autoParsed"
    aimpointDir = f"{GLOBALS.targetFiles}/{theKey}"
    monitoredDir = f"{GLOBALS.monitorTrgt}/{theKey}"
    GLOBALS.S3utils.deleteEntireKey(config["defaultWrkBucket"], aimpointDir)
    GLOBALS.S3utils.deleteEntireKey(config["defaultWrkBucket"], monitoredDir)

    counter = 1
    for deviceID in selection:
        try:
            selectionState = selection[deviceID] if isinstance(selection[deviceID], str) else selection[deviceID]["monitoringData"]["selectionsState"]
        except KeyError as e:
            logger.error(f"Missing key {e} for id {deviceID} in selections file; skipping")
            continue
        # Don't go through everything if we're not on PROD
        if not GLOBALS.onProd and counter == 5:
            logger.debug(f"Not running on PROD; exiting at device #{counter}")
            break

        logger.info(f"Creating JSON file for ID:{deviceID}")
        apTemplate["deviceID"] = deviceID
        if selectionState == "decoy" or selectionState == "monitor-decoy":
            apTemplate["decoy"] = True
        else:
            apTemplate["decoy"] = False

        # Handle individual aimpoint tweaks
        newAimpoint = hput.mergeSelections(
            selected=selection[deviceID], baseTemplate=apTemplate
        )

        outFile = os.path.join(config["workDirectory"], f"{deviceID}.json")

        try:
            ut.writeJsonDataToFile(newAimpoint, outFile)
        except Exception as err:
            logger.exception(f"Error creating aimpoint file:::{err}")
            raise HPatrolError("Error creating aimpoint file")

        s3Dir = aimpointDir
        if "monitor" in selectionState:
            s3Dir = monitoredDir
        GLOBALS.S3utils.pushToS3(outFile,
                                s3Dir,
                                config["defaultWrkBucket"],
                                s3BaseFileName=f"{deviceID}.json",
                                deleteOrig=GLOBALS.onProd,
                                extras={"ContentType": "application/json"})
        counter += 1


def _doStillCams(allCams, selection, apTemplate):
    theKey = f"{DOMAIN}-autoParsed"
    aimpointDir = f"{GLOBALS.targetFiles}/{theKey}"
    monitoredDir = f"{GLOBALS.monitorTrgt}/{theKey}"
    GLOBALS.S3utils.deleteEntireKey(config["defaultWrkBucket"], aimpointDir)
    GLOBALS.S3utils.deleteEntireKey(config["defaultWrkBucket"], monitoredDir)

    counter = 1
    for aCam in allCams:
        # Don't go through everything if we're not on PROD
        if not GLOBALS.onProd and counter == 5:
            logger.debug(f"Not running on PROD; exiting at device #{counter}")
            break

        if _allInputsValid(aCam):
            cameraId = str(aCam["id"])
            deviceID = cameraId.zfill(5)

            if deviceID in selection:
                try:
                    selectionState = selection[deviceID] if isinstance(selection[deviceID], str) else selection[deviceID]["monitoringData"]["selectionsState"]
                except KeyError as e:
                    logger.error(f"Missing key {e} for id {deviceID} in selections file; skipping")
                    continue
                url = f"{START_URL}/{cameraId}"
                logger.info(f"Creating JSON file for ID:{deviceID}")
                apTemplate["deviceID"] = deviceID
                apTemplate["longLat"] = [float(aCam["lon"]), float(aCam["lat"])]
                apTemplate["accessUrl"] = url
                apTemplate["proxy"] = config["proxy"]
                if selectionState == "decoy" or selectionState == "monitor-decoy":
                    apTemplate["decoy"] = True
                else:
                    apTemplate["decoy"] = False

                # Handle individual aimpoint tweaks
                newAimpoint = hput.mergeSelections(
                    selected=selection[deviceID], baseTemplate=apTemplate
                )

                outFile = os.path.join(config["workDirectory"], f"{deviceID}.json")

                try:
                    ut.writeJsonDataToFile(newAimpoint, outFile)
                except Exception as err:
                    logger.exception(f"Error creating aimpoint file:::{err}")
                    raise HPatrolError("Error creating aimpoint file")

                s3Dir = aimpointDir
                if "monitor" in selectionState:
                    s3Dir = monitoredDir
                throwAway = GLOBALS.S3utils.pushToS3(outFile,
                                        s3Dir,
                                        config["defaultWrkBucket"],
                                        s3BaseFileName=f"{deviceID}.json",
                                        deleteOrig=GLOBALS.onProd,
                                        extras={"ContentType": "application/json"})
                counter += 1


def _getApTemplate():
    apTemplate = {
          "deviceID": "SETLATER"
        , "collEnabled": "SETLATER"
        , "decoy": "SETLATER"
        , "collRegions": ["us-east-1"]
        , "collectionType": "ISTLLS"
        , "accessUrl": "SETLATER"
        , "longLat": "SETLATER"
        , "pollFrequency": 120
        , "filenameBase": "141ir-{deviceID}"
        , "finalFileSuffix": "_{year}{month}{day}"
        , "bucketPrefixTemplate": "ir/141ir/{deviceID}/{year}/{month}"
        , "headers": {
              "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:102.0) Gecko/20100101 Firefox/102.0"
            , "Accept": "*/*"
            , "Accept-Language": "en-US,en;q=0.5"
            , "Accept-Encoding": "gzip, deflate, br"
            , "DNT": "1"
            , "Connection": "keep-alive"
            , "Host": "141.ir"
            , "Sec-Fetch-Dest": "empty"
            , "Sec-Fetch-Mode": "cors"
            , "Sec-Fetch-Site": "same-origin"
            , "Referer": "https://141.ir/"
        }
        , "devNotes": {
              "givenURL": "https://141.ir/cameras"
            , "startedOn": "February 2025 on HP; overall 2014 under task Iodine"
            , "missionTLDN": "ir"
            , "setBy": "who originally worked it"
            }
    }
    return apTemplate


def _getCollApTemplate():
    baseTemplate = _getApTemplate()
    collTemplate = {
          "collEnabled": True
        , "accessUrl": "SETLATER"
        , "proxy": "SETLATER"
        , "longLat": "SETLATER"
        , "monitoringData": {
            "monitorFrequency": 2
        }
    }

    apTemplate = {**baseTemplate, **collTemplate}
    return apTemplate


def _getProxyUrl() -> str:
    ssmUtils = SSMutils()
    params = ssmUtils.getParameterValues(prefix="/wormhole/proxies/commercial_proxy_")
    # Can't use GLOBALS.onProd here because it hasn't been set yet (it's set on processInit.initialize())
    if config["mode"] == "prod":
        # This proxy must ONLY be used in the Vortex environment!!!
        proxyUrl = params["/wormhole/proxies/commercial_proxy_url_template"].format(
            params["/wormhole/proxies/commercial_proxy_credentials"],
            "ir",
            "",
            params["/wormhole/proxies/commercial_proxy_server"],
            params["/wormhole/proxies/commercial_proxy_port"]
        ).replace("_city-", "") # Use random city
        proxy = "http://" + proxyUrl
    else:
        logger.warning("System mode is not 'PROD', proxy will not be added to aimpoints")
        proxy = ""
    return proxy


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

    # Test input correctness
    try:
        test = event["envCmd"]
    except KeyError:
        logger.error("Invalid message received")
        logger.debug(f"Message received is:{event}")
        return {"status": False}

    try:
        wasGoodRun = False

        # Execute!
        if execute(upSince, event):
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
    logger.info(f"=={"=" * len(toPrint)}==")

    return {"status": wasGoodRun}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="141ir Generator\n"\
            "\thole=some environment\n"\
            "\there=main environment",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("task",
                        help="type of aimpoint to create",
                        choices=["hole", "here"],
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

    task= {}
    if args.task == "hole":
        task["envCmd"] = "forwardDeployed"
    if args.task == "here":
        task["envCmd"] = "postProcessings"

    execute(upSince, task)

    nownow = int(time.time())
    logger.info(f"Process clocked at {str(dt.timedelta(seconds=nownow-upSince))}")

    toPrint = f"Exiting Process: {threading.main_thread().name}"
    logger.info(f"= {toPrint} =")
    logger.info(f"=={'=' * len(toPrint)}==")
