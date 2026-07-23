"""
Aimpoint generator for ufanet
Many things in this script are hardcoded because it's for a specific domain.

"""

# External libraries import statements
import os
import glob
import time
import logging
import argparse
import threading
import datetime as dt
from pathlib import Path


# This application's import statements
import processInit
import systemSettings
from exceptions import *
import comparitor as comp
import superGlblVars as GLOBALS
from superGlblVars import config
from orangeUtils import auditUtils
from orangeUtils import utils as ut
from utils import hPatrolUtils as hput
from orangeUtils.auditUtils import AuditLogLevel


logger = logging.getLogger()

# Constants
DOMAIN = "ufanetVideos"
START_URL = "http://maps.ufanet.ru"


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

    wasGoodRun = False
    try:
        # Execute!
        if execute(upSince, False):
            wasGoodRun = True
            exitMessage = "Normal execution"

    except Exception as e:
        logger.exception(f"UNHANDLED EXCEPTION CAUGHT:::{e}")
        systemLevel = AuditLogLevel.CRITICAL
        wasGoodRun = False
        dataLevel = None

    finally:
        nownow = int(time.time())
        logger.info(f"Process clocked at {str(dt.timedelta(seconds=nownow-upSince))}")

        # Need to reset proxy; lambdas can keep memory
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
            # **collectionSummaryArgs
            # collectionSummaryArgs1="some",
            # collectionSummaryArgs2="additional",
            # collectionSummaryArgs3="info"
            )

    toPrint = "Exiting Process"
    logger.info(f"= {toPrint} =")
    logger.info(f"=={'=' * len(toPrint)}==")

    return {"status": wasGoodRun}


def _allInputsValid(camSpec):
    if camSpec["regionName"] == "":
        return False
    if camSpec["longitude"] == "":
        return False
    if camSpec["latitude"] == "":
        return False
    if camSpec["id"] == "":
        return False

    return True


def _cleanUpWorkDirectory():
    for filename in Path(config["workDirectory"]).glob("*.json"):
        logger.info(f"Deleting jsons in {config["workDirectory"]}")
        filename.unlink()

    for filename in Path(config["workDirectory"]).glob("*.tsv"):
        logger.info(f"Deleting tsvs in {config["workDirectory"]}")
        filename.unlink()

    for filename in Path(config["workDirectory"]).glob("*.html"):
        logger.info(f"Deleting htmls in {config["workDirectory"]}")
        filename.unlink()


# This function helps with pulling the HTMLs into the work directory
def _getRegionUrls():
    # Get the regions shown on the main page
    urls = []
    mainUrl = START_URL

    r = GLOBALS.netUtils.get(mainUrl)
    filepath = f"{config["workDirectory"]}/ufa.html"
    with open(filepath, "w") as wfd:
        wfd.write(str(r.text))

    with open(filepath, "r") as rfd:
        for line in rfd:
            if "<li><a tabindex=\"-1\"" in line:
                link = line.split("href=")[1].split(">")[0]
                region = link[1:-1]
                url = mainUrl + region
                urls.append(url)                

    return urls


# This function downloads the HTMLs
# Writes files in the work directory and returns a list of them
def _writeToFiles():
    allList = []

    if GLOBALS.useTestData:
        testFile = "Maps.UfaNet.ru.html"
        fileWithPath = f"{GLOBALS.testResources}/{testFile}"
        regionDict = {}
        regionDict["url"] = "http://maps.ufanet.ru/ufa"
        regionDict["filepath"] = fileWithPath
        allList.append(regionDict)

    else:
        # Delete the old downloads from the work directory
        _cleanUpWorkDirectory()

        regionUrls = _getRegionUrls()
        for eachUrl in regionUrls:
            regionDict = {}
            # regionName is used to make a file path
            regionName = eachUrl.split("ru/")[1].split("\"")[0]
            r = GLOBALS.netUtils.get(eachUrl)
            localFile = os.path.join(f"{config["workDirectory"]}/{regionName}.html")
            with open(localFile, "w") as f:
                f.write(str(r.text))

            regionDict["url"] = eachUrl
            regionDict["filepath"] = localFile
            allList.append(regionDict)

    return allList


