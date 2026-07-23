# Python libraries import statements
import os
import pathlib
import logging


# AWS import statements
import boto3
from constructs import Construct
from aws_cdk import aws_s3 as s3
from aws_cdk.aws_sqs import Queue
from aws_cdk import RemovalPolicy
from aws_cdk import aws_logs as logs
from aws_cdk.aws_lambda import Runtime
from aws_cdk import Duration, Stack, Size
from aws_cdk.aws_logs import RetentionDays
from aws_cdk.aws_sqs import QueueEncryption
from aws_cdk import aws_s3_notifications as s3n
from aws_cdk.aws_events_targets import LambdaFunction
from aws_cdk.aws_lambda_python_alpha import PythonFunction
from aws_cdk.aws_lambda_python_alpha import BundlingOptions
from aws_cdk.aws_lambda_event_sources import SqsEventSource
from aws_cdk.aws_events import Rule, Schedule, RuleTargetInput


# This application's import statements
from . import commonStackFunctions as csf
from superGlblVars import config
from superGlblVars import projectName
from superGlblVars import targetFiles


logger = logging.getLogger()
logging.basicConfig(format="%(asctime)s - %(levelname)-8s - %(module)s:%(lineno)d - %(message)s")

# Get both sets of config settings (for the system and deployment)
executionMode = config["mode"]
cdkConfig = csf.initCdkSettings()
# Stupid DynaConf relies on environment variables
os.environ["ENV_FOR_DYNACONF"] = executionMode.name


