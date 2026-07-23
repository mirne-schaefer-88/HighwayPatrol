"""
General HPatrol utilities
"""


# External libraries import statements
import os
import re
import time
import json
import logging
import datetime as dt
from enum import IntEnum


# This application's import statements
import systemSettings
from exceptions import *
import superGlblVars as GLOBALS
from superGlblVars import config
from systemMode import SystemMode
from orangeUtils import utils as ut
from orangeUtils import timeUtils as tu


logger = logging.getLogger()


def getAllAPs():
    # Select currently tasked aimpoints
    s3Dir = GLOBALS.targetFiles
    # logger.info(f"Looking for files in S3: '{s3Dir}/'")
    fileList = GLOBALS.S3utils.getFilesAsStrList(config["defaultWrkBucket"], s3Dir)

    # Select monitored aimpoints
    s3Dir = GLOBALS.monitorTrgt
    # logger.info(f"Looking for files in S3: '{s3Dir}/'")
    monitoredList = GLOBALS.S3utils.getFilesAsStrList(config["defaultWrkBucket"], s3Dir)

    try:
        fileList.extend(monitoredList)
    except TypeError:
        # There may not be a monitored folder
        pass

    # Select aimpoints from any other non-standard deployments
    for ap in [GLOBALS.targetFiles, GLOBALS.monitorTrgt]:
        for aKey in config["otherAps"]:
            s3Dir = f"{aKey}/{ap}"
            logger.info(f"Looking for files in S3: '{s3Dir}/'")
            monitoredList = GLOBALS.S3utils.getFilesAsStrList(config["defaultWrkBucket"], s3Dir)

            try:
                fileList.extend(monitoredList)
            except TypeError:
                # There may not be an active folder
                pass

    try:
        logger.info(f"Total aimpoints found:{len(fileList)}")
        # There may not be any aimpoints either; we looking in the wrong place?
    except TypeError:
        raise HPatrolError("No aimpoints found") from None

    # logger.debug(fileList)
    return fileList


def formatNameBase(nameTemplate, devId):
    formattedBase = nameTemplate.format(deviceID=devId)

    return formattedBase


def formatNameSuffix(baseName, suffixTemplate, timestamp=None):
    ext = os.path.splitext(baseName)[1]
    name = os.path.splitext(baseName)[0]

    if not timestamp:
        timestamp = int(time.time())

    year, month, day, hour, mins, secs = tu.returnYMDHMS(timestamp)
    formattedSuffix = suffixTemplate.format(
        year=year,
        month=month,
        day=day,
        hour=hour,
        mins=mins,
        secs=secs,
        epoch=int(timestamp)
    )

    return f"{name}{formattedSuffix}{ext}"


def addHrMinToSuffix(suffixTemplate):
    if "hour" not in suffixTemplate:
        suffixTemplate = f"{suffixTemplate}{{hour}}"
    if "mins" not in suffixTemplate:
        suffixTemplate = f"{suffixTemplate}{{mins}}"

    return suffixTemplate


def itsTimeToBail(lambdaContext, breakPoint, aboutToSleep):
    # Prevent lambda timeouts
    # Return True if we're close to, or would timeout during an upcoming sleep
    
    # First, a simple check in case the target's pollFreq >= systemFreq; cost savings
    if aboutToSleep >= GLOBALS.systemPeriodicity * 60 * 1000:
        logger.info("Time to bail; pollFreq >= systemFreq")
        return True

    # Notice that if we're not on a lambda, we simulate by calculating forward from now
    if lambdaContext:
        remainingTimeInMillis = lambdaContext.get_remaining_time_in_millis()
        logger.debug(f"Lambda time left {str(dt.timedelta(milliseconds=remainingTimeInMillis)).split(".", 2)[0]}")
        if remainingTimeInMillis - breakPoint - aboutToSleep < 0:
            logger.info("Time to bail; either potential timeout, or time to close shop")
            return True
    else:
        nowIs = int(time.time()*1000)
        if GLOBALS.useTestData and nowIs > breakPoint:
            # When using test data, we don't do the between-calls-to-the-target sleeps
            return True
        elif nowIs > breakPoint + aboutToSleep:
            return True

    return False