# Processes only one HTML file
def _getPopulationFromHtml(htmlFile):
    camPopulationList = []

    # Reads the entire file first to check for "L.marker"
    # If it exists, move the file pointer to the beginning and
    # split at "L.marker" into a list (of strings)
    # otherwise file doesn't have data and returns empty
    with open(htmlFile, "r") as f:
        if ("L.marker") in f.read():
            f.seek(0)
            htmlContents = f.read().split("L.marker")
        else:
            return None

    # Concatenate "L.marker" to the beginning of each string to aid in parsing lon/lat
    # If the string has "<!DOCTYPE html>", there is no metadata, ignore 
    # If the string has "marker.number" as empty, there is no metadata, ignore
    # Otherwise go ahead and parse
    for i in htmlContents:
        i = "L.marker " + i
        if "<!DOCTYPE html>" in i:
            continue
        if "marker.number = ''" in i:
            continue
        if "L.marker" in i:
            parseStr = i.split("(")[1].split("]")[0].strip("[").replace(" ", "")
            lon = parseStr.split(",")[0]
            lat = parseStr.split(",")[1]
            camDict = {}
            # Catch empty lon/lat
            if lon:
                 camDict["longitude"] = lon
            else:
                 camDict["longitude"] = "0"
            if lat:
                 camDict["latitude"] = lat
            else:
                 camDict["latitude"] = "0"

            if GLOBALS.useTestData:
                camDict["regionName"] = "ufa"
            else:
                camDict["regionName"] = htmlFile.split(".")[0].split("/")[3]

        if "marker.name" in i:
            camDict["name"] = i.split("marker.name", 1)[1].split("'", 1)[1].split("'")[0]
        if "marker.server" in i:
            camDict["server"] = i.split("marker.server", 1)[1].split("'", 1)[1].split("'")[0]
        if "marker.number" in i:
            camDict["id"] = i.split("marker.number", 1)[1].split("'", 1)[1].split("'")[0]
        if "marker.token" in i:
            camDict["token"] = i.split("marker.token", 1)[1].split("'", 1)[1].split("'")[0]
        
        camPopulationList.append(camDict)

    return camPopulationList


# Call _getPopulationFromHtml() for each region
# to build a final list (of dicts) of all regions and their cameras
# Returns a list of dicts 
def _getPopulation():
    ufaList = []

    # htmls is a list of dicts - "url" and "filePath"
    htmls = _writeToFiles()  
    for html in htmls:
        try:
            regionData = _getPopulationFromHtml(html["filepath"])
        except KeyError:
            raise HPatrolError(f"'filepath' key is missing")
        # Make a list only if the region data is not empty
        if regionData:
            ufaDict = {}
            ufaDict["regionUrl"] = html["url"]  
            ufaDict["regionData"] = regionData
            ufaList.append(ufaDict)
    
    return ufaList


def execute(upSince, forceCreation):
    # Identify ourselves for the audit logs
    GLOBALS.taskName = "Generator"
    GLOBALS.subtaskName = "Ufanet"
    selectionsFile = f"selected-{DOMAIN}.json"

    config["proxy"] = "ru-protonvpn.flurri.dom:3128"
    processInit.initSessionObject(config["sessionHeaders"])

    try:
        allPopulation = _getPopulation()
    except HPatrolError as err:
        logger.exception(f"Error getting target population:::{err}")
        return False

    try:
        videosSelection = hput.getSelection(selectionsFile)
    except HPatrolError as err:
        return False

    structTitles = (
          "ID"
        , "Longitude"
        , "Latitude"
        , "RegionName"
        , "Name"
        , "Server"
        , "Token"
    )

    structKeys = (
          "id"
        , "longitude"
        , "latitude"
        , "regionName"
        , "name"
        , "server"
        , "token"
    )

    apTemplate = _getApTemplate()
    domainFolder = comp.getDomainFolder(apTemplate)

    # If aimpoint creation is forced, comparison is NOT done
    # Else, comparison *is* done, and the master file and the
    # aimpoint files are re-created as necessary
    shouldWriteAimpoints = False
    if forceCreation:
        shouldWriteAimpoints = True
    else:
        combinedList = []
        regionDataList = []

        # Combine metadata from each region into a single list
        for i in allPopulation:
            regionDataList.append(i["regionData"])
        for j in regionDataList:
            combinedList.extend(j)

        try:
            shouldWriteAimpoints = comp.writeAPs(
                    upSince,
                    combinedList,
                    (structKeys, structTitles),
                    domainFolder,
                    "rptUfanetMasterIdlist",
                    selectedList=videosSelection)
        except HPatrolError:
            logger.exception("Unable to do ID comparison")
            return False

    if shouldWriteAimpoints:
        # Delete old aimpoints once before each cycle
        # A cycle processes all the regions resulting in multiple aimpoints per cycle
        theKey = f"{DOMAIN}-autoParsed"
        aimpointDir = f"{GLOBALS.targetFiles}/{theKey}"
        monitoredDir = f"{GLOBALS.monitorTrgt}/{theKey}"
        GLOBALS.S3utils.deleteEntireKey(config["defaultWrkBucket"], aimpointDir)
        GLOBALS.S3utils.deleteEntireKey(config["defaultWrkBucket"], monitoredDir)
        
        try:
            for eachRegionPopulation in allPopulation:
                try:
                    populationUrl = eachRegionPopulation["regionUrl"]
                    populationList = eachRegionPopulation["regionData"]
                except KeyError as err:
                    logger.error(f"{err} not specified")
                    raise HPatrolError(f"{err} key is missing")
                _doVideos(videosSelection, populationUrl, populationList, theKey, apTemplate)
        except HPatrolError as err:
            logger.exception(f"Error creating aimpoints:::{err}")
            return False

    return True


