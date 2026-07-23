#
# System configuration parameters
#


# External libraries import statements
import os


# This application's import statements
from superGlblVars import config
from systemMode import SystemMode


# Determine whether we're running on lambda or not
onLambda = "AWS_LAMBDA_FUNCTION_NAME" in os.environ

# Where to put all of our working files
config["workDirectory"] = "/tmp/highwaypatrol"
config["logsDirectory"] = os.path.join(config["workDirectory"], "logs")

# Names of environment variables needed; to check if the vars are set
# Note that the value here is the env var's name, NOT the value of the variable
# This is done as a helper to the CDK script since we don't know the element's name until CDK runs
# unless, of course, we hard-code it in the stack script, and in most situations, we don't want that
config["stsQueueVarName"] = "HPatrolStatusQueue"
config["bagQueueVarName"] = "HPatrolBaggingQueue"
config["disQueueVarName"] = "HPatrolDispatchQueue"
config["tcdQueueVarName"] = "HPatrolTranscodeQueue"

# System queues; can be overriden by environment variables
# The overriding happens in processInit.py
config["bagQueue"] = "highwayPatrol_hPatrolBagging"
config["disQueue"] = "highwayPatrol_hPatrolDispatch"
config["tcdQueue"] = "highwayPatrol_hPatrolTranscode"
config["statusQueue"] = "highwayPatrol_hPatrolStatus"


# Location of FFMPEG executables
if onLambda:
    config["ffmpeg"] = "/opt/bin/ffmpeg"
    config["ffprobe"] = "/opt/bin/ffprobe"
else:
    config["ffmpeg"] = "/bin/ffmpeg"
    config["ffprobe"] = "/bin/ffprobe"


# Note that there are 2 buckets to be specified
    # config["defaultWrkBucket"] indicates the working bucket
    # config["defaultDstBucket"] indicates destination for deliveries

# Amazon Web Services config
if config["mode"] == SystemMode.PROD:
    config["awsProfile"] = "wormhole-mission-data-morganh"
    config["defaultWrkBucket"] = "wormhole-mission-data-morganh"
    config["defaultDstBucket"] = "wormhole-mission-data-morganh"
else:
    config["awsProfile"] = ""
    config["defaultWrkBucket"] = "wormhole-mission-data-nikolaiv"
    config["defaultDstBucket"] = "wormhole-mission-data-nikolaiv"

# Other deployments' locations (aimpoints, monitored, etc.)
config["otherAps"] = ["flynnl/mission_data/hp", "morganh/mission_data/hp"]

# Here order matters; this MUST go after the config["awsProfile"] specifications above
if onLambda:
    config["awsProfile"] = False


# Default session headers; user-agent strings are added in processInit for randomization
config["sessionHeaders"] = {
    "Connection": "keep-alive",
    "Cache-Control": "max-age=0",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8"
}

# Proxy to use during requests' library connections
# If no proxy is to be used, use value False
# Note that this can change later per aimpoint
config["proxy"] = "mendeleev.whirl.dom:14400"
if onLambda:
    config["proxy"] = False

# Site to check our IP
config["chkIpURL"] = "https://0yjmfrxhl0.execute-api.us-east-1.amazonaws.com"
# config["chkIpURL"] = "https://whatsmyip.com/api/ip-info"
# config["chkIpURL"] = "https://ipinfo.io/json"
