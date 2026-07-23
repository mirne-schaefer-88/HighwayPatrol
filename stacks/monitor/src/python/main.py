# External libraries import statements
import os
import time
import json
import math
import random
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
        totalAimpoints = execute(upSince)
        exitMessage = "Normal execution"

    except Exception as e:
        logger.exception(f"UNHANDLED EXCEPTION CAUGHT:::{e}")
        systemLevel = AuditLogLevel.CRITICAL
        dataLevel = None
        wasGoodRun = False
        totalAimpoints = None

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
            monitoredAimpoints=totalAimpoints,
            # **collectionSummaryArgs
            # collectionSummaryArgs1="some",
            # collectionSummaryArgs2="additional",
            # collectionSummaryArgs3="info"
            )

    toPrint = "Exiting Process"
    logger.info(f"= {toPrint} =")
    logger.info(f"=={'=' * len(toPrint)}==")

    return {"status": wasGoodRun}


def execute(nowEpoch: int):
    # Identify ourselves for the audit logs
    GLOBALS.taskName = "Monitor"

    # Select aimpoints that are currently being monitored
    s3Dir = GLOBALS.monitorTrgt
    logger.info(f"Looking for files in S3: '/{s3Dir}'")
    fileList = GLOBALS.S3utils.getFilesAsStrList(config["defaultWrkBucket"], s3Dir)
    # logger.debug(f"fileList:{fileList}")
    try:
        logger.info(f"Total aimpoints found:{len(fileList)}")
    except TypeError:
        return 0

    monitorOneDomains = set()
    processedDomains = set()
    # For each aimpoint file, check its status
    for idx, aFile in enumerate(fileList, start=1):
        # Don't go through everything if we're not on PROD
        if not GLOBALS.onProd and idx == 2:
            logger.debug(f"Not running on PROD; exiting before processing file #{idx}")
            break

        # If an entire domain is down, only monitor one device per run
        # from that domain; otherwise monitor all devices per domain
        monitorDomain = os.path.dirname(aFile)
        if monitorDomain in monitorOneDomains:
            # Already looked into this domain as being fully down; ignore the rest
            continue
        if monitorDomain not in processedDomains:
            activeDomain = monitorDomain.replace(GLOBALS.monitorTrgt, GLOBALS.targetFiles)
            activeDevices = GLOBALS.S3utils.getFilesAsStrList(config["defaultWrkBucket"], activeDomain)
            if not activeDevices:
                # None found as active; therefore all are in monitoring status
                logger.info(f"No active aimpoints found; assuming domain is out; will only test one")
                monitorOneDomains.add(monitorDomain)
                disabledDevices = GLOBALS.S3utils.getFilesAsStrList(config["defaultWrkBucket"], monitorDomain)
                aFile = random.choice(disabledDevices)  # select one aimpoint to test at random
            else:
                processedDomains.add(monitorDomain)

        logger.info(f"Processing file '{aFile}'")
        contents = GLOBALS.S3utils.readFileContent(config["defaultWrkBucket"], aFile)
        try:
            ap = json.loads(contents)
        except Exception as e:
            logger.warning(f"Error processing input file; skipping:::{e}")
            continue

        # Act only if explicitly enabled
        try:
            if ap["collEnabled"] is True:
                pass
            else:
                logger.info("Aimpoint disabled; skipping")
                continue
        except KeyError as err:
            logger.error(f"Aimpoint missing parameter {err}; skipping")
            continue

        monitoringCfg = ap.get("monitoringData", {})
        # monitorFrequency defaults to 12hrs if not set in aimpoint
        monitorFrequency = monitoringCfg.get("monitorFrequency", GLOBALS.monitorFrequency)
        lastMonitored = monitoringCfg.get("lastMonitored", None)

        hoursPassed = 0
        # Note the variable nowEpoch = int(time.time()) from processInit
        if lastMonitored:
            timeDiff = nowEpoch - lastMonitored
            # Convert seconds to hours
            hoursPassed = timeDiff // 3600  # 3600==1 hour in seconds

        if not lastMonitored or hoursPassed >= monitorFrequency:
            dtObj = dt.datetime.fromtimestamp(nowEpoch, dt.UTC)
            # If monitoringData hasn't been set yet, create it (prevent KeyError)
            ap.setdefault("monitoringData", {})["lastMonitored"] = nowEpoch
            ap["monitoringData"]["lastMonitoredIsoDate"] = dtObj.isoformat()

            if _monitorTasksSent(dtObj, ap):
                _updateLastMonitored(aFile, ap)

    return len(fileList)