def getSelection(selectionFile, includeOff=False):
    logger.info("Getting specified selection from file")
    if GLOBALS.useTestData:
        testFile = selectionFile
        logger.info(f"Reading from test file '{testFile}'")
        try:
            with open(f"{GLOBALS.testResources}/{testFile}", 'r', encoding="utf-8") as f:
                respText = f.read()
        except FileNotFoundError as err:
            logger.error(err)
            raise HPatrolError("No selected list of targets found")

    else:
        idsS3fileAndPath = f"{GLOBALS.selectTrgts}/{selectionFile}"
        respText = GLOBALS.S3utils.readFileContent(config["defaultWrkBucket"], idsS3fileAndPath)

        if not respText:
            logger.error("No selected list of targets found")
            raise HPatrolError("No selected list of targets found")

    selectionJson = json.loads(respText)
    selections = selectionJson["selections"]
    logger.info(f"Total IDs in selection: {len(selections)}")
    if includeOff:
        return selections

    # We rather not even bother w/off devices instead of creating a "disabled" aimpoint
    selections = {
        aKey: aVal
        for aKey, aVal in selections.items()
        if aVal != "off" and not (
            isinstance(aVal, dict) and aVal.get("monitoringData", {}).get("selectionsState") == "off"
        )
    }
    logger.info(f"Total non-off selected: {len(selections)}")
    if len(selections) == 0:
        logger.warning("No devices selected for aimpoint creation")

    return selections


def refreshSelectionsTimestamp(selectionsFile: str, timeStamp: int):
    """
    Prevents selections files from being deleted in some environments
    due to their S3 file retention policy
    Read a selections file from S3, add to/update the JSON with a timestamp,
    and write back to s3
    """

    if GLOBALS.useTestData:
        testFile = selectionsFile
        logger.info(f"Reading from test file '{testFile}'")
        try:
            with open(f"{GLOBALS.testResources}/{testFile}", 'r', encoding="utf-8") as f:
                respText = f.read()
        except FileNotFoundError as err:
            logger.error(err)
            raise HPatrolError("No selected list of targets found")

    else:
        selectionsKey = f"{GLOBALS.selectTrgts}/{selectionsFile}"
        respText = GLOBALS.S3utils.readFileContent(config["defaultWrkBucket"], selectionsKey)

        if not respText:
            logger.error("No selected list of targets found")
            raise HPatrolError("No selected list of targets found")

    selectionJson = json.loads(respText)
    tsObj = dt.datetime.fromtimestamp(timeStamp)
    selectionJson["refreshedOn"] = tsObj.strftime("%Y-%m-%dT%H:%M:%S")

    tmpFile = os.path.join(config["workDirectory"], selectionsFile)
    ut.writeJsonDataToFile(selectionJson, tmpFile)
    logger.info(f"Pushing updated selections file {selectionsFile} to S3")
    pushedToS3 = GLOBALS.S3utils.pushToS3(
                    tmpFile, 
                    GLOBALS.selectTrgts, 
                    config["defaultWrkBucket"], 
                    deleteOrig=GLOBALS.onProd, 
                    s3BaseFileName=selectionsFile,
                    extras={"ContentType": "application/json"})
    if not pushedToS3:
        raise HPatrolError(f"Error pushing '{selectionsFile}' to S3")


