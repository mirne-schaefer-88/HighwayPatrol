"""
Module to manage the tasking to the transcoder

This can be run as a stand-alone python script to test
When run as stand-alone script, note that certain plumbing must be in place
"""

# External libraries import statements
import os
import re
import time
import json
import logging
import argparse
import threading
import datetime as dt
from pathlib import Path
from enum import IntEnum


# This application's import statements
import processInit
import systemSettings
from exceptions import *
import superGlblVars as GLOBALS
from superGlblVars import config
from orangeUtils import auditUtils
from utils import hPatrolUtils as hput
from collectionTypes import CollectionType
from orangeUtils.auditUtils import AuditLogLevel


class DroverTask(IntEnum):
    """
    Doing this only to speed up comparison statements (ints instead of strings)
    It may also help in the future if we ever get to refactoring
    """
    TRANSCODE = 1
    TIMELAPSE = 2
    TAKEAUDIO = 3


class DroverInterval(IntEnum):
    """
    Allowed intervals
    """
    ONE     = 1
    TWO     = 2
    THREE   = 3
    FOUR    = 4
    FIVE    = 5
    SIX     = 6
    TEN     = 10
    TWELVE  = 12
    FIFTEEN = 15
    TWENTY  = 20
    THIRTY  = 30
    SIXTY   = 60


logger = logging.getLogger()


def _shouldTranscode(interval:DroverInterval, minute):
    return minute % interval == 0


# Works when interval is a factor of 60 (1,2,3,4,5,6,10,12,15,20,30,60)
def _getLastIntervalSpan(vMin, interval:DroverInterval):
    vMinStart = int(vMin / interval) * interval - interval
    if vMinStart < 0:
        vMinStart = vMinStart + 60
    vMinEnd = vMinStart + interval
    return vMinStart, vMinEnd


def _calculateMinRange(now, interval):    
    nowInEpoch = int(now.timestamp())
    pMinStart, pMinEnd = _getLastIntervalSpan(now.minute, interval)
    logger.info(f"Interval set at {interval}mins; will get time range from :{pMinStart:02} to :{pMinEnd:02}")

    if now.minute < interval:
        targetDay = nowInEpoch - 1*60*60
    else:
        targetDay = nowInEpoch

    tDt = dt.datetime.fromtimestamp(targetDay, dt.UTC)
    # Notice we're using the pMinStart identified above
    tDtStr = f"{tDt.year} {tDt.month} {tDt.day} {tDt.hour} {pMinStart}"
    fromTime = dt.datetime.strptime(tDtStr, "%Y %m %d %H %M")
    fromTimeInEpoch = int(fromTime.timestamp())

    # Make clips at interval x seconds; ex. if 15m then video clips=900s, if 10m then video clips=600s
    toTime = interval * 60

    logger.info(f"Going from '{fromTimeInEpoch}' to '{fromTimeInEpoch + toTime}'")
    return tDt, fromTimeInEpoch, toTime


### DEPRECATED ###
def _getLast15mSpan(vMin):
    if vMin < 15:
        vMinStart = 45
        vMinEnd = 60
    elif vMin < 30:
        vMinStart = 0
        vMinEnd = 15
    elif vMin < 45:
        vMinStart = 15
        vMinEnd = 30
    elif vMin < 60:
        vMinStart = 30
        vMinEnd = 45
    return vMinStart, vMinEnd