def _doVideos(selection, urlAddress, allCams, theKey, apTemplate):
    # Keep track of how many aimpoints we actually make
    aCounter = 0
    expected = len(selection)
    logger.info(f"Creating aimpoint files on {expected} devices")
  
    for aCam in allCams:
        if _allInputsValid(aCam):
            try:
                camId = aCam["id"]
            except KeyError:
                raise HPatrolError(f"'id' key is missing")
            theID = str(camId)
            if theID in selection:
                try:
                    selectionState = selection[theID] if isinstance(selection[theID], str) else selection[theID]["monitoringData"]["selectionsState"]
                except KeyError as e:
                    logger.error(f"Missing key {e} for id {theID} in selections file; skipping")
                    continue
                logger.info(f"Creating JSON file for ID:{theID}")
                apTemplate["deviceID"] = theID
                try:
                    regionName = aCam["regionName"]
                except KeyError:
                    raise HPatrolError(f"'regionName' key is missing")
                apTemplate["accessUrl"] = f"{urlAddress}#{theID}"
                try:
                    lon = aCam["longitude"]
                except KeyError:
                    logger.warning("Longitude not found")
                longitude = float(lon)
                try:
                    lat = aCam["latitude"]
                except KeyError:
                    logger.warning("Latitude not found")
                latitude = float(lat)
                apTemplate["longLat"] = [longitude, latitude]
                apTemplate["bucketPrefixTemplate"] = f"ru/ufanet/{theID}/{{year}}/{{month}}/{{day}}"
                apTemplate["devNotes"]["region"] = regionName
                if selectionState == "decoy" or selectionState == "monitor-decoy":
                    apTemplate["decoy"] = True
                else:
                    apTemplate["decoy"] = False

                outFile = os.path.join(config["workDirectory"], f"{theID}.json")
                try:
                    ut.writeJsonDataToFile(apTemplate, outFile)
                except Exception as err:
                    logger.exception(f"Error creating aimpoint file:::{err}")
                    raise HPatrolError("Error creating aimpoint file")

                s3Dir = f"{GLOBALS.targetFiles}/{theKey}"
                if "monitor" in selectionState:
                    s3Dir = f"{GLOBALS.monitorTrgt}/{theKey}"

                if GLOBALS.S3utils.pushToS3(outFile,
                                        s3Dir,
                                        config["defaultWrkBucket"],
                                        s3BaseFileName=f"{theID}.json",
                                        deleteOrig=GLOBALS.onProd,
                                        extras={"ContentType": "application/json"}):
                    aCounter += 1

    if aCounter != expected:
        logger.warning(f"Created {aCounter} aimpoints out of the expected {expected}")


def _getApTemplate():
    apTemplate = {
          "deviceID": "SETLATER"
        , "collEnabled": True
        , "decoy": "SETLATER"
        , "collRegions": ["US East (N. Virginia)"]
        , "collRegionsOriginallyIn": ["Europe (Frankfurt)"]
        , "collectionType": "UFANET"
        , "proxy": "ru-protonvpn.flurri.dom:3128" 
        , "accessUrl": "SETLATER"
        , "pollFrequency": 28
        , "concatenate": False
        , "transcodeExt": "mp4"
        , "filenameBase": "{deviceID}"
        , "finalFileSuffix": "_{year}-{month}-{day}-{hour}-{mins}-{secs}"
        , "bucketPrefixTemplate": "ru/ufanet/{deviceID}/{year}/{month}/{day}"
        , "longLat": "SETLATER"
        , "headers": {
              "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:109.0) Gecko/20100101 Firefox/115.0"
            , "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
            , "Accept-Encoding": "gzip, deflate"
            , "DNT": "1"
            , "Accept-Language": "en-US,en;q=0.5"
            , "Connection": "keep-alive"
            , "Upgrade-Insecure-Requests": "1"
            , "Sec-GPC": "1"
            , "Host": "maps.ufanet.ru"
            }
        , "devNotes": {
              "givenURL": "https://maps.ufanet.ru"
            , "startedOn": "August 2024"
            , "missionTLDN": "ru"
            , "setBy": "who originally worked it"
            }
        }
    return apTemplate


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Aimpoint generator for videos",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument(
        "-f",
        "--force",
        required=False,
        action="store_true",
        help=(
            "force the creation of aimpoints\n"\
            "If aimpoint creation is forced, comparison is NOT done\n"\
            "Else, comparison *is* done, and the master file and\n"\
            "aimpoints are created if necessary"
        )
    )
    args = parser.parse_args()

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

    if args.force:
        logger.info("Forcing aimpoints creation; won't execute comparitor")
        execute(upSince, True)
    else:
        execute(upSince, False)

    nownow = int(time.time())
    logger.info(f"Process clocked at {str(dt.timedelta(seconds=nownow-upSince))}")

    toPrint = f"Exiting Process: {threading.main_thread().name}"
    logger.info(f"= {toPrint} =")
    logger.info(f"=={'=' * len(toPrint)}==")
