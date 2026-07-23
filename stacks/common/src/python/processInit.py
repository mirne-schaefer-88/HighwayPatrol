# External libraries import statements
import os
import sys
import time
import json
import logging
import requests
import platform
import threading


# This application's import statements
from exceptions import *
import superGlblVars as GLOBALS
from superGlblVars import config
from orangeUtils import loggerSetup
from orangeUtils import utils as ut
from utils._version import __version__
from orangeUtils.awsUtils import S3utils
from orangeUtils.awsUtils import SQSutils
from orangeUtils.networkUtils import NetworkUtils


logger = logging.getLogger()

# Determine whether we're running on lambda or not
onLambda = "AWS_LAMBDA_FUNCTION_NAME" in os.environ


def preFlightSetup():
    # This "pre-flight" setup is intended to just establish the basic application (especially logging) before
    # we do any real serious processing or initializations; of course logging helps us better pinpoint errors
    # Therefore, this function should not have much else; any other initializations should be handled
    # elsewhere, such as the initialize() method.

    # Record startup time
    upSince = int(time.time())

    threading.main_thread().name = GLOBALS.projectName
    appName = threading.main_thread().name.lower()

    # Make sure all working directories exist
    if not os.path.exists(config["workDirectory"]):
        os.makedirs(config["workDirectory"])
    if not os.path.exists(config["logsDirectory"]):
        os.makedirs(config["logsDirectory"])

    loggerSetup.setupLogging(
        os.path.join(config["logsDirectory"], appName + ".log"),
        threading.main_thread().name
    )

    toPrint = f"Starting Service v{__version__}"
    logger.info(f"=={'=' * len(toPrint)}==")
    logger.info(f"= {toPrint} =")

    logger.info(f"Process ID: {ut.writePidFile(config["workDirectory"], GLOBALS.projectName)}")
    logger.info(f"System mode: {"PRODUCTION" if config["mode"] == "prod" else "DEVELOPMENT/TEST"}")
    logger.info(f"Logs directory: {config["logsDirectory"]}")

    return upSince


def initialize():
    # Determine the current running mode
    if config["mode"] == "prod":
        GLOBALS.onProd = True
    elif config["mode"] == "test":
        GLOBALS.useTestData = True
        logger.warning("*************************NOTE*************************")
        logger.warning("*******USING RESOURCES FROM THE TEST DIRECTORY********")
        logger.warning("******************************************************")

    # Make absolutely sure we're using UTC as default everywhere
    # Don't be dependent on specifying tz=utc in time-functions
    os.environ["TZ"] = "UTC"
    time.tzset()

    try:
        grabIp()
    except HPatrolError:
        return False

    # Log software info
    logger.info(f"OS Version: {platform.platform()}")
    logger.info(f"PY Version: {sys.version.replace('\n', ' ')}")


    # Obtains the queue names from environment variables, if set
    # Overrides any queue settings on settings.py
    # This gives us the flexibility to either assign the queue names in 
    # environment variables or on settings.py; this is to help w/CDK deployments
    if config["bagQueueVarName"] in os.environ:
        # Setting our own on-app variable so we don't look at the OS every time we need it
        config["bagQueue"] = os.environ[config["bagQueueVarName"]]
    else:
        try:
            logger.info(f"Bagging queue 'config[\"bagQueue\"]' set to '{config["bagQueue"]}'")
        except Exception:
            logger.warning("Queue value 'config[\"bagQueue\"]' NOT set by either settings.py nor environment variable")
            logger.warning(f"\t'{config["bagQueueVarName"]}' environment variable not set")

    if config["disQueueVarName"] in os.environ:
        # Setting our own on-app variable so we don't look at the OS every time we need it
        config["disQueue"] = os.environ[config["disQueueVarName"]]
    else:
        try:
            logger.info(f"Dispatch queue 'config[\"disQueue\"]' set to '{config["disQueue"]}'")
        except Exception:
            logger.warning("Queue value 'config[\"disQueue\"]' NOT set by either settings.py nor environment variable")
            logger.warning(f"\t'{config["disQueueVarName"]}' environment variable not set")

    if config["tcdQueueVarName"] in os.environ:
        # Setting our own on-app variable so we don't look at the OS every time we need it
        config["tcdQueue"] = os.environ[config["tcdQueueVarName"]]
    else:
        try:
            logger.info(f"Transcoder queue 'config[\"tcdQueue\"]' set to '{config["tcdQueue"]}'")
        except Exception:
            logger.warning("Queue value 'config[\"tcdQueue\"]' NOT set by either settings.py nor environment variable")
            logger.warning(f"\t'{config["tcdQueueVarName"]}' environment variable not set")

    if config["stsQueueVarName"] in os.environ:
        # Setting our own on-app variable so we don't look at the OS every time we need it
        config["statusQueue"] = os.environ[config["stsQueueVarName"]]
    else:
        try:
            logger.info(f"Status queue 'config[\"statusQueue\"]' set to '{config["statusQueue"]}'")
        except Exception:
            logger.warning("Queue value 'config[\"statusQueue\"]' NOT set by either settings.py nor environment variable")
            logger.warning(f"\t'{config["stsQueueVarName"]}' environment variable not set")


    # Instantiate S3 connections and SQS client
    try:
        if onLambda:
            GLOBALS.S3utils = S3utils(None, None, config["defaultWrkBucket"], useSsl=GLOBALS.useSslS3)
        else:
            GLOBALS.S3utils = S3utils(None, None, config["defaultWrkBucket"], profile=config["awsProfile"])
    except ValueError:
        return False

    try:
        if onLambda:
            GLOBALS.sqsUtils = SQSutils(regionName="us-east-1")
        else:
            GLOBALS.sqsUtils = SQSutils(config["awsProfile"])

    except ValueError:
        return False

    GLOBALS.myVersion = __version__

    # from pprint import pformat
    # logger.debug("Execution configuration:\n" + pformat(config))

    return True


def initSessionObject(sessionHeaders, verify=None):
    try:
        GLOBALS.netUtils = NetworkUtils(
            verify=verify,
            proxy=config["proxy"],
            sessionHeaders=sessionHeaders,
            workDirectory=config["workDirectory"]
            )
    except Exception as e:
        # If we are expected to connect on a proxy, best to fail
        # than to continue connecting without
        logger.critical(f"Unable to establish network connection:::{e}")
        raise HPatrolError("Network init error")


def grabIp():
    try:
        req = GLOBALS.netUtils.get(config["chkIpURL"])

    except AttributeError as e:
        # netUtils session not set; maybe not needed yet
        logger.info(f"Making a temporary network session to get IP:::{e}")
        sessionObj = requests.Session()
        req = sessionObj.get(config["chkIpURL"])

    except Exception as e:
        logger.error(f"Unable to obtain IP address; caught exception:::{e}")
        raise HPatrolError(f"Unable to obtain IP: {e}")

    try:
        tmp = json.loads(req.text.rstrip())
        GLOBALS.perceivedIP = tmp["ip"]

    except Exception as e:
        logger.error(f"Unable to obtain IP address; caught exception:::{e}")
        raise HPatrolError(f"Unable to obtain IP: {e}")

    # Log our perceived IP
    logger.info(f"IP Address: {GLOBALS.perceivedIP}")
