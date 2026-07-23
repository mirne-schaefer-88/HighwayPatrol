"""
Module to collect video and stills from live cameras given an URL.

Can be run as a stand-alone python script to test; but note that some architectural
elements must exist (e.g. the queues) because they are checked pre-flight.
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
import stillsGrabber as sg
import videosGrabber as vg
import youtubeInterface as yt
import superGlblVars as GLOBALS
from superGlblVars import config
from orangeUtils import auditUtils
from addons import streamInvoker as si
from utils import hPatrolUtils as hput
from orangeUtils import timeUtils as tu
from collectionTypes import CollectionType
from orangeUtils.auditUtils import AuditLogLevel


logger = logging.getLogger()


def lambdaHandler(event, context):
    # Pre-set values in case execution is interrupted
    dataLevel = AuditLogLevel.WARN
    systemLevel = AuditLogLevel.WARN
    exitMessage = "Exit with errors"

    upSince = processInit.preFlightSetup()
    # logger.info(f"Lambda Handler started at {upSince}")
    # logger.debug(f"lambdaContext: {context}")

    # Capture our ARN for later use
    GLOBALS.myArn = context.invoked_function_arn

    processInit.initSessionObject(config["sessionHeaders"])
    if not processInit.initialize():
        logger.error("Failed to initialize")
        exit(1)

    try:
        if "proxy" in event and event["proxy"]:
            config["proxy"] = event["proxy"]
            logger.info("Will use aimpoint-specified proxy")
            processInit.initSessionObject(config["sessionHeaders"])
            processInit.grabIp()

        # Execute!
        exitMessage, wasGoodRun = execute(event, context)

        # Seems execution was ok, update audit values
        dataLevel = AuditLogLevel.INFO
        systemLevel = AuditLogLevel.INFO
        if not wasGoodRun:
            # TODO: Improve handling of and exit codes for audit logs;
            #       Not happy w/these returns and message logic
            #       Try to call out network issues
            dataLevel = AuditLogLevel.WARN

    except Exception as e:
        logger.exception(f"UNHANDLED EXCEPTION CAUGHT:::{e}")
        wasGoodRun = False
        dataLevel = AuditLogLevel.CRITICAL
        systemLevel = AuditLogLevel.CRITICAL

    finally:
        nownow = int(time.time())
        logger.info(f"Process clocked at {str(dt.timedelta(seconds=nownow-upSince))}")

        # Need to reset proxy; lambdas can keep memory
        # This is specific to cases where the aimpoint needs a proxy; most don't
        # It is CRITICAL that this reset to False happens because
        # if not (e.g. when a lambda falls out through an exception)
        # the proxy value can be maintained for the next lambda that runs
        # So need to make sure to catch any exceptions on lambda PROD
        config["proxy"] = False

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
            location=event["longLat"],
            # **collectionSummaryArgs
            # collectionSummaryArgs1="some",
            # collectionSummaryArgs2="additional",
            # collectionSummaryArgs3="info"
        )

    toPrint = "Exiting Process"
    logger.info(f"= {toPrint} =")
    logger.info(f"=={'=' * len(toPrint)}==")

    return {"status": wasGoodRun}


# 2026.02.12 DEPRECATED: Previously used for our own VPN solution
# import certifi
# from OpenSSL import crypto
# def _modifyCAfile():
#     customCAFile = f"{config["workDirectory"]}/combined.pem"

#     cafile = certifi.where()
#     logger.info(f"CA file in {cafile}")
#     with open(cafile, "rb") as infile:
#         caContents = infile.read()

#     ourCaAddFile = f"{GLOBALS.hpResources}/{GLOBALS.proxyCaFile}"
#     logger.info(f"Reading our CA file from S3 's3://{config["defaultWrkBucket"]}/{ourCaAddFile}'")
#     ourCaAdd = GLOBALS.S3utils.readFileContent(config["defaultWrkBucket"], ourCaAddFile)
#     if not ourCaAdd:
#         raise HPatrolError(f"Own CA file error; content is '{ourCaAdd}'")
#     # logger.debug(f"Contents:\n{ourCaAdd}")

#     try:
#         cert = crypto.load_certificate(crypto.FILETYPE_PEM, ourCaAdd)
#     except Exception as err:
#         raise HPatrolError(err)

#     thedate = dt.datetime.strptime(cert.get_notAfter().decode(), "%Y%m%d%H%M%SZ")
#     logger.info(f"Cert expires on: {thedate}")

#     with open(customCAFile, "wb") as outfile:
#         outfile.write(caContents)
#         outfile.write(ourCaAdd.encode("ascii"))

#     return customCAFile


def execute(ap, lambdaContext=None):
    # Identify ourselves for the audit logs
    GLOBALS.taskName = "Collector"

    rtnMessage = "Exit with errors"

    # Validate type of collection being tasked
    collectionTypesMap = {
          "M3U": CollectionType.M3U
        , "RDTC": CollectionType.RDTC
        , "STILLS": CollectionType.STILLS
        , "FSTLLS": CollectionType.FSTLLS
        , "ISTLLS": CollectionType.ISTLLS
        , "MSTLLS": CollectionType.MSTLLS
        , "IVIDEO": CollectionType.IVIDEO
        , "UFANET": CollectionType.UFANET
        , "RTSPME": CollectionType.RTSPME
        , "IPLIVE": CollectionType.IPLIVE
        , "HNGCLD": CollectionType.HNGCLD
        , "GNDONG": CollectionType.GNDONG
        , "BAZNET": CollectionType.BAZNET
        , "YOUTUB": CollectionType.YOUTUB
        , "YTFILE": CollectionType.YTFILE
        , "STREAM": CollectionType.STREAM
        , "OPTION": CollectionType.OPTION
        , "FIRSTCONTACT": CollectionType.FIRST
        , "IMAGEINJSON": CollectionType.IMAGEINJSON
    }

    try:
        collType = collectionTypesMap[ap["collectionType"]]

        # Identify our subtask for the audit logs
        GLOBALS.subtaskName = ap["collectionType"]
    except KeyError as err:
        logger.error(f"Collection type unknown in input configuration:::{err}")
        logger.error('Be sure to specify "collectionType": <TYPE>')
        logger.debug(json.dumps(ap))
        return rtnMessage, False

    try:
        logger.info(
            f"Executing for filenameBase '{hput.formatNameBase(ap["filenameBase"], ap["deviceID"])}'"
        )
    except KeyError as err:
        logger.error(f"{err} not specified in input configuration")
        logger.error(f"Be sure to specify {err} in JSON file")
        return rtnMessage, False

    try:
        _handleType(collType, ap, lambdaContext)
        rtnMessage = "Normal execution"
        return rtnMessage, True
    except HPatrolError as what:
        # Overall HPatrolError catcher, but raisers should still print to log themselves
        logger.error(f"HPatrolError: {what}")
        GLOBALS.sqsUtils.sendMessage(config["statusQueue"], {"aimpoint": ap, "isCollecting": False})
        return rtnMessage, False


def _handleType(collType, ap, lambdaContext=None):
    try:
        # Determine the S3's prefix
        deviceID = ap["deviceID"]
        year, month, day = tu.returnYMD(time.time())
        resolvedTemplate = ap["bucketPrefixTemplate"].format(
            year=year, month=month, day=day, deviceID=deviceID
        )
        prefixBase = f"{GLOBALS.landingZone}/{resolvedTemplate}"
    except KeyError as err:
        logger.error(
            "Parameter unspecified attempting to resolve 'bucketPrefixTemplate'"
        )
        logger.error(
            f"Check input configuration or resolving function for {err} parameter"
        )
        raise HPatrolError("Parameter unspecified")

    if (
        collType == CollectionType.FIRST or
        collType == CollectionType.IVIDEO or
        collType == CollectionType.UFANET or
        collType == CollectionType.RTSPME or
        collType == CollectionType.IPLIVE or
        collType == CollectionType.HNGCLD or
        collType == CollectionType.GNDONG or
        collType == CollectionType.BAZNET or
        collType == CollectionType.OPTION or
        collType == CollectionType.RDTC or
        collType == CollectionType.M3U
    ):
        # Identify ourselves for the audit logs
        GLOBALS.subtaskName = "Video"
        vg.handleVideos(collType, prefixBase, ap, lambdaContext)

    elif collType == CollectionType.YTFILE:
        # Identify ourselves for the audit logs
        GLOBALS.subtaskName = "YouTubeFile"
        yt.handleTube(prefixBase, ap)

    elif collType == CollectionType.YOUTUB:
        # Identify ourselves for the audit logs
        GLOBALS.subtaskName = "YouTubeStream"
        yt.handleTubeStream(prefixBase, ap, lambdaContext)

    elif collType == CollectionType.STREAM:
        GLOBALS.subtaskName = "Stream"
        si.invoke(ap)

    elif (
        collType == CollectionType.IMAGEINJSON or
        collType == CollectionType.STILLS or
        collType == CollectionType.FSTLLS or
        collType == CollectionType.ISTLLS or
        collType == CollectionType.MSTLLS
    ):
        # Identify ourselves for the audit logs
        GLOBALS.subtaskName = "Still"

        # As default, the system uses the yr/mnth/day/filenameBase/ construct for the stills working area
        # The bucketPrefixTemplate is used for final delivery
        fnBase = hput.formatNameBase(ap["filenameBase"], ap["deviceID"])
        resolvedTemplate = "{year}/{month}/{day}/{fnBase}".format(
            year=year, month=month, day=day, fnBase=fnBase
        )
        prefixBase = f"{GLOBALS.stillImages}/{resolvedTemplate}"
        sg.handleStills(collType, prefixBase, ap, lambdaContext)

    else:
        logger.error("Collection type undefined")
        raise HPatrolError("Collection type undefined")


if __name__ == "__main__":
    # Obtain test file name, if given
    # Defaults to testResources/aimpoint-youtube.json otherwise
    parser = argparse.ArgumentParser(prog="Collector",
                                     description="Test the collector on an EC2 instance",
                                     formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("-f",
                        help=(
                            "Aimpoint file for testing\n"\
                            "default: aimpoint-youtube.json"
                        ),
                        dest="testFile",
                        default="aimpoint-youtube.json")

    args = parser.parse_args()
    testFile = args.testFile
    # print(args)

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

    if testFile == "aimpoint-youtube.json": # Using the default
        testFile = f"{GLOBALS.testResources}/{testFile}"
    logger.debug(f"Reading from test file '{testFile}'")
    with open(testFile, "r") as f:
        testEvent = json.loads(f.read())
    # logger.debug(f"AIMPOINT:\n'{json.dumps(testEvent)}'")

    try:
        wasGoodRun = execute(testEvent)
    except ConnectionError as err:
        logger.info(f"Caught exception: {err}")

    nownow = int(time.time())
    logger.info(f"Process clocked at {str(dt.timedelta(seconds=nownow-upSince))}")

    toPrint = f"Exiting Process: {threading.main_thread().name}"
    logger.info(f"= {toPrint} =")
    logger.info(f"=={'=' * len(toPrint)}==")
