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
    GLOBALS.taskName = "Disabler"

    # Select aimpoints that are on collection
    s3Dir = GLOBALS.targetFiles
    logger.info(f"Looking for files in S3: '{s3Dir}/'")
    fileList = GLOBALS.S3utils.getFilesAsStrList(config["defaultWrkBucket"], s3Dir)

    try:
        logger.info(f"Total aimpoints found:{len(fileList)}")
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
            logger.warning(f"Error reading content of file {aFile}; skipping:::{e}")
            continue

        try:
            if not ap["collEnabled"]:
                logger.info("Aimpoint disabled; skipping")
                continue
        except KeyError:
            pass

        if _shouldDisable(ap, timestamp):
            logger.info(f"Aimpoint {aFile} failed to collect for 30 minutes; switching to monitored status")
            try:
                _disableAimpoint(aFile, ap, timestamp)
            except HPatrolError as e:
                logger.error(f"Unexpected error copying '{aFile}' to monitored:::{e}")
                continue

            # Modify the selection file if it exists
            if "-autoParsed" in aFile:
                domainName = os.path.basename(os.path.dirname(aFile)).removesuffix("-autoParsed")
                selectedFileName = f"selected-{domainName}.json"
                selectedFileKey = f"{GLOBALS.selectTrgts}/{selectedFileName}"
                if GLOBALS.S3utils.isFileInS3(config["defaultWrkBucket"], selectedFileKey):
                    try:
                        _disableSelectedDevice(selectedFileKey, ap["deviceID"])
                    except (HPatrolError, KeyError):
                        logger.error("Failed to update selections file")
    return len(fileList)


def _disableAimpoint(aimpointKey: str, ap: dict, timestamp: int):
    monitoredKey = aimpointKey.replace(GLOBALS.targetFiles, GLOBALS.monitorTrgt)

    # Update aimpoint with moved-to-monitoring timestamp
    dtObj = dt.datetime.fromtimestamp(timestamp, dt.UTC)
    # If monitoringData hasn't been set yet, create it
    ap.setdefault("monitoringData", {})["movedToMonitored"] = dtObj.isoformat()

    baseFileName = os.path.basename(monitoredKey)
    tmpFile = os.path.join(config["workDirectory"], baseFileName)
    ut.writeJsonDataToFile(ap, tmpFile)
    if not GLOBALS.S3utils.pushToS3(
                tmpFile, 
                os.path.dirname(monitoredKey), 
                config["defaultWrkBucket"], 
                deleteOrig=GLOBALS.onProd, 
                s3BaseFileName=baseFileName,
                extras={"ContentType": "application/json"}):
        raise HPatrolError(f"Error pushing '{monitoredKey}' to S3")
    if not GLOBALS.S3utils.deleteFileInS3(config["defaultWrkBucket"], aimpointKey):
        raise HPatrolError(f"Failed to delete from S3: {aimpointKey}")


def _disableSelectedDevice(selectedFileKey: str, deviceID: str):
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
        "on": "monitor",
        "mp4": "monitor-mp4",
        "decoy": "monitor-decoy"
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
        logger.info(f"Pushing updated selections file {justTheName} to S3")
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

    raise HPatrolError(f"Max retries exceeded attempting to update '{justTheName}'")


def _shouldDisable(ap: dict, timestamp: int) -> bool:
    # Set aimpoint to monitor
    # if the last disablerLookBack seconds of collection is all failures
    filenameBase = hput.formatNameBase(ap["filenameBase"], ap["deviceID"])
    filePrefix = f"{GLOBALS.aimpointSts}/{filenameBase}"
    lookBack = timestamp - GLOBALS.disablerLookBack
    year, month, day, hour, mins, secs = tu.returnYMDHMS(lookBack)
    startAfterPrefix = f"{filePrefix}/{year}{month}{day}{hour}{mins}{secs}"

    # Most aimpoints don't have GLOBALS.collResultLimit number of collection results in a 30 minute period,
    # however, some do so the limit is set here to avoid timeouts when reading from aimpointStatus/
    collectionResults = GLOBALS.S3utils.getFilesAsStrList(config["defaultWrkBucket"], filePrefix, limit=GLOBALS.collResultLimit, startAfter=startAfterPrefix)
    if not collectionResults:
        logger.info(f"No collection results for {filePrefix} in the past {GLOBALS.disablerLookBack} seconds")
        return False

    return all("failure" in result for result in collectionResults)


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
