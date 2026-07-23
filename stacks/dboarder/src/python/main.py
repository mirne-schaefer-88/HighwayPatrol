# External libraries import statements
import os
import re
import time
import json
import logging
import argparse
import importlib
import threading
import datetime as dt
import concurrent.futures
from urllib.parse import urlparse


# This application's import statements
import processInit
import systemSettings
from exceptions import *
import superGlblVars as GLOBALS
from superGlblVars import config
from systemMode import SystemMode
from orangeUtils import auditUtils
from orangeUtils import utils as ut
from utils import hPatrolUtils as hput
from orangeUtils.auditUtils import AuditLogLevel


logger = logging.getLogger()


def _getAllowedParameters():
    # The aimpoint parameters that will be included in the filtered output
    allowedParams = [
          "hpID"
        , "deviceID"
        , "collEnabled"
        , "collRegions"
        , "decoy"
        , "proxy"
        , "collectionType"
        , "pollFrequency"
        , "concatenate"
        , "singleCollector"
        , "hours"
        , "longLat"
        , "transcoderInterval"
        , "timelapseLen"
        , "bucketPrefixTemplate"
        , "deliveryKey"
        , "filenameBase"
        , "monitoringData"
        , "lastMonitored"   # DEPRECATED
        , "lastMonitoredIsoDate"   # DEPRECATED
    ]
    return allowedParams


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
        # An aimpoint pushed to S3 triggers this lambda
        anS3file = event["Records"][0]["s3"]["object"]["key"]

        # Execute!
        wasGoodRun = True
        totalAimpoints = execute(anS3file)
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


def execute(fileDropped: str=None):
    # Identify ourselves for the audit logs
    GLOBALS.taskName = "DBoarder"

    if fileDropped:
        logger.info("Limiting execution to one specified aimpoint")
        fileList = [fileDropped]
    else:
        logger.info("No file specified; going after ALL aimpoints")
        fileList = hput.getAllAPs()

    if not GLOBALS.onProd:
        # Don't go through everything if we're not on PROD
        idx = 5
        fileList = fileList[:idx]
        logger.debug(f"Not running on PROD; exiting before processing file #{idx}")
    # logger.debug(f"fileList:{fileList}")

    # # This here is for non-parallel processing during dev/test
    # # Remember to comment out the parallelization below
    # for file in fileList:
    #     _processAimpoints(file)
    with concurrent.futures.ThreadPoolExecutor() as executor:
        futureToFile = {}
        for file in fileList:
            futureToFile[executor.submit(_processAimpoints, file)] = file
        for future in concurrent.futures.as_completed(futureToFile):
            try:
                # Check for unhandled exceptions during processing
                future.result()
            except Exception as e:
                logger.error(f"Exception when processing {futureToFile[future]}:::{e}")

    return len(fileList)


def _processAimpoints(aFile):
    # logger.debug(f"Processing file '{aFile}'")
    contents = GLOBALS.S3utils.readFileContent(config["defaultWrkBucket"], aFile)
    try:
        ap = json.loads(contents)
    except Exception as e:
        logger.warning(f"Error processing input file; skipping:::{e}")
        return None

    allowedParams = _getAllowedParameters()
    # filteredAp = {key: ap[key] for key in allowedParams if key in ap} // deletes the unallowed
    filteredAp = {key: value if key in allowedParams else "REDACTED" for key, value in ap.items()}
    filteredAp = _customizeAp(filteredAp)
    # print(json.dumps(filteredAp))

    _sendToBucket(filteredAp)

    return True


def _sendToBucket(ap: dict) -> None:
    deviceID = ap["deviceID"]
    outFile = os.path.join(config["workDirectory"], f"{deviceID}.json")
    try:
        ut.writeJsonDataToFile(ap, outFile)
    except Exception as err:
        logger.exception(f"Error creating aimpoint file:::{err}")
        raise HPatrolError("Error creating aimpoint file")

    for deliveryKey in ap["deliveryKey"]:
        s3Key = f"{GLOBALS.dashboardLz}/aimpoints/{deliveryKey}"
        s3BaseFileName = f"{ap["filenameBase"]}.json"

        # logger.debug(f"{s3Key}/{s3BaseFileName}")
        if not GLOBALS.S3utils.pushToS3(
            outFile,
            s3Key,
            config["defaultWrkBucket"],
            s3BaseFileName=s3BaseFileName,
            deleteOrig=GLOBALS.onProd,
            extras={"ContentType": "application/json"}
        ):
            logger.error(f"Error pushing '{deviceID}' to S3")


