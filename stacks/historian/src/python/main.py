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

    encounteredError = False
    for record in event["Records"]:
        try:
            payload = json.loads(record["body"])
            aimpoint: dict = payload["aimpoint"]
            isCollecting: bool = payload["isCollecting"]
            timestamp: str = record["attributes"]["SentTimestamp"]
        except KeyError as err:
            logger.error(f"Invalid message received: {err}")
            logger.debug(f"Message received is: {event}")
            continue

        try:
            wasGoodRun = execute(aimpoint, isCollecting, timestamp)
            if not wasGoodRun:
                encounteredError = True

        except Exception as e:
            logger.exception(f"UNHANDLED EXCEPTION CAUGHT:::{e}")
            systemLevel = AuditLogLevel.CRITICAL
            dataLevel = None
            continue

    if not encounteredError:
        exitMessage = "Normal execution"
        wasGoodRun = True
    else:
        wasGoodRun = False
    nownow = int(time.time())
    logger.info(f"Process clocked at {str(dt.timedelta(seconds=nownow-upSince))}")

    # Don't want to clog up the logs with thousands of "message" events, so
    # just logging the number of events processed per cycle
    eventsCount = len(event["Records"])
    msgsCount = {"messagesProcessed": eventsCount}
    auditUtils.logFromLambda(
        event=msgsCount,
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


def execute(aimpoint: dict, isCollecting: bool, timestamp: str) -> bool:
    # Identify ourselves for the audit logs
    GLOBALS.taskName = "Historian"

    try:
        fileKey = _formatFileKey(aimpoint, isCollecting, timestamp)

        # logger.info(f"Recording collection result for {fileKey}")
        if not GLOBALS.S3utils.createEmptyKey(config["defaultWrkBucket"], fileKey):
            return False

    except HPatrolError as err:
        logger.error(f"Unexpected error in historian: {err}")
        return False

    return True


def _formatFileKey(ap: dict, isCollecting: bool, timestamp: str) -> str:
    deviceID = ap["deviceID"]
    epochSeconds = float(timestamp) / 1000
    epoch = int(epochSeconds)
    year, month, day, hour, mins, secs = tu.returnYMDHMS(epoch)
    filenameBase = ap["filenameBase"].format(deviceID=deviceID)
    result = "success" if isCollecting else "failure"
    filename = f"{year}{month}{day}{hour}{mins}{secs}_{epoch}_{result}"
    fileKey = f"{GLOBALS.aimpointSts}/{filenameBase}/{filename}"
    return fileKey


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

    testFile = "aimpoint-m3u8.json"
    logger.debug(f"Reading from test file '{testFile}'")
    with open(f"{GLOBALS.testResources}/{testFile}", 'r') as f:
        aimpoint = json.loads(f.read())

    # Successful collection of aimpoint
    # wasGoodRun = execute(aimpoint, True, str(time.time()))

    # Failed collection of monitored aimpoint
    wasGoodRun = execute(aimpoint, False, str(time.time()*1000))
    logger.info(f"Execution success: {wasGoodRun}")
    nownow = int(time.time())
    logger.info(f"Process clocked at {str(dt.timedelta(seconds=nownow-upSince))}")

    toPrint = f"Exiting Process: {threading.main_thread().name}"
    logger.info(f"= {toPrint} =")
    logger.info(f"=={'=' * len(toPrint)}==")