def calculateExecutionStop(ap, lambdaContext=None):
    try:
        sleepyFraction = ap["waitFraction"]
    except KeyError:
        sleepyFraction = 1.0

    # Calculate how many requests we should make before we die out
    # Could have gone with an Expires entry on the returned headers, but not all headers have them
    pollFrequency = ap["pollFrequency"]
    runningTimeMins = GLOBALS.systemPeriodicity + 0.5 # add 30secs so requests overlap
                                        # as of 08/11/22 lambdas run for no more than 15mins
                                        # but this whole system is set to run every 10mins

    # Notice we are converting to make the calculations in milliseconds
    pollFrequency = pollFrequency * 1000
    theSleep = pollFrequency * sleepyFraction
    runningTime = runningTimeMins * 60 * 1000

    # Obtain current time before we start looping and processing files,
    # so we get an accurate time of the "now" on the targets
    now = dt.datetime.now()

    try:
        targetTime, theRanges = tu.getWorkHours(now, ap["hours"])
    except KeyError:
        # No Working Hours specified
        targetTime = now
        theRanges = ["0000-2359"]

    try:
        runningTime = tu.closeShopSecsLeft(theRanges, targetTime, runningTimeMins)
        logger.debug(f"Will set breakPoint to {runningTime}s")
        runningTime = int(runningTime * 1000)
    except ValueError:
        pass

    # When breakPoint is eventually reached, the system will stop processing
    # Notice that if we're on lambda, we measure backwards from remainingTimeInMillis
    # whereas if we're on EC2, we measure forward from now; i.e. time.time()
    # i.e., when in lambda, the breakPoint variable will be compared against a future remaining-time
    # and when in EC2, the breakPoint variable will be compared against a future now-time
    # see function itsTimeToBail() for the comparison
    if lambdaContext:
        remainingTimeInMillis = lambdaContext.get_remaining_time_in_millis()
    if not GLOBALS.onProd:
        stopIt = 30
        logger.debug(f"Not running on PROD; limiting iterations to {stopIt} seconds")
        stopIt = stopIt * 1000
        if lambdaContext:
            breakPoint = remainingTimeInMillis - stopIt
        else:
            breakPoint = int(time.time() * 1000 + stopIt)
    else:
        # Note that if lambda time is 15mins and we run for 11, we only have 4mins left for processing
        # Obvious, yes, but this is where that calculation happens
        if lambdaContext:
            breakPoint = remainingTimeInMillis - runningTime
        else:
            breakPoint = int(time.time() * 1000 + runningTime)

    return breakPoint, theSleep, sleepyFraction


def _atof(text):
    try:
        retval = float(text)
    except ValueError:
        retval = text
    return retval


def naturalKeys(theList):
    # For human sort (natural sort) of floating point units
    regex = r'[+-]?([0-9]+(?:[.][0-9]*)?|[.][0-9]+)'

    return [_atof(c) for c in re.split(regex, theList)]


def mergeSelections(selected, baseTemplate: dict) -> None:
    """Merge aimpoint settings with a template"""

    # Double format change because "selected" could be string or dict
    mergeTemplate = json.loads(json.dumps(selected))
    logger.info("Creating aimpoint config")

    if isinstance(mergeTemplate, dict):
        logger.info("Advanced selection found; merging configs")
        newConfig = deepMerge(baseTemplate, mergeTemplate)
        logger.info(f"Completed config merge for {newConfig["deviceID"]}")
    else:
        newConfig = _handleSettings(mergeTemplate, baseTemplate)

    return newConfig


def deepMerge(dict1: dict, dict2: dict) -> dict:
    """Merge two dicts, works with nested dicts as well"""
    merged = dict1.copy()
    for key, value in dict2.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = deepMerge(merged[key], value)
        else:
            merged[key] = value
    return merged


def pushAimpointToS3(newConfig: dict, s3Dir: str) -> None:
    """Push aimpoint JSON to S3"""
    deviceID = newConfig["deviceID"]
    outFile = os.path.join(config["workDirectory"], f"{deviceID}.json")

    try:
        ut.writeJsonDataToFile(newConfig, outFile)
    except Exception as err:
        logger.exception(f"Error creating aimpoint file:::{err}")
        raise HPatrolError("Error creating aimpoint file")

    wasPushed = GLOBALS.S3utils.pushToS3(
        outFile,
        s3Dir,
        config["defaultWrkBucket"],
        s3BaseFileName=f"{newConfig["deviceID"]}.json",
        deleteOrig=GLOBALS.onProd,
        extras={"ContentType": "application/json"}
    )
    if not wasPushed:
        raise HPatrolError(f"Error pushing '{deviceID}' to S3")