### DEPRECATED ###
def _calculate15minRange(now):
    # This time-range function and complex hoop-jumps is being used because
    # we want 15minute-on-the-clock chunks, not just any arbitrary 15minute chunks

    # now = dt.datetime(2022,1,31,1,3)  # Test for new hour
    # now = dt.datetime(2022,1,31,0,3)  # Test for new day
    # now = dt.datetime(2022,1,1,0,3)   # Test for new year
    # now = dt.datetime(2022,2,1,0,3)   # Test for new month

    nowInEpoch = int(now.timestamp())
    pMinStart, pMinEnd = _getLast15mSpan(now.minute)
    logger.info(f"Will get time range from :{pMinStart:02} to :{pMinEnd}")

	# For every start of a new hour, use the previous hour
    if now.minute < 15:
        targetDay = nowInEpoch - 1*60*60
    else:
        targetDay = nowInEpoch

    tDt = dt.datetime.fromtimestamp(targetDay, dt.UTC)
    # Notice we're using the pMinStart identified above
    tDtStr = f"{tDt.year} {tDt.month} {tDt.day} {tDt.hour} {pMinStart}"
    fromTime = dt.datetime.strptime(tDtStr, "%Y %m %d %H %M")
    fromTimeInEpoch = int(fromTime.timestamp())

    # logger.debug(f"NOW IS  : {now.strftime('%Y-%m-%d %H:%M:%S')} ({nowInEpoch})")
    # logger.debug(f"PREVIOUS: {fromTime} ({fromTimeInEpoch})")
    toTime = 900     # Make 15min video clips (900s)

    logger.info(f"Going from '{fromTimeInEpoch}' to '{fromTimeInEpoch + toTime}'")
    return tDt, fromTimeInEpoch, toTime


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

    # Test input correctness
    try:
        test = event["task"]
    except KeyError:
            logger.error("Invalid message received")
            logger.debug(f"Message received is: {event}")
            return {"status": False}

    try:
        # Execute!
        wasGoodRun = True
        execute(event)
        exitMessage = "Normal execution"

    except Exception as e:
        logger.exception(f"UNHANDLED EXCEPTION CAUGHT:::{e}")
        systemLevel = AuditLogLevel.CRITICAL
        dataLevel = None
        wasGoodRun = False

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


def execute(taskConfig, s3Dir=None):
    # Identify ourselves for the audit logs
    GLOBALS.taskName = "Drover"
    logger.info(f"Received task: {json.dumps(taskConfig)}")

    # If no specific folder is requested, select all current aimpoints
    if not s3Dir:
        logger.info("No key specified; going after ALL aimpoints")
        apsList = hput.getAllAPs()
    else:
        logger.info(f"Looking for files in '{s3Dir}'")
        apsList = GLOBALS.S3utils.getFilesAsStrList(config["defaultWrkBucket"], s3Dir)
        # logger.debug(f"fileList:{fileList}")
        try:
            logger.info(f"Total aimpoints found:{len(apsList)}")
        except TypeError:
            raise HPatrolError("No aimpoints found")

    # Capture the current time in order to determine intervals
    now = dt.datetime.now(dt.UTC)

    try:
        # For when using CLI, or if we want to test lambda event
        now = dt.datetime.fromtimestamp(int(taskConfig["epoch"]), dt.UTC)
        logger.info(f"Now-time manually specified ({int(now.timestamp())}) {now}")
    except KeyError:
        pass

    if taskConfig["task"] == "transcode":
        theTask = DroverTask.TRANSCODE
        GLOBALS.subtaskName = "Transcode"
    elif taskConfig["task"] == "timelapse":
        theTask = DroverTask.TIMELAPSE
        GLOBALS.subtaskName = "Timelapse"
    elif taskConfig["task"] == "audio":
        theTask = DroverTask.TAKEAUDIO
        GLOBALS.subtaskName = "Audio"
    else:
        logger.error("Invalid task requested")
        raise HPatrolError("Invalid task")

    _sendTaskings(theTask, apsList, now)


def _getFileGroups(srcPrefix, filenameBase, bucketName):
    # Return a list of available filename groups without their epoch suffix
    # e.g. For cases where filenames are:
    #       <deviceID><someSuffix1>_<epoch>.jpg 
    #       <deviceID><someSuffix2>_<epoch>.jpg
    # We need this because normally, we would only know 
    # about <deviceID>_<epoch>.jpg

    fnamesList = GLOBALS.S3utils.getFilesAsStrList(
        bucketName,
        srcPrefix,
        onlyFilename=True)

    if not fnamesList:
        return []

    uniqueSet = set()
    # This pattern excludes the "_epoch" part of the filename
    regex = re.compile(rf"({filenameBase}(.*))(?:_\d*)(\....)")
    for testStr in fnamesList:
        match = regex.search(testStr)
        if match:
            # print(f"Match was found at {match.start()}-{match.end()}: {match.group()}")
            # for groupNum, group in enumerate(match.groups(), start=1):
            #     print(f"Group {groupNum} found at {match.start(groupNum)}-{match.end(groupNum)}: {group}")
            uniqueSet.add(f"{match.group(1)}")

    logger.info(f"Unique groups ({len(uniqueSet)}): {list(uniqueSet)}")
    return list(uniqueSet)


