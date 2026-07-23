# This module holds all the "super-global" shtuff
# Very similar to the settings.py file, in terms of accesibility of values to all the code
# but this one is not to be edited dependent on different configurations

# Make the configuration in systemSettings.py available to all
config = {}

# Specify whether we are running in PRODUCTION or not
# This also affects how many pages we go through on the target site; if we're not on
# production, we don't go through all when paginating
onProd = False

# Self-awareness for audit logs, and some CDK stack settings
projectName = "hPatrol"

# Each audit-writing element in the system (e.g. lambda) should set these for themselves
# To help in identification and reporting of each part
taskName= None
subtaskName= None

# When in test, system won't reach out on the net, but will instead
# use the files in the testResources directory
useTestData = False

# Set testResources directory; only used during testing
testResources = "testResources"

# AWS access objects
S3utils = None

# Determines if use_ssl should be set to True or False for S3 operations
useSslS3 = True

# The IP address used when executing
perceivedIP = None

# This system's software version
myVersion = None

# AWS system's ARN where we're running from (lambda or EC2)
myArn = None

# The http session object to enable re-use between separate calls
netUtils = None

# Number of parallel threads to use when uploading segments to S3
upThreads = 4

# Default FFMPEG deduplication mechanism
ffmpegDedup = None

# Default transcoder segment interval in minutes
# Only factors of 60 are accepted; i.e. 1,2,3,4,5,6,10,12,15,20,30,60
transcoderInterval = 15

# How often is the system expected to wake up (in minutes)
# This number must match the cron wake-up for the system Scheduler
systemPeriodicity = 10

# Range of time in seconds that the enabler lambda analyzes for successful collections
enablerLookBack = 600      # 600s == 10m

# Range of time in seconds that the disabler lambda analyzes for failed collections
disablerLookBack = 1800      # 1800s == 30m

# Frequency in hours to process aimpoints set to "monitor" status
# Each aimpoint can set its own frequency with the monitorFrequency parameter
monitorFrequency = 12

# Max number of retries for etag conditional put_object operation in S3
s3ConditionalRetries = 3

# Max number of collection results to retrieve from aimpointStatus/ in S3
# when checking to enable or disable aimpoints
collResultLimit = 500

# AWS S3 bucket key-prefixes; some are used as inputs, some as outputs
selectTrgts = 'OUTBOX/hp/selections'  # individual devices selected; used for the aimpoint producers
targetFiles = 'OUTBOX/hp/aimpoints'   # config files indicating what we're going after
monitorTrgt = 'OUTBOX/hp/monitored'   # config files for down devices that are periodically monitored
landingZone = 'OUTBOX/hp/lz'          # start point for videos; collected videos go here
stillImages = 'OUTBOX/hp/stillsLz'    # start point for still images; collected stills go here
deliveryKey = 'OUTBOX/hp/up'          # default downstream delivery prefix; can be overriden by aimpoint
audiosPlace = 'OUTBOX/hp/audios'      # default downstream audio delivery prefix; overriden by aimpoint
s3Hashfiles = 'OUTBOX/hp/hashfiles'   # md5 hash files of collected data for deduplication
mtdtReports = 'OUTBOX/hp/0_Metadata'  # available devices' historical data and reports
hpResources = 'OUTBOX/hp/resources'   # resources to aid in system execution (e.g. mitmproxy-ca.pem file)
aimpointSts = 'OUTBOX/hp/aimpointStatus' # collection status for all aimpoints (success/fail)
dashboardLz = 'OUTBOX/hp/dboard'      # data used for the status dashboards

# When running as a lambda, the received 'context'
lambdaContext = None

# Our stack name for the Dispatcher to call the correct Collector
# This is used internally and during deployment as a CDK parameter
baseStackName = "highwayPatrol"