class HPatrolProcessingStack(Stack):
    cwd = str(pathlib.Path.cwd())

    def __init__(self,
        profile: str,
        stackName: str,
        description: str,
        baseStackName: str,
        scope: Construct,
        **kwargs) -> None:
        super().__init__(scope, stackName, description=description, **kwargs)

        env = None
        csf.loggerSetup()
        self.stackName = stackName
        self.baseStackName = baseStackName

        logger.info(f"Stack ID: {self.stackName}")

        if "env" in kwargs:
            env = kwargs.get("env")

        try:
            acctInfo = csf.getAccountInfo(env, profile)
        except KeyError as err:
            return None

        accountNumber = acctInfo["accountId"]
        logger.info(f"Account#: {accountNumber}")
        regionName = acctInfo["region"]
        logger.info(f"Region: {regionName}")

        # # Just for debugging; enable when necessary
        # csf.printAllAccounts(regionName)

        try:
            self.botoSession = boto3.Session(
                profile_name=profile,
                region_name=regionName
            )
            # Test if credentials are valid
            if "BP" in os.environ:
                # The "BP" env variable indicates a specific enviroment
                # Don't do list_aliases() in certain environments
                self.botoSession.client("kms").list_aliases()
        except Exception as exc:
            logger.error(exc)
            logger.error("Will now exit... (this may hang; go ahead and Ctrl+C)")
            return None

        # Need lambda client to find out latest layer versions
        self.lambdaClient = self.botoSession.client("lambda")

        # Apply the BoundaryPolicy to the entire stack
        csf.applyBoundaryPolicy(self)

        self._lambdaRole = csf.createLambdaRoles(self)
        self._createQueues()
        self._createLambdas()
        # This CDK recipe assumes that any S3 buckets necessary are already created


    def _createQueues(self) -> None:
        # The "BP" env variable indicates a specific enviroment
        # Don't need all queues in all environments
        if "BP" in os.environ:
            self._createBaggingQueue()
        self._createDispatchQueue()
        self._createTranscodingQueue()


    def _createBaggingQueue(self) -> None:
        # Construct the SQS Queue
        self._baggingQueue = Queue(
            self, "HPatrolBaggingQueue",
            queue_name=self.baseStackName + "_" + projectName + "Bagging",
            encryption=QueueEncryption.KMS_MANAGED,
            visibility_timeout=Duration.hours(2)
        )
        logger.debug("Bagging queue defined")


    def _createTranscodingQueue(self) -> None:
        # Construct the SQS Queue
        self._transcodeQueue = Queue(
            self, "HPatrolTranscodeQueue",
            queue_name=self.baseStackName + "_" + projectName + "Transcode",
            encryption=QueueEncryption.KMS_MANAGED,
            visibility_timeout=Duration.hours(2)
        )
        logger.debug("Transcoding queue defined")


    def _createDispatchQueue(self) -> None:
        # Construct the SQS Queue
        self._dispatchQueue = Queue(
            self, "HPatrolDispatchQueue",
            queue_name=self.baseStackName + "_" + projectName + "Dispatch",
            encryption=QueueEncryption.KMS_MANAGED,
            visibility_timeout=Duration.hours(2)
        )
        logger.info("Dispatch queue defined")


    def _createLambdas(self) -> None:
        # Import/create the Lambda Layer for dependencies
        reqsFile = f"{self.cwd}/stacks/systemResources/lambdaRequirements.txt"
        self._dependsLayer = csf.createDependenciesLayer(self, reqsFile)
        ffmpegLayer = csf.getLatestLayerVersion(self, self.lambdaClient, cdkConfig["ffmpegIsolated"])
        ffprobeLayer = csf.getLatestLayerVersion(self, self.lambdaClient, cdkConfig["ffprobeIsolated"])
        logger.info("Lambda layers created")

        # Construct the Lambdas
        components = [
              "drover"
            , "minion"
            , "marshal"
            , "dboarder"
            , "scheduler"
            , "transcoder"
            , "dispatcher"
        ]
        self.buildDirs = {} # Pointer to where system components are built prior to uploading
        compsDir = f"{self.cwd}/stacks"
        csf.createLambdasStructure(self, compsDir, components, executionMode)

        # The "BP" env variable indicates a specific enviroment
        # Post-processing is only needed in certain environments
        if "BP" in os.environ:
            self._createMinionLambda("minion")
            self._createDroverLambda("drover")
            self._createMarshalLambda("marshal")
            self._createDBoarderLambda("dboarder")
            self._createTranscoderLambda("transcoder", ffmpegLayer, ffprobeLayer)
        self._createSchedulerLambda("scheduler")
        self._createDispatcherLambda("dispatcher")


    def _createSchedulerLambda(self, buildDir) -> None:
        theLambda = PythonFunction(self, "schedulerLambda",
            description="Distributes work to the Dispatcher to start work",
            entry=self.buildDirs[buildDir],
            index="main.py",
            role=self._lambdaRole,
            runtime=Runtime.PYTHON_3_13,
            handler="lambdaHandler",
            bundling=BundlingOptions(
                asset_excludes =["__pycache__", "*.pyc", ".pytest_cache"],
            ),
            function_name=self.baseStackName + "_Scheduler",
            memory_size=512,
            # Using "unreserved account concurrency"
            timeout=Duration.minutes(15),
            retry_attempts=1,
            log_retention=RetentionDays.THREE_MONTHS,
            layers=[
                self._dependsLayer
            ],
            environment={
                "stackName": self.stackName,
                "HPatrolDispatchQueue": self._dispatchQueue.queue_url
            }
        )
        logger.info("Scheduler lambda defined")

        # Connect the Subscription Filters for the Audit Service
        csf.makeLogSubscriptionFilter(
            stackObj=self,
            executionMode=executionMode,
            auditAccount=cdkConfig["AUDIT_ACCOUNT"],
            lambdaObj=theLambda)

        # Create the "fireing" rule
        # AWS cron expressions have the following format:
        #   cron(Minutes Hours Day-of-month Month Day-of-week Year)
        # Note that you cannot supply both "day" and "weekDay" at the same time; it will error out
        # Running every 10mins
        cronRule = Rule(
            self, "SchedulerRule",
            schedule=Schedule.cron(
                minute="00/10",
                hour="*",
                # day="?",
                month="*",
                week_day="*",
                year="*"),
            enabled=False # Disabled by SOP; to be enabled after deployment
        )
        tgtInput = {"dummy": "event input sent to the lambda"}
        cronRule.add_target(LambdaFunction(theLambda, event=RuleTargetInput.from_object(tgtInput)))
        logger.info("Cron rule defined")


    def _createDispatcherLambda(self, buildDir) -> None:
        #Create an SQS event source for Lambda
        sqsEventSource = SqsEventSource(self._dispatchQueue, batch_size=1)

        theLambda = PythonFunction(self, "dispatcherLambda",
            description="Invokes the Collector lambdas based on instructions received",
            entry=self.buildDirs[buildDir],
            index="main.py",
            role=self._lambdaRole,
            runtime=Runtime.PYTHON_3_13,
            handler="lambdaHandler",
            bundling=BundlingOptions(
                asset_excludes =["__pycache__", "*.pyc", ".pytest_cache"],
            ),
            function_name=self.baseStackName + "_Dispatcher",
            memory_size=1024,
            # Using "unreserved account concurrency"
            events=[sqsEventSource],
            timeout=Duration.minutes(15),
            retry_attempts=0,
            log_retention=RetentionDays.THREE_MONTHS,
            layers=[
                self._dependsLayer
            ],
            environment={
                "stackName": self.stackName
            }
        )
        logger.info("Dispatcher lambda defined")

        # Connect the Subscription Filters for the Audit Service
        csf.makeLogSubscriptionFilter(
            stackObj=self,
            executionMode=executionMode,
            auditAccount=cdkConfig["AUDIT_ACCOUNT"],
            lambdaObj=theLambda)


    def _createMarshalLambda(self, buildDir) -> None:
        theLambda = PythonFunction(self, "marshalLambda",
            description="Identifies still image files and sends to Minion to collate and zip",
            entry=self.buildDirs[buildDir],
            index="main.py",
            role=self._lambdaRole,
            runtime=Runtime.PYTHON_3_13,
            handler="lambdaHandler",
            bundling=BundlingOptions(
                asset_excludes =["__pycache__", "*.pyc", ".pytest_cache"],
            ),
            function_name=self.baseStackName + "_Marshal",
            memory_size=1024,
            # Using "unreserved account concurrency"
            timeout=Duration.minutes(15),
            retry_attempts=0,
            log_retention=RetentionDays.THREE_MONTHS,
            layers=[
                self._dependsLayer
            ],
            environment={
                "stackName": self.stackName,
                "HPatrolBaggingQueue": self._baggingQueue.queue_url
            }
        )
        logger.debug("Marshal lambda defined")

        # Connect the Subscription Filters for the Audit Service
        csf.makeLogSubscriptionFilter(
            stackObj=self,
            executionMode=executionMode,
            auditAccount=cdkConfig["AUDIT_ACCOUNT"],
            lambdaObj=theLambda)

        # Create the "fireing" rule
        # AWS cron expressions have the following format:
        #   cron(Minutes Hours Day-of-month Month Day-of-week Year)
        # Note that you cannot supply both "day" and "weekDay" at the same time; it will error out
        # Running every day at 0800EDT
        cronRule = Rule(
            self, "MarshalRule",
            schedule=Schedule.cron(
                minute="00",
                hour="12",
                # day="?",
                month="*",
                week_day="*",
                year="*"),
            enabled=False # Disabled by SOP; to be enabled after deployment
        )
        tgtInput = {"dummy": "event input sent to the lambda"}
        cronRule.add_target(LambdaFunction(theLambda, event=RuleTargetInput.from_object(tgtInput)))
        logger.debug("Cron rule defined")


    def _createMinionLambda(self, buildDir) -> None:
        #Create an SQS event source for Lambda
        sqsEventSource = SqsEventSource(self._baggingQueue, batch_size=1)

        # Notice that this lambda has greater memory capacity
        theLambda = PythonFunction(self, "minionLambda",
            description="Collate still images into zips and place in pickup location",
            entry=self.buildDirs[buildDir],
            index="main.py",
            role=self._lambdaRole,
            runtime=Runtime.PYTHON_3_13,
            handler="lambdaHandler",
            bundling=BundlingOptions(
                asset_excludes =["__pycache__", "*.pyc", ".pytest_cache"],
            ),
            function_name=self.baseStackName + "_Minion",
            memory_size=1024,
            # Using "unreserved account concurrency"
            ephemeral_storage_size=Size.mebibytes(4096),
            events=[sqsEventSource],
            timeout=Duration.minutes(15),
            retry_attempts=0,
            log_retention=RetentionDays.THREE_MONTHS,
            layers=[
                self._dependsLayer
            ],
            environment={
                "stackName": self.stackName
            }
        )

        # Connect the Subscription Filters for the Audit Service
        csf.makeLogSubscriptionFilter(
            stackObj=self,
            executionMode=executionMode,
            auditAccount=cdkConfig["AUDIT_ACCOUNT"],
            lambdaObj=theLambda)

        logger.debug("Minion lambda defined")


    def _createDroverLambda(self, buildDir) -> None:
        theLambda = PythonFunction(self, "droverLambda",
            description="Identifies aimpoints to send tasks to Tanscoder",
            entry=self.buildDirs[buildDir],
            index="main.py",
            role=self._lambdaRole,
            runtime=Runtime.PYTHON_3_13,
            handler="lambdaHandler",
            bundling=BundlingOptions(
                asset_excludes =["__pycache__", "*.pyc", ".pytest_cache"],
            ),
            function_name=self.baseStackName + "_Drover",
            memory_size=1024,
            # Using "unreserved account concurrency"
            timeout=Duration.minutes(15),
            retry_attempts=1,
            log_retention=RetentionDays.THREE_MONTHS,
            layers=[
                self._dependsLayer
            ],
            environment={
                "stackName": self.stackName,
                "HPatrolTranscodeQueue": self._transcodeQueue.queue_url
            }
        )
        logger.debug("Drover lambda defined")

        # Connect the Subscription Filters for the Audit Service
        csf.makeLogSubscriptionFilter(
            stackObj=self,
            executionMode=executionMode,
            auditAccount=cdkConfig["AUDIT_ACCOUNT"],
            lambdaObj=theLambda)

        # Create the "fireing" rules
        # AWS rate expressions have the following format:
        #   rate(duration: Duration)
        # Running every minute
        cronRule = Rule(
            self, "DroverRule-Transcode",
            schedule=Schedule.rate(Duration.minutes(1)),
            enabled=False # Disabled by SOP; to be enabled after deployment
        )
        tgtInput = {"task": "transcode"}
        cronRule.add_target(LambdaFunction(theLambda, event=RuleTargetInput.from_object(tgtInput)))

        # Running every minute
        cronRule = Rule(
            self, "DroverRule-Audios",
            schedule=Schedule.rate(Duration.minutes(1)),
            enabled=False # Disabled by SOP; to be enabled after deployment
        )
        tgtInput = {"task": "audio"}
        cronRule.add_target(LambdaFunction(theLambda, event=RuleTargetInput.from_object(tgtInput)))

        # Note however, that the rule for Timelapse operations is on cron format
        # AWS cron expressions have the following format:
        #   cron(Minutes Hours Day-of-month Month Day-of-week Year)
        # Note that you cannot supply both "day" and "weekDay" at the same time; it will error out
        # Running every minute
        cronRule = Rule(
            self, "DroverRule-Timelapse",
            schedule=Schedule.rate(Duration.minutes(1)),
            enabled=False # Disabled by SOP; to be enabled after deployment
        )
        tgtInput = {"task": "timelapse"}
        cronRule.add_target(LambdaFunction(theLambda, event=RuleTargetInput.from_object(tgtInput)))
        logger.debug("Cron rules defined")


    def _createTranscoderLambda(self, buildDir, ffmpegLayer, ffprobeLayer) -> None:
        #Create an SQS event source for Lambda
        sqsEventSource = SqsEventSource(self._transcodeQueue, batch_size=1)

        # Notice that this lambda has greater memory capacity
        theLambda = PythonFunction(self, "transcoderLambda",
            description="Collate video clips and transcode them",
            entry=self.buildDirs[buildDir],
            index="main.py",
            role=self._lambdaRole,
            runtime=Runtime.PYTHON_3_13,
            handler="lambdaHandler",
            bundling=BundlingOptions(
                asset_excludes =["__pycache__", "*.pyc", ".pytest_cache"],
            ),
            function_name=self.baseStackName + "_Transcoder",
            memory_size=2560,
            # Using "unreserved account concurrency"
            ephemeral_storage_size=Size.mebibytes(4096),
            events=[sqsEventSource],
            timeout=Duration.minutes(15),
            log_retention=RetentionDays.THREE_MONTHS,
            layers=[
                self._dependsLayer,
                ffmpegLayer,
                ffprobeLayer
            ],
            environment={
                "stackName": self.stackName
            }
        )

        # Connect the Subscription Filters for the Audit Service
        csf.makeLogSubscriptionFilter(
            stackObj=self,
            executionMode=executionMode,
            auditAccount=cdkConfig["AUDIT_ACCOUNT"],
            lambdaObj=theLambda)

        logger.debug("Transcoder lambda defined")


