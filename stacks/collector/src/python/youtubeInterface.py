# External libraries import statements
import os
import time
import logging
import subprocess


# This application's import statements
from exceptions import *
import superGlblVars as GLOBALS
from superGlblVars import config
from orangeUtils import utils as ut
from utils import hPatrolUtils as hput


logger = logging.getLogger()


def _sentToBucket(theBucket, lzS3Prefix, finalFileName, fileNamePath):
    logger.info(f"Pushing to S3 as '{finalFileName}'")

    try:
        if os.path.isfile(fileNamePath):
            result = GLOBALS.S3utils.pushToS3(fileNamePath,
                                                lzS3Prefix,
                                                theBucket,
                                                s3BaseFileName=finalFileName,
                                                deleteOrig=GLOBALS.onProd)
            if result:
                logger.info(f"Pushed video: {finalFileName}")
                return True
            else:
                logger.error(f"Video file {finalFileName} was not pushed to S3!")
        else:
            logger.warning(f"Unable to push {finalFileName}; file not found: {fileNamePath}")
    except Exception:
        logger.warning(f"Unknown error trying to push {finalFileName}: {fileNamePath}")
    return False


def handleTube(prefixBase, ap):
    logger.info("Type selected: youtubeFile")

    try:
        devId = ap["deviceID"]
        videoUrl = ap["accessUrl"]
        filenameBase = ap["filenameBase"]
    except KeyError as err:
        logger.error("Parameter unspecified in input configuration")
        logger.error(f"Be sure to specify {err} in JSON file")
        raise HPatrolError("Parameter unspecified")

    wrkBucketName = hput.pickBestBucket(ap, "wrkBucket")

    if config["proxy"]:
        theProxy = config["proxy"]
    else:
        theProxy = ""

    filenameBase = f"{hput.formatNameBase(filenameBase, devId)}.mp4"
    try:
        ourFilename = hput.formatNameSuffix(filenameBase, ap["finalFileSuffix"], int(time.time()))
    except KeyError:
        ourFilename = hput.formatNameSuffix(filenameBase, "", int(time.time()))

    if GLOBALS.useTestData:
        testFile = "testVideo.ts"
        logger.debug(f"Reading from test file '{testFile}'")
        fileWithPath = f"{GLOBALS.testResources}/{testFile}"

    else:
        fileWithPath = os.path.join(config["workDirectory"], ourFilename)

        # yt-dlp command flags:
        # --merge-output-format: forces creation of .mp4
        # --geo-verification-proxy: attempt to bypass geo-blocking
        cmd = [
            "yt-dlp",
            "--proxy", theProxy,
            "--geo-verification-proxy", theProxy,
            "--verbose",
            "--merge-output-format", "mp4",
            "-o", fileWithPath, videoUrl
        ]
        try:
            subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
                timeout=300 # Prevent lambda from hanging forever if there is an issue
            )

        except subprocess.CalledProcessError as e:
            logger.error(f"Download failed with error: {e.stderr}")
        except FileNotFoundError as exc:
            logger.error(f"Process failed, executable or output file '{exc}' not found")
        except subprocess.TimeoutExpired as e:
            logger.error(f"Process timed out: {e}")

    isCollecting =  _sentToBucket(wrkBucketName, prefixBase, ourFilename, fileWithPath)
    GLOBALS.sqsUtils.sendMessage(config["statusQueue"], {"aimpoint": ap, "isCollecting": isCollecting})


def handleTubeStream(prefixBase, ap, lambdaContext):
    logger.info("Type selected: youtubeStream")

    breakPoint, theSleep, throwAway = hput.calculateExecutionStop(
        ap, lambdaContext
    )
    while True:
        _getStreamSegment(prefixBase, ap)
        # We're only intended to run once
        if not ap.get("singleCollector", False):
            logger.info(f"Not a singleCollector request; breaking out")
            break

        if hput.itsTimeToBail(lambdaContext, breakPoint, theSleep):
            break

    logger.info("Enough iterations for now")
    return


def _getStreamSegment(prefixBase, ap):
    try:
        devId = ap["deviceID"]
        streamUrl = ap["accessUrl"]
        filenameBase = ap["filenameBase"]
        fragmentDuration = ap["pollFrequency"]
    except KeyError as err:
        logger.error("Parameter unspecified in input configuration")
        logger.error(f"Be sure to specify {err} in JSON file")
        raise HPatrolError("Parameter unspecified")

    theProxy = ap.get("proxy", "")
    decoy = ap.get("decoy", False)
    wrkBucketName = hput.pickBestBucket(ap, "wrkBucket")

    ourFilename = hput.formatNameBase(f"{filenameBase}.ts", devId)
    filenameBase = hput.formatNameSuffix(ourFilename, "_{epoch}", int(time.time()))
    fileWithPath = os.path.join(config["workDirectory"], filenameBase)

    if GLOBALS.useTestData:
        testFile = "testVideo.ts"
        logger.debug(f"Reading from test file '{testFile}'")
        fileWithPath = f"{GLOBALS.testResources}/{testFile}"

    else:
        # fragment-retries: retries for errors when downloading a fragment
        # extractor-retries: retries for extractor errors (getting the playlist/metadata)
        # file-access-retries: retries for errors writing to local fs (not needed in lambda)
        # sleep-interval, max-sleep-interval: adds randomness to sleep behavior
        #   between fragment downloads
        # no-part: write directly to output file
        # ffmpeg external downloader: needed to download fragments instead of
        #   downloading the entire live stream until it ends
        cmd = [
            "yt-dlp",
            "--retries", "0",
            "--fragment-retries", "1",
            "--extractor-retries", "1",
            "--file-access-retries", "0",
            "--sleep-interval", "0",
            "--max-sleep-interval", "2",
            "--proxy", theProxy,
            "--geo-verification-proxy", theProxy,
            "--verbose",
            "--no-part",
            "--external-downloader", "ffmpeg",
            "--external-downloader-args", f"ffmpeg_i:-t {fragmentDuration} -reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
            "-o", fileWithPath, streamUrl
        ]

        logger.info(f"Running yt-dlp command: '{" ".join(cmd)}'")
        try:
            subprocess.run(
                cmd,
                check=True,
                capture_output=True,
                text=True,
                timeout=int(fragmentDuration) + 30 # Prevent lambda from hanging forever if there is an issue
            )
            logger.info(f"Segment '{filenameBase}' downloaded successfully")

        except subprocess.CalledProcessError as e:
            logger.error(f"Download failed; yt-dlp output follows:\n{e.stderr}")
        except FileNotFoundError as exc:
            logger.error(f"Process failed: {exc}")
        except subprocess.TimeoutExpired as e:
            logger.error(f"Process timed out: {e}")

    if os.path.exists(fileWithPath) and os.path.getsize(fileWithPath) > 0:
        if decoy:
            GLOBALS.sqsUtils.sendMessage(config["statusQueue"], {"aimpoint": ap, "isCollecting": True})
            logger.info(f"Decoy; NOT pushing to S3")
        else:
            isCollecting = _sentToBucket(wrkBucketName, prefixBase, filenameBase, fileWithPath)
            GLOBALS.sqsUtils.sendMessage(config["statusQueue"], {"aimpoint": ap, "isCollecting": isCollecting})

    else:
        GLOBALS.sqsUtils.sendMessage(config["statusQueue"], {"aimpoint": ap, "isCollecting": False})