def _updateLastMonitored(filePath, ap):
    dirName = os.path.dirname(filePath)
    fileName = os.path.basename(filePath)
    tmpFile = os.path.join(config["workDirectory"], fileName)
    ut.writeJsonDataToFile(ap, tmpFile)
    logger.info(f"Updating lastMonitored value in {filePath}")
    pushedToS3 = GLOBALS.S3utils.pushToS3(
                    tmpFile, 
                    dirName, 
                    config["defaultWrkBucket"], 
                    deleteOrig=GLOBALS.onProd, 
                    s3BaseFileName=fileName,
                    extras={"ContentType": "application/json"})
    if not pushedToS3:
        raise HPatrolError(f"Error pushing to S3 when updating lastMonitored value: {filePath}")


def _monitorTasksSent(now, ap):
    systemPeriodicity = GLOBALS.systemPeriodicity * 60  # convert to seconds
    systemTimeLimit = systemPeriodicity + 30
    # We add 30secs of overlap to the queue orders so as to not lose anything
    # Video may jump and repeat frames, but we prefer that than to lose feed

    # Sometimes we want just one Collector to be spawned during our systemPeriodicity
    # One Collector sometimes is better instead of spawning and re-spawning multiples
    # i.e.: Had a case where the pollFrequency was 2 seconds for stills
    try:
        singleCollector = True == ap["singleCollector"]
    except KeyError:
        singleCollector = False

    try:
        notUsed, theRanges = tu.getWorkHours(now, ap["hours"])
    except KeyError:
        # There is no working hours specified in the aimpoint; go for default
        theRanges = ["0000-2359"]

    # This determines how often Collectors are spawned
    for aRange in theRanges:
        # Requests are sent for the same target within the systemPeriodicity, spaced out by the frequency
        # Rounding down instead of up because we rather overlap than have gaps in transmission
        # Overlaps are handled later using file hashes
        # Example: (round down)
        #          if pollFrequency = 28, frequency = 20
        try:
            pollFrequency = ap["pollFrequency"]
        except KeyError:
            # YouTube file downloads do not need pollFrequency
            if ap.get("collectionType", None) == "YTFILE":
                pollFrequency = systemPeriodicity
            else:
                deviceID = ap.get("deviceID", "MISSING DEVICEID")
                filenameBase = ap.get("filenameBase", "MISSING FILENAMEBASE")
                filenameBase = hput.formatNameBase(filenameBase, deviceID)
                logger.error(f"Aimpoint lacks pollFrequency; skipping ({filenameBase})")
                continue

        if pollFrequency < 10:
            frequency = 10
            logger.warning(f"Poll frequency < 10; rounded to 10s")
        elif pollFrequency >= systemPeriodicity:
            # If the target's poll frequency is larger than system frequency, do just one
            frequency = systemTimeLimit
        else:
            frequency = int(math.floor(pollFrequency / 10.0)) * 10

        # delayList indicates the delays which the task messages will have on the queue
        delayList = list(range(0, systemTimeLimit, frequency))
        try:
            # Initial delayList is further reduced to the target's working hours
            delayList = tu.getReducedSegmentsRange(delayList, now, ap["hours"]["tz"], aRange)
        except KeyError:
            # No Working Hours specified
            pass

        if delayList != []:
            if singleCollector:
                delayList = [delayList[0]]
                logger.info("SingleCollector requested")
            else:
                addPlural = 's' if len(delayList) > 1 else ''                
                logger.info(f"Will request every {frequency} seconds; {len(delayList)} request{addPlural} total")

            _sendTasks(now, delayList, ap)
            return True
        else:
            logger.info("Aimpoint NOT within working hours; won't monitor")
            return False


def _sendTasks(now, delayList, ap):
    for idx, theDelay in enumerate(delayList, start=1):
        # Don't go through everything if we're not on PROD
        if not GLOBALS.onProd and idx == 5:
            logger.debug(f"Not running on PROD; exiting at request #{idx}")
            break

        baseName = hput.formatNameBase(ap["filenameBase"], ap["deviceID"])
        logger.info(f"Sending '{baseName}' "
            f"to {config["disQueue"]} queue "
            f"with a delay of {str(dt.timedelta(seconds=theDelay))}, "
            f"to run at {(now + dt.timedelta(seconds=theDelay)).strftime('%m/%d %H:%M:%S')}"
        )
        # logger.debug(f"Message: {json.dumps(ap)}")
        GLOBALS.sqsUtils.sendMessage(config["disQueue"], ap, theDelay)


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