# TODO: Create Firehose stream 'deliveryData' for dashboard data


    class LambdaBuildDir:
        def __init__(self, name, srcDir, outputDir):
            self.name = name
            self.srcDir = srcDir
            self.outputDir = outputDir


    def _createDBoarderLambda(self, buildDir) -> None:
        myLogGroup = logs.LogGroup(
            self, "DBoarderLambdaLogGroup",
            retention=logs.RetentionDays.THREE_MONTHS,
            removal_policy=RemovalPolicy.RETAIN
        )

        theLambda = PythonFunction(self, "dboarderLambda",
            description="Processes aimpoints to produce dashboard data",
            entry=self.buildDirs[buildDir],
            index="main.py",
            role=self._lambdaRole,
            runtime=Runtime.PYTHON_3_13,
            handler="lambdaHandler",
            bundling=BundlingOptions(
                asset_excludes =["__pycache__", "*.pyc", ".pytest_cache"],
            ),
            function_name=self.baseStackName + "_DBoarder",
            memory_size=1024,
            timeout=Duration.minutes(15),
            retry_attempts=0,
            log_group=myLogGroup,
            layers=[
                self._dependsLayer
            ],
            environment={
                "stackName": self.stackName
            }
        )
        logger.info("DBoarder lambda defined")

        # Connect the Subscription Filters for the Audit Service
        csf.makeLogSubscriptionFilter(
            stackObj=self,
            executionMode=executionMode,
            auditAccount=cdkConfig["AUDIT_ACCOUNT"],
            lambdaObj=theLambda)

        # S3 event notification trigger: fires on any object created in the aimpoints folder
        bucket = s3.Bucket.from_bucket_name(self, "wrkBucket", config["defaultWrkBucket"])
        bucket.add_event_notification(
            s3.EventType.OBJECT_CREATED,
            s3n.LambdaDestination(theLambda),
            s3.NotificationKeyFilter(prefix=f"{targetFiles}/")
        )
