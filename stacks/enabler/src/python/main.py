# External libraries import statements
import os
import time
import json
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
from orangeUtils import auditUtils
from orangeUtils import utils as ut
from utils import hPatrolUtils as hput
from orangeUtils import timeUtils as tu
from orangeUtils.auditUtils import AuditLogLevel


logger = logging.getLogger()


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
        # Execute!
        wasGoodRun = True
        aimpointsProcessed = execute(upSince)
        exitMessage = "Normal execution"

    except Exception as e:
        logger.exception(f"UNHANDLED EXCEPTION CAUGHT:::{e}")
        systemLevel = AuditLogLevel.CRITICAL
        dataLevel = None
        wasGoodRun = False
        aimpointsProcessed = None

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
            aimpointsProcessed=aimpointsProcessed
            )

    toPrint = "Exiting Process"
    logger.info(f"= {toPrint} =")
    logger.info(f"=={'=' * len(toPrint)}==")

    return {"status": wasGoodRun}


def execute(timestamp: int):
    # Identify ourselves for the audit logs
    GLOBALS.taskName = "Enabler"

    # Select aimpoints that are currently being monitored
    s3Dir = GLOBALS.monitorTrgt
    logger.info(f"Looking for files in S3: '{s3Dir}/'")
    fileList = GLOBALS.S3utils.getFilesAsStrList(config["defaultWrkBucket"], s3Dir)
    try:
        logger.info(f"Total aimpoints being monitored found:{len(fileList)}")
    except TypeError:
        return 0

    for idx, aFile in enumerate(fileList, start=1):
        if not GLOBALS.onProd and idx == 2:
            logger.debug(f"Not running on PROD; exiting before processing file #{idx}")
            break

        logger.info(f"Processing file '{aFile}'")
        contents = GLOBALS.S3utils.readFileContent(config["defaultWrkBucket"], aFile)
        try:
            ap = json.loads(contents)
        except Exception as e:
            logger.warning(f"Error reading contents of '{aFile}'; skipping:::{e}")
            continue

        try:
            if not ap["collEnabled"]:
                logger.info("Aimpoint disabled; skipping")
                continue
        except KeyError:
            pass

        logger.info(f"Checking collection results for {aFile}")
        if _shouldBeEnabled(ap, timestamp):
            logger.info(f"Successful collection found; moving '{aFile}' to active")
            try:
                _enableAimpoint(aFile, ap)
            except HPatrolError as err:
                logger.error(f"Unexpected error copying '{aFile}' to active:::{err}")
                continue

            # Modify the selection file if it exists
            if "-autoParsed" in aFile:
                domainName = os.path.basename(os.path.dirname(aFile)).removesuffix("-autoParsed")
                selectedFileName = f"selected-{domainName}.json"
                selectedFileKey = f"{GLOBALS.selectTrgts}/{selectedFileName}"
                if GLOBALS.S3utils.isFileInS3(config["defaultWrkBucket"], selectedFileKey):
                    try:
                        _enableSelectedDevice(selectedFileKey, ap["deviceID"])
                    except (HPatrolError, KeyError):
                        logger.error("Failed to update selections file")
    return len(fileList)


def _enableAimpoint(monitoredKey: str, ap: dict):
    aimpointKey = monitoredKey.replace(GLOBALS.monitorTrgt, GLOBALS.targetFiles)

    try:
        # If monitoringData doesn't exist, ignore
        # Otherwise, remove the other properties if they exist
        ap["monitoringData"].pop("movedToMonitored", None)
        ap["monitoringData"].pop("lastMonitored", None)
        ap["monitoringData"].pop("lastMonitoredIsoDate", None)
    except KeyError:
        pass
    
    baseFileName = os.path.basename(aimpointKey)
    tmpFile = os.path.join(config["workDirectory"], baseFileName)
    ut.writeJsonDataToFile(ap, tmpFile)
    isPushed = GLOBALS.S3utils.pushToS3(
                tmpFile, 
                os.path.dirname(aimpointKey), 
                config["defaultWrkBucket"], 
                deleteOrig=GLOBALS.onProd, 
                s3BaseFileName=baseFileName,
                extras={"ContentType": "application/json"})
    if not isPushed:
        raise HPatrolError(f"Error pushing '{aimpointKey}' to S3")
    if not GLOBALS.S3utils.deleteFileInS3(config["defaultWrkBucket"], monitoredKey):
        raise HPatrolError(f"Failed to delete from S3: {monitoredKey}")