def _sendTaskings(theTask, apsList, now:dt.datetime):
    # Set default transcoder interval
    # Notice we start to focus on files as if we were "15 minutes ago".
    # This is because Collectors can overlap 15minute segments, and may still be collecting
    # The easiest way to think of this is by the following time-continuum (using Consolas font):
    #    <------------STABLE FILES------------> | <--MAY BE CURRENTLY BEING COLLECTED--> Now
    #   ┌───────────────────────────────────────┬─────────────────────────────────────────┐⇨⇨⇨
    #   30                                      15                                        0
    #   ⇦═══════════GRAB THESE FILES════════════╩══════════════════SKIPPED════════════════╝
    # Time example (with now being 10:15)
    # 09:45                                   10:00                                     10:15
    #
    # Please note that this causes a delay for *new* feeds processed to appear downstream
    # So even though data will be collected, it won't show up immediately but until after about 30mins

    # For each aimpoint file, read to see if transcoding is requested
    # TODO: Improve this for-loop; it's way too long; make sub-functions
    #       Like, separate and put first the weed-outs, then the set-ups, and later the actions
    for idx, apFile in enumerate(apsList, start=1):
        # Don't go through everything if we're not on PROD
        if not GLOBALS.onProd and idx == 2:
            logger.debug(f"Not running on PROD; exiting at file #{idx}")
            break

        logger.debug(f"Processing file '{apFile}'")
        contents = GLOBALS.S3utils.readFileContent(config["defaultWrkBucket"], apFile)
        try:
            ap = json.loads(contents)
        except Exception as e:
            logger.warning(f"Error processing input file; skipping:::{e}")
            logger.warning(f"Processing file '{contents}'")
            continue

        # Check if transcodeExt is null
        # Some aimpoints may be collEnabled=False but still have
        # data collected elsewhere and needing our post-processing
        try:
            if not ap["transcodeExt"]:
                logger.info("Transcoding disabled; skipping")
                continue
        except KeyError as e:
            # Not all aimpoints request transcoding
            # logger.info(f"Transcoding not requested; continuing")
            continue

        try:
            if ap["decoy"]:
                logger.info("Decoy; skipping")
                continue
        except KeyError as e:
            pass

        try:
            if ap["transcodeOptions"]:
                transcodeOptions = ap["transcodeOptions"]
        except KeyError as e:
            transcodeOptions = {}

        try:
            # transcoderInterval sets the time portion to focus on
            interval = DroverInterval(ap["transcoderInterval"])
        except (KeyError, ValueError):
            interval = GLOBALS.transcoderInterval

        # Determine if we are at a proper minute mark for transcoding
        if _shouldTranscode(interval, now.minute):
            customNowDtime = now - dt.timedelta(minutes=interval)
            tgtDay, fromTimeInEpoch, clipLen = _calculateMinRange(customNowDtime, interval)
        else:
            # logger.debug("Not transcoding yet")
            continue

        if theTask == DroverTask.TAKEAUDIO:
            try:
                if not ap["extractAudio"]["enabled"]:
                    logger.info("Audio extraction disabled; skipping")
                    continue
            except KeyError as e:
                continue

        wrkBucketName = hput.pickBestBucket(ap, "wrkBucket")
        dstBucketName = hput.pickBestBucket(ap, "dstBucket")

        # The audio processor has its own deliveryKey
        # so we can deliver both (vid and aud) to two places
        if theTask == DroverTask.TAKEAUDIO:
            try:
                deliveryKey = ap["extractAudio"]["deliveryKey"]
                if not deliveryKey:
                    deliveryKey = GLOBALS.audiosPlace
                else:
                    logger.info(f"Using aimpoint-specified audio deliveryKey '{deliveryKey}'")
            except KeyError:
                deliveryKey = GLOBALS.audiosPlace
        else:
            try:
                deliveryKey = ap["deliveryKey"]
                if not deliveryKey:
                    deliveryKey = GLOBALS.deliveryKey
                else:
                    logger.info(f"Using aimpoint-specified deliveryKey '{deliveryKey}'")
            except KeyError:
                deliveryKey = GLOBALS.deliveryKey

        try:
            ffmpegDedup = ap["ffmpegDedup"]
        except Exception:
            ffmpegDedup = GLOBALS.ffmpegDedup

        # Handle single-string input in the deliveryKey field
        if type(deliveryKey) is str:
            deliveryKey = deliveryKey.split()

        # Add a buffer to the front and back of the calculated timeframe
        # Notice that theFilename(s) will still retain the original fromTimeInEpoch time
        try:
            videoBuffer = int(ap["transcodedBuffer"])
        except Exception:
            videoBuffer = 10
        if videoBuffer > 30: videoBuffer = 30   # Cap the buffer at 30seconds
        startTime = fromTimeInEpoch - videoBuffer
        stopTime = clipLen + videoBuffer * 2    # x2 because we just cut the startTime

        filenameBase = hput.formatNameBase(ap["filenameBase"], ap["deviceID"])
        theFilename = hput.formatNameSuffix(f"{filenameBase}.{ap["transcodeExt"]}",
                                            ap["finalFileSuffix"],
                                            fromTimeInEpoch)
        logger.info(f"Processing '{filenameBase}'")

        # Prepare the task name for putting it on the queue
        # Want to keep the Transcoder independent so it can be used by other projects
        allTasks={
            DroverTask.TRANSCODE: "transcode", 
            DroverTask.TIMELAPSE: "timelapse", 
            DroverTask.TAKEAUDIO: "takeaudio"
        }
        taskWord = allTasks[theTask]

        theMsg = {
            "task": taskWord,
            "filenameBase": filenameBase,
            "outFilename": theFilename,
            "wrkBucket": wrkBucketName,
            "dstBucket": dstBucketName,
            "srcPrefix": "SETLATER",
            "dstPrefix": "SETLATER",
            "clipStart": startTime,
            "clipLengthSecs": stopTime,
            "ffmpegDedup": ffmpegDedup,
            "transcodeOptions": transcodeOptions
        }

        # Notice we want to zero-pad the numbers in the path
        resolvedTemplate = ap["bucketPrefixTemplate"].format(
            year=tgtDay.year,
            month=f"{tgtDay.month:02}",
            day=f"{tgtDay.day:02}",
            deviceID=ap["deviceID"]
            )

        isStillType = hput.isThisAStillType(ap)

        if theTask == DroverTask.TRANSCODE or theTask == DroverTask.TAKEAUDIO:
            if isStillType:
                # We don't transcode stills
                continue
            lz = ap.get("deliveryLzInput", GLOBALS.landingZone)
            theMsg["srcPrefix"] = f"{lz}/{resolvedTemplate}"

        elif theTask == DroverTask.TIMELAPSE:
            if not isStillType:
                # We don't do timelapse on non-stills
                continue
            # The system uses the yr/mnth/day/filenameBase/ construct for the stills landing zone
            fnBase = hput.formatNameBase(ap["filenameBase"], ap["deviceID"])
            stillsLzTemplate = "{year}/{month}/{day}/{fnBase}".format(
                year=tgtDay.year,
                month=f"{tgtDay.month:02}",
                day=f"{tgtDay.day:02}",
                fnBase=fnBase
                )
            lz = ap.get("deliveryLzInput", GLOBALS.stillImages)
            theMsg["srcPrefix"] = f"{lz}/{stillsLzTemplate}"

        for aDeliveryKey in deliveryKey:
            theMsg["dstPrefix"] = f"{aDeliveryKey}/{resolvedTemplate}"
            if theTask == DroverTask.TRANSCODE or theTask == DroverTask.TAKEAUDIO:
                logger.debug(f"Message: {json.dumps(theMsg)}")
                GLOBALS.sqsUtils.sendMessage(config["tcdQueue"], theMsg)

            elif theTask == DroverTask.TIMELAPSE:
                _sendTimelapseMessages(theMsg, ap, videoBuffer, now)


