"""
Module to manage the rounding up of a bunch of files, zip and upload them.
Code will look into an S3 bucket, get a listing of all the files, organize them
into each device (deviceID) then put the file list of each device into a queue.

This can be run as a stand-alone python script to test.
When run as stand-alone script, note that certain plumbing must be in place.
"""


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
import superGlblVars as GLOBALS
from superGlblVars import config
from orangeUtils import auditUtils
from utils import hPatrolUtils as hput
from orangeUtils import timeUtils as tu
from orangeUtils.auditUtils import AuditLogLevel


logger = logging.getLogger()


def _checkValues(data):
    # Check and/or set default values
    data["wrkBucket"] = hput.pickBestBucket(data, "wrkBucket")
    data["dstBucket"] = hput.pickBestBucket(data, "dstBucket")

    try:
        checkValue = data["deliveryKey"]
        if not checkValue:
            checkValue = GLOBALS.deliveryKey
        else:
            logger.info(f"Using aimpoint-specified deliveryKey '{checkValue}'")
    except KeyError:
        checkValue = GLOBALS.deliveryKey
    data["deliveryKey"] = checkValue

    return data


def execute():
    # Identify ourselves for the audit logs
    GLOBALS.taskName = "Marshal"

    # Note that it is here where we determine which files to go after
    # By default, we will be picking up the files from "yesterday"
    now = time.time()
    dayToWorkOn = now - 24*60*60
    if not GLOBALS.onProd:
        logger.debug("NOT on prod; using 'now' timestamp")
        dayToWorkOn = now
        # year, month, day = ('2022', '08','16')

    year, month, day = tu.returnYMD(dayToWorkOn)

    stillsAimpoints = []
    # Special treatment for multi-stills aimpoints (mstllsAimpoints) because a mapping happens later
    mstllsAimpoints = []
    # First, select all stills aimpoints out of all available
    if GLOBALS.useTestData:
        # List all files in testResources including in subdirectories
        apsList = [os.path.join(root, name) for root, dirs, files in os.walk(f"{GLOBALS.testResources}/") for name in files]
        for aFile in apsList:
            try:
                aJson = open(aFile, "r").read()
                apObject = json.loads(aJson)
            except Exception:
                # Not JSON files
                continue
            try:
                if(apObject["collectionType"] == "STILLS"):
                    stillsAimpoints.append(apObject)
                elif(apObject["collectionType"] == "FSTLLS"):
                    mstllsAimpoints.append(apObject)    # notice this uses mstllsAimpoints
                elif(apObject["collectionType"] == "ISTLLS"):
                    stillsAimpoints.append(apObject)
                elif(apObject["collectionType"] == "MSTLLS"):
                    stillsAimpoints.append(apObject)
                elif(apObject["collectionType"] == "IMAGEINJSON"):
                    stillsAimpoints.append(apObject)
            except Exception:
                # Not aimpoints
                continue
    else:
        apsList = hput.getAllAPs()
        for idx, ap in enumerate(apsList, start=1):
            # Don't go through everything if we're not on PROD
            if not GLOBALS.onProd and idx == 5:
                logger.debug(f"Not running on PROD; exiting before processing file #{idx}")
                break

            logger.info(f"Processing file '{ap}'")
            try:
                contents = GLOBALS.S3utils.readFileContent(config["defaultWrkBucket"], ap)
                apObject = json.loads(contents)
            except Exception as e:
                logger.warning(f"Error processing input file; skipping:::{e}")
                continue

            try:
                if not apObject["collEnabled"]:
                    try:
                        # If the ap is disabled AND not from a different collection system
                        throwAway = apObject["deliveryLzInput"]
                    except KeyError:
                        logger.info("Aimpoint disabled; skipping")
                        continue
            except KeyError as err:
                logger.error(f"Aimpoint missing key '{err}'")
                continue

            if(apObject["collectionType"] == "STILLS"):
                stillsAimpoints.append(apObject)

            if(apObject["collectionType"] == "FSTLLS"):
                mstllsAimpoints.append(apObject)    # notice this uses mstllsAimpoints

            if(apObject["collectionType"] == "ISTLLS"):
                stillsAimpoints.append(apObject)

            if(apObject["collectionType"] == "MSTLLS"):
                stillsAimpoints.append(apObject)

            if(apObject["collectionType"] == "IMAGEINJSON"):
                stillsAimpoints.append(apObject)

    logger.info(f"Stills aimpoints to work on: {len(stillsAimpoints)}")
    for ap in stillsAimpoints:
        ap = _checkValues(ap)
        deviceID = ap["deviceID"]

        # As default, the system uses the yr/mnth/day/filenameBase/ construct for the stills working area
        fnBase = hput.formatNameBase(ap["filenameBase"], deviceID)
        logger.info(f"Sending: {fnBase}")
        filenameBase = f"{fnBase}.zip"
        zipFileName = hput.formatNameSuffix(filenameBase, ap["finalFileSuffix"], dayToWorkOn)

        lz = ap.get("deliveryLzInput", GLOBALS.stillImages)
        lzPath = "{year}/{month}/{day}/{fnBase}".format(year=year, month=month, day=day, fnBase=fnBase)
        bucketPrefix = ap["bucketPrefixTemplate"].format(year=year, month=month, day=day, deviceID=deviceID)

        # Handle single-string input in the deliveryKey field
        if type(ap["deliveryKey"]) is str:
            ap["deliveryKey"] = ap["deliveryKey"].split()

        theMsg = {
            "bagAndZip": {
                "selected": fnBase,
                "zipFileName": zipFileName,
                "bucketPrefix" : bucketPrefix,
                "wrkBucket": ap["wrkBucket"],
                "dstBucket": ap["dstBucket"],
                "deliveryKey": ap["deliveryKey"],
                "filesLocation": f"{lz}/{lzPath}"
                }
        }
        logger.debug(f"Message: {json.dumps(theMsg)}")
        resp = GLOBALS.sqsUtils.sendMessage(config["bagQueue"], theMsg)
        # logger.debug(f"SQS response: {resp}")

    logger.info(f"Multi-stills aimpoints to work on: {len(mstllsAimpoints)}")
    for ap in mstllsAimpoints:
        ap = _checkValues(ap)
        stationId = ap["deviceID"]
        idList  = ap["deviceIdList"]
        urlList = ap["accessUrlList"]
        fnbList = ap["filenameBaseList"]
        for id, url, fNamBas in zip(idList, urlList, fnbList):
            deviceID = id
            ap["deviceID"] = id
            ap["accessUrl"] = url
            ap["filenameBase"] = fNamBas

            # The system uses the yr/mnth/day/filenameBase/ construct for the stills landing zone
            fnBase = hput.formatNameBase(ap["filenameBase"], deviceID)
            logger.info(f"Sending: {fnBase}")
            filenameBase = f"{fnBase}.zip"
            zipFileName = hput.formatNameSuffix(filenameBase, ap["finalFileSuffix"], dayToWorkOn)

            lz = ap.get("deliveryLzInput", GLOBALS.stillImages)
            lzPath = "{year}/{month}/{day}/{fnBase}".format(year=year, month=month, day=day, fnBase=fnBase)
            bucketPrefix = ap["bucketPrefixTemplate"].format(year=year, month=month, day=day, deviceID=deviceID)

            # Handle single-string input in the deliveryKey field
            if type(ap["deliveryKey"]) is str:
                ap["deliveryKey"] = ap["deliveryKey"].split()

            theMsg = {
                "bagAndZip": {
                    "selected": fnBase,
                    "zipFileName": zipFileName,
                    "bucketPrefix" : bucketPrefix,
                    "wrkBucket": ap["wrkBucket"],
                    "dstBucket": ap["dstBucket"],
                    "deliveryKey": ap["deliveryKey"],
                    "filesLocation": f"{lz}/{lzPath}"
                    }
            }
            logger.debug(f"Message: {json.dumps(theMsg)}")
            resp = GLOBALS.sqsUtils.sendMessage(config["bagQueue"], theMsg)
            # logger.debug(f"SQS response: {resp}")

        ap["accessUrl"] = None
        ap["filenameBase"] = None
        ap["deviceID"] = stationId

    return True


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
        wasGoodRun = execute()
        exitMessage = "Normal execution"
        if not wasGoodRun:
            dataLevel = AuditLogLevel.WARN

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

    execute()
    nownow = int(time.time())
    logger.info(f"Process clocked at {str(dt.timedelta(seconds=nownow-upSince))}")

    toPrint = f"Exiting Process: {threading.main_thread().name}"
    logger.info(f"= {toPrint} =")
    logger.info(f"=={'=' * len(toPrint)}==")