def _enableSelectedDevice(selectedFileKey: str, deviceID: str):
    """
    Read selected file, get object etag, update the JSON, then conditionally push to S3.
    If the etags do not match, likely due to a race condition, then retry (with a new etag)
    and attempt conditional push to S3 again. Will attempt GLOBALS.s3ConditionalRetries times.
    """

    logger.info(f"File found in selections directory, updating '{selectedFileKey}'")
    attempt = 0
    sleepyTime = 2
    justTheName = os.path.basename(selectedFileKey)
    toggleValues = {
        "monitor": "on",
        "monitor-mp4": "mp4",
        "monitor-decoy": "decoy"
    }
    while attempt < GLOBALS.s3ConditionalRetries:
        selectionsStr, etag = GLOBALS.S3utils.getFileAndEtag(config["defaultWrkBucket"], selectedFileKey)
        try:
            selectionsDict = json.loads(selectionsStr)
        except json.decoder.JSONDecodeError as err:
            logger.error(f"Unable to parse response: {err}")
            logger.info("Check if file is zero-bytes")
            raise HPatrolError("Response text is not JSON")
        try:
            selections = selectionsDict["selections"]
            if isinstance(selections[deviceID], dict):
                selectionsState = selections[deviceID]["monitoringData"]["selectionsState"]
                selections[deviceID]["monitoringData"]["selectionsState"] = toggleValues[selectionsState]
            else:
                selections[deviceID] = toggleValues[selections[deviceID]]
        except KeyError as key:
            logger.error(f"Key {key} not found; selections file or deviceID should be updated")
            raise

        body = json.dumps(selectionsDict)
        logger.info(f"Pushing updated selections file '{justTheName}' to S3")
        pushedToS3 = GLOBALS.S3utils.pushDataIfEtagsMatch(
                    config["defaultWrkBucket"],
                    selectedFileKey,
                    body,
                    etag,
                    contentType="application/json")  
        if pushedToS3:
            return

        logger.warning(f"Failed to update selections file '{justTheName}', retrying after {sleepyTime}s")
        attempt += 1
        time.sleep(sleepyTime)

    logger.error(f"Max retries exceeded attempting to update '{justTheName}'")
    raise HPatrolError("Max retries exceeded")


def _shouldBeEnabled(ap: dict, timestamp: int) -> bool:
    # Put the aimpoint back on collection if any recent collections were successful
    filenameBase = hput.formatNameBase(ap["filenameBase"], ap["deviceID"])
    filePrefix = f"{GLOBALS.aimpointSts}/{filenameBase}"
    lookBack = timestamp - GLOBALS.enablerLookBack
    year, month, day, hour, mins, secs = tu.returnYMDHMS(lookBack)
    startAfterPrefix = f"{filePrefix}/{year}{month}{day}{hour}{mins}{secs}"

    # Most aimpoints don't have GLOBALS.collResultLimit number of collection results in a 30 minute period,
    # however, some do so the limit is set here to avoid timeouts when reading from aimpointStatus/
    collectionResults = GLOBALS.S3utils.getFilesAsStrList(config["defaultWrkBucket"], filePrefix, limit=GLOBALS.collResultLimit, startAfter=startAfterPrefix)
    if not collectionResults:
        # logger.info(f"No collection results for {filePrefix} in the past {GLOBALS.enablerLookBack} seconds")
        return False
    for result in collectionResults:
        if "success" in result:
            return True
    return False


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

    try:
        execute(upSince)
    except HPatrolError as err:
        logger.info(f"Caught exception: {err}")

    nownow = int(time.time())
    logger.info(f"Process clocked at {str(dt.timedelta(seconds=nownow-upSince))}")

    toPrint = f"Exiting Process: {threading.main_thread().name}"
    logger.info(f"= {toPrint} =")
    logger.info(f"=={'=' * len(toPrint)}==")