def _sendTimelapseMessages(theMessage, ap, videoBuffer, now):
    systemPeriodicity = GLOBALS.systemPeriodicity * 60

    # Add timelapse parameter (not used for transcoding)
    theMessage["timelapseFPS"] = ap["timelapseFPS"]

    # clipLen determines the time range of files to grab that make up the timelapse
    clipLen = ap["timelapseLen"]

    # If requesting a 24hr timelapse
    if clipLen >= 86400:
        # Check for a new day so we don't produce 24hr vids every system cycle
        today = now.day
        yestr = (now - dt.timedelta(minutes=GLOBALS.systemPeriodicity)).day
        if today == yestr:
            # logger.debug("Not a new day; ignore")
            return
        theMessage["clipStart"] = int((now-dt.timedelta(days=1)).timestamp())
    else:
        # If asking for less than 24hrs, make sure the suffix has time info
        finalFileSuffix = hput.addHrMinToSuffix(ap["finalFileSuffix"])

    offsetList = list(range(0, systemPeriodicity, clipLen))
    logger.info(f"Will request every {clipLen}secs; {len(offsetList)} requests total")

    # Add a buffer to the back of the calculated timeframe; # x2 because we also cut startTime
    clipLen = clipLen + videoBuffer * 2
    theMessage["clipLengthSecs"] = clipLen

    clipStart = theMessage["clipStart"]
    wrkBucketName = hput.pickBestBucket(ap, "wrkBucket")
    # FIXME: Don't look for fileGroups every time
    #        This is only and strictly a costing issue
    #        As of 2026.03.01 only the ISTLLS type needs it
    #        So we shouldn't be pulling the file list from S3 every time
    fileGroups = _getFileGroups(theMessage["srcPrefix"], theMessage["filenameBase"], wrkBucketName)

    for idx, theOffset in enumerate(offsetList, start=1):
        # Don't go through everything if we're not on PROD
        if not GLOBALS.onProd and idx == 5:
            logger.debug(f"Not running on PROD; exiting at request #{idx}")
            break

        newClipStart = clipStart + theOffset
        theMessage["clipStart"] = newClipStart

        for fnameBase in fileGroups:
            name = os.path.splitext(fnameBase)[0]
            # ext = os.path.splitext(fnameBase)[1] Not used
            theFilename = hput.formatNameSuffix(f"{name}.{ap["transcodeExt"]}",
                                                finalFileSuffix,
                                                newClipStart)
            theMessage["filenameBase"] = name
            theMessage["outFilename"] = theFilename

            logger.info(f"Sending '{theFilename}' "
                f"to {config["tcdQueue"]} queue"
            )
            logger.debug(f"Message: {json.dumps(theMessage)}")
            GLOBALS.sqsUtils.sendMessage(config["tcdQueue"], theMessage)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Drover for the Transcoder:\n"\
            "To send tasking to the Transcoder function",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("task",
                        help="task to execute",
                        choices=["transcode", "timelapse", "audio"],
                        )
    parser.add_argument("epoch",
                        help="start epoch suffix of the files on which to operate")
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

    # vvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvvv
    # Can use this portion here if we ever need to reprocess a bunch
    # Note that these times here are local to this machine (not necesarily UTC)
    # s3Dir = "aimpoints/orionnet.online"
    # humanTimeFrom = "2025-01-21 20:00:00"
    # humanTimeTo   = "2025-01-30 16:30:00"
    # 
    # dtObj = dt.datetime.strptime(humanTimeFrom, "%Y-%m-%d %H:%M:%S")
    # fromHere = int(dtObj.timestamp())
    # fromHere = fromHere + 1800  # fix to start time on the requested
    # dtObj = dt.datetime.strptime(humanTimeTo, "%Y-%m-%d %H:%M:%S")
    # toHere = int(dtObj.timestamp())
    # toHere = toHere + 2700  # fix to end time on the requested
    # while fromHere < toHere:
    #     args.epoch = str(fromHere)
    #     event = {"task": args.task, "epoch": args.epoch}
    #     try:
    #         execute(event, s3Dir)
    #     except HPatrolError as err:
    #         logger.error(err)
    #     fromHere = fromHere + 900
    # Be sure to comment out the portion below that calls execute()
    # and change the Transcoding queue (tcdQueue) on systemSettings
    # ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

    event = {"task": args.task, "epoch": args.epoch}
    try:
        execute(event)
    except HPatrolError as err:
        logger.error(err)

    nownow = int(time.time())
    logger.info(f"Process clocked at {str(dt.timedelta(seconds=nownow-upSince))}")

    toPrint = f"Exiting Process: {threading.main_thread().name}"
    logger.info(f"= {toPrint} =")
    logger.info(f"=={'=' * len(toPrint)}==")