def _customizeAp(ap):
    # Filename base
    ap["filenameBase"] = hput.formatNameBase(ap["filenameBase"], ap["deviceID"])

    # Collection types
    if hput.isThisAStillType(ap):
        ap["collectionType"] = "STILL"
    else:
        ap["collectionType"] = "VIDEO"

    # Delivery keys
    try:
        deliveryKey = ap["deliveryKey"]
        if not deliveryKey:
            deliveryKey = GLOBALS.deliveryKey
    except KeyError:
        deliveryKey = GLOBALS.deliveryKey

    # Handle single-string input in the deliveryKey field
    if type(deliveryKey) is str:
        deliveryKey = deliveryKey.split()

    resolvedTemplate = ap["bucketPrefixTemplate"].format(
        year=None, month=None, day=None, deviceID=ap["deviceID"]
    )
    del ap["bucketPrefixTemplate"]
    deliveryKey = [f"{aDeliveryKey}/{resolvedTemplate}" for aDeliveryKey in deliveryKey]
    ap["deliveryKey"] = [aKey.replace("/None", "") for aKey in deliveryKey]

    # Add system defaults to make sure they show up in the dashboard
    ap = _addDefaults(ap, [
          (["transcoderInterval"], GLOBALS.transcoderInterval)
        , (["singleCollector"], False)
        , (["decoy"], False)
        , (["concatenate"], False)
        , (["monitoringData"], {})
        , (["monitoringData", "monitorFrequency"], GLOBALS.monitorFrequency)
    ])

    # No uname/pass on proxies
    ap = { k: re.sub(r'(https?://)[^:]+:[^@_]+', r'\1XXXX:YYYY', v) if isinstance(v, str) and 'proxy' in k else v for k, v in ap.items()}

    return ap


def _addDefaults(ap, defaults):
      """Add default values to aimpoint dictionary for missing keys.
      Args:
          ap: aimpoint dictionary
          defaults: list of tuples as (keyPath, defaultValue) where keyPath is a list of keys for nested access
      Returns:
          modified ap dictionary
      """
      for keyPath, defaultValue in defaults:
          target = ap
          for key in keyPath[:-1]:
              if key not in target:
                  target[key] = {}
              target = target[key]
          if keyPath[-1] not in target:
              target[keyPath[-1]] = defaultValue
      return ap


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Process aimpoints to show up in the \n"\
                    "dashboard. For security, aimpoints are\n"\
                    "limited in what they display in the dashboard.",
        formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("-s",
                        dest="s3Obj",
                        help=(
                            "s3 URL of an aimpoint\n"\
                            "If no s3 is specified, all will be processed."
                        ))
    parser.add_argument(
                        "-prod",
                        required=False,
                        action="store_true",
                        help=(
                            "optional; execute on PROD environment"
                        ))

    args = parser.parse_args()
    # print(args)

    if args.prod:
        config["mode"] = SystemMode.PROD
        # Need to reload system settings since we want to switch over to PROD
        importlib.reload(systemSettings)

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

    specifiedAp = None
    if args.s3Obj:
        parsedUrl = urlparse(args.s3Obj)
        specifiedAp = parsedUrl.path.lstrip('/')

    try:
        execute(specifiedAp)
    except HPatrolError as err:
        logger.info(f"Caught exception: {err}")

    nownow = int(time.time())
    logger.info(f"Process clocked at {str(dt.timedelta(seconds=nownow-upSince))}")

    toPrint = f"Exiting Process: {threading.main_thread().name}"
    logger.info(f"= {toPrint} =")
    logger.info(f"=={'=' * len(toPrint)}==")