def _handleSettings(mergeTemplate, ap: dict) -> dict:
    """Handles simple settings for the selections file"""
    if mergeTemplate == "on":
        ap["collEnabled"] = True
        ap["decoy"] = False
    elif mergeTemplate == "decoy":
        ap["collEnabled"] = True
        ap["decoy"] = True
    elif mergeTemplate == "monitor":
        ap["collEnabled"] = True
        ap["decoy"] = False
    elif mergeTemplate == "monitor-decoy":
        ap["collEnabled"] = True
        ap["decoy"] = True
    elif mergeTemplate == "off":
        ap["collEnabled"] = False
    elif mergeTemplate == "mp4" or mergeTemplate == "monitor-mp4":
        ap["collEnabled"] = True
        ap["decoy"] = False
        ap["transcodeExt"] = "mp4"
    else:
        ap["collEnabled"] = False
        logger.error(f"Unknown setting '{mergeTemplate}' encountered for aimpoint ID '{ap["deviceID"]}'")

    return ap


def pickBestBucket(ap: dict, bucketKey: str) -> str:
    """Return the name of the work/dest bucket in use"""

    defaultBucketKeys = {
        "wrkBucket": "defaultWrkBucket",
        "dstBucket": "defaultDstBucket"
    }
    try:
        bucketName = ap[bucketKey]
        if not bucketName:
            bucketName = config[defaultBucketKeys[bucketKey]]       # Note this is a double-redirect
        else:
            logger.info(f"Using bucket '{bucketName}' as '{bucketKey}'")
    except KeyError:
        bucketName = config[defaultBucketKeys[bucketKey]]           # Note this is a double-redirect

    return bucketName


class FFMPEGType(IntEnum):
    """
    Doing this only to speed up comparison statements (ints instead of strings)
    It may also help in the future if we ever get to refactoring
    """
    STREAMING   = 0
    TRANSCODING = 1


class FFMPEGBuilder:
    ffmpeg = config["ffmpeg"]

    def  __init__(self, inputSource, outputFile, aimpointOptions=None):
        self.inputSource        = inputSource
        self.outputFile         = outputFile
    
        if aimpointOptions is None:
            self.aimpointOptions = {}
        else:
            self.aimpointOptions = dict(aimpointOptions)


    def input(self, options: dict):   
        if "input" in self.aimpointOptions:
            # logger.debug(self.aimpointOptions)
            self.aimpointOptions["input"] = {**options, **self.aimpointOptions["input"]}
        else:
            self.aimpointOptions["input"] = options    
        return self


    def output(self, options: dict):
        if "output" in self.aimpointOptions:
            self.aimpointOptions["output"] = {**options, **self.aimpointOptions["output"]}
        else:
            self.aimpointOptions["output"] = options
        return self


    def renderCommand(self) -> list:
        finalCommand = [config["ffmpeg"]]

        try:
            finalCommand += selectOptions(self.aimpointOptions, "input")
        except Exception:
            pass
        if self.inputSource != None:
            finalCommand += ["-i", self.inputSource]

        try:
            finalCommand += selectOptions(self.aimpointOptions, "output")
        except Exception:
            pass
        # if type == FFMPEGType.STREAMING:
        #     finalCommand.append("-strftime","1")
        if self.outputFile != None:
            finalCommand.append(self.outputFile)
        return finalCommand


def selectOptions(optionsDict: dict, optionKey: str) -> list:
    """Produce options lists"""
    try:
        return list(filter(None, dictToList(optionsDict[optionKey])))
    except KeyError:
        logger.warning(
            f"ffmpeg '{optionKey}' options not found"
        )
        return []


def dictToList(inputDict: dict = {}) -> list:
    """List comprehension to transform dict into flat list"""
    # Remove keys with null values
    noNone = [value for value in list(inputDict.items()) if None not in value]
    return [keyvalue for tuplePair in noNone for keyvalue in tuplePair]


def isThisAStillType(ap: dict) -> bool:
    # FIXME: Improve stills vs. videos identification
    #       Will cause a hard-to-find bug if new still types are added
    #       and this f() is not updated
    allStills = ["STILLS", "FSTLLS", "ISTLLS", "MSTLLS", "IMAGEINJSON"]
    if ap["collectionType"] in allStills:
        return True

    return False


def getPostApTemplate(baseTemplate, deliveryLz):
    """
    Returns a template for aimpoints that are to be post-processed, but not collected on
    This is specific to non-standard deployment environments
    """
    postTemplate = {
          "collEnabled": False
        , "accessUrl": "None"
        , "longLat": [0, 0]
        , "deliveryLzInput": deliveryLz
    }

    apTemplate = {**baseTemplate, **postTemplate}
    return apTemplate
