# External libraries import statements
import os
import time
import json
import math
import logging
import argparse
import threading
import datetime as dt
from pathlib import Path
import concurrent.futures


# This application's import statements
import processInit
import systemSettings
from exceptions import *
import superGlblVars as GLOBALS
from superGlblVars import config
from orangeUtils import auditUtils
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
        totalAimpoints = execute()
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
            totalAimpoints=totalAimpoints,
            # **collectionSummaryArgs
            # collectionSummaryArgs1="some",
            # collectionSummaryArgs2="additional",
            # collectionSummaryArgs3="info"
            )

    toPrint = "Exiting Process"
    logger.info(f"= {toPrint} =")
    logger.info(f"=={'=' * len(toPrint)}==")

    return {"status": wasGoodRun}


def execute():
    # Identify ourselves for the audit logs
    GLOBALS.taskName = "Scheduler"

    # TODO: Re-architect into using queues from the Collector to avoid overlaps

    # Obtain current time before we start looping and processing files,
    # so we get an accurate time of the "now" on the targets
    now = dt.datetime.now()

    # Select all currently tasked aimpoints
    s3Dir = GLOBALS.targetFiles
    logger.info(f"Looking for files in S3: '/{s3Dir}'")
    fileList = GLOBALS.S3utils.getFilesAsStrList(config["defaultWrkBucket"], s3Dir)
    if not GLOBALS.onProd:
        # Don't go through everything if we're not on PROD
        idx = 2
        fileList = fileList[:idx]
        logger.debug(f"Not running on PROD; exiting before processing file #{idx}")
    # logger.debug(f"fileList:{fileList}")
    try:
        logger.info(f"Total aimpoints found:{len(fileList)}")
    except TypeError:
        return 0

    with concurrent.futures.ThreadPoolExecutor() as executor:
        futureToFile = {}
        for file in fileList:
            futureToFile[executor.submit(_loadAimpoints, now, file)] = file
        for future in concurrent.futures.as_completed(futureToFile):
            try:
                # Check for unhandled exceptions during processing
                future.result()
            except Exception as e:
                logger.error(f"Exception when processing {futureToFile[future]}:::{e}")
                try:
                    executor.submit(_loadAimpoints, now, futureToFile[future]).result()
                except Exception as retryE:
                    logging.critical(f"Failed to process {futureToFile[future]} twice:::{retryE}")

    return len(fileList)


def _loadAimpoints(now, aFile):
    logger.info(f"Processing file '{aFile}'")
    contents = GLOBALS.S3utils.readFileContent(config["defaultWrkBucket"], aFile)
    try:
        ap = json.loads(contents)
    except Exception as e:
        logger.warning(f"Error processing input file; skipping:::{e}")
        return None

    # Make collection disabled by default; want enable to be explicit
    try:
        if ap["collEnabled"] is True:
            _processAndTaskIt(now, ap)
        else:
            logger.info("Aimpoint disabled; skipping")
            return None
    except KeyError as err:
        logger.error(f"Aimpoint missing parameter {err}")
        return None


def _processAndTaskIt(now, ap):
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
            if ap.get("collectionType", "NOPE") == "YTFILE":
                pollFrequency = systemPeriodicity
            else:
                deviceID = ap.get("deviceID", "MISSING DEVICEID")
                filenameBase = ap.get("filenameBase", "MISSING FILENAMEBASE")
                filenameBase = hput.formatNameBase(filenameBase, deviceID)
                logger.error(f"Aimpoint lacks pollFrequency; skipping ({filenameBase})")
                continue

        if pollFrequency < 10:
            frequency = 10
            logger.info(f"Poll frequency < 10; rounded to 10s")
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
                logger.info(f"SingleCollector requested")
            else:
                addPlural = 's' if len(delayList) > 1 else ''                
                logger.info(f"Will request every {frequency} seconds; {len(delayList)} request{addPlural} total")

            _sendTasks(now, delayList, ap)


def _sendTasks(now, delayList, ap):
    for idx, theDelay in enumerate(delayList, start=1):
        # Don't go through everything if we're not on PROD
        if not GLOBALS.onProd and idx == 5:
            logger.debug(f"Not running on PROD; exiting at request #{idx}")
            break

        theMsg = ap

        baseName = hput.formatNameBase(ap["filenameBase"], ap["deviceID"])
        logger.info(f"Sending '{baseName}' "
            f"to {config["disQueue"]} queue "
            f"with a delay of {str(dt.timedelta(seconds=theDelay))}, "
            f"to run at {(now + dt.timedelta(seconds=theDelay)).strftime('%m/%d %H:%M:%S')}"
        )
        # logger.debug(f"Message: {json.dumps(theMsg)}")
        GLOBALS.sqsUtils.sendMessage(config["disQueue"], theMsg, theDelay)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description='Scheduler:\n'\
            'Takes some optional parameters just to help in aimpoint creation',
    )
    parser.add_argument(
        "-z",
        "--zones",
        required=False,
        action='store_true',
        help=(
            "displays all timezones available in the python library"
        ),
    )
    args = parser.parse_args()
    if args.zones:
        tu.getAllTZs(True)
        exit(1)

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
        execute()
    except HPatrolError as err:
        logger.info(f"Caught exception: {err}")

    nownow = int(time.time())
    logger.info(f"Process clocked at {str(dt.timedelta(seconds=nownow-upSince))}")

    toPrint = f"Exiting Process: {threading.main_thread().name}"
    logger.info(f"= {toPrint} =")
    logger.info(f"=={'=' * len(toPrint)}==")
