# Python libraries import statements
import os
import pathlib
import logging


# AWS import statements
import boto3
from constructs import Construct
from aws_cdk.aws_sqs import Queue
from aws_cdk.aws_lambda import Runtime
from aws_cdk import Duration, Stack, Size
from aws_cdk.aws_logs import RetentionDays
from aws_cdk.aws_sqs import QueueEncryption
from aws_cdk.aws_events_targets import LambdaFunction
from aws_cdk.aws_lambda_python_alpha import PythonFunction
from aws_cdk.aws_lambda_python_alpha import BundlingOptions
from aws_cdk.aws_lambda_event_sources import SqsEventSource
from aws_cdk.aws_events import Rule, Schedule, RuleTargetInput


# This application's import statements
from . import commonStackFunctions as csf
from superGlblVars import config
from superGlblVars import projectName


logger = logging.getLogger()
logging.basicConfig(format="%(asctime)s - %(levelname)-8s - %(module)s:%(lineno)d - %(message)s")

# Get both sets of config settings (for the system and deployment)
executionMode = config["mode"]
cdkConfig = csf.initCdkSettings()
# Stupid DynaConf relies on environment variables
os.environ["ENV_FOR_DYNACONF"] = executionMode.name


class HPatrolMonitoringStack(Stack):
    cwd = str(pathlib.Path.cwd())

    bucketName = config["defaultWrkBucket"]

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

        try:
            self.botoSession = boto3.Session(profile_name=profile, region_name=regionName)
            # Test if credentials are valid
            if "BP" in os.environ:
                self.botoSession.client("kms").list_aliases()
        except Exception as exc:
            logger.error(exc)
            logger.error("Will now exit... (this may hang; go ahead and Ctrl+C)")
            return None

        # Apply the BoundaryPolicy to the entire stack
        csf.applyBoundaryPolicy(self)

        self._lambdaRole = csf.createLambdaRoles(self)
        self._createStatusQueue()
        self._createLambdas()


    def _createStatusQueue(self) -> None:
        self._statusQueue = Queue(
            self, "HPatrolStatusQueue",
            queue_name=self.baseStackName + "_" + projectName + "Status",
            encryption=QueueEncryption.KMS_MANAGED,
            visibility_timeout=Duration.hours(2), 
            retention_period=Duration.minutes(30)
        )
        logger.info("Status queue defined")


    def _createLambdas(self)-> None:
        # Import/create the Lambda Layer for dependencies
        reqsFile = f"{self.cwd}/stacks/systemResources/lambdaRequirements.txt"
        self._dependsLayer = csf.createDependenciesLayer(self, reqsFile)
        logger.info("Lambda layers created")

        # Construct the Lambdas
        components = [
              "monitor"
            , "enabler"
            , "disabler"
            , "historian"
        ]
        self.buildDirs = {} # Pointer to where system components are built prior to uploading
        compsDir = f"{self.cwd}/stacks"
        csf.createLambdasStructure(self, compsDir, components, executionMode)

        self._createMonitorLambda("monitor")
        self._createEnablerLambda("enabler")
        self._createDisablerLambda("disabler")
        self._createHistorianLambda("historian")


    def _createMonitorLambda(self, buildDir) -> None:
        theLambda = PythonFunction(self, "monitorLambda",
            description="Periodically sends aimpoints in the monitored directory to the Dispatcher to start work",
            entry=self.buildDirs[buildDir],
            index="main.py",
            role=self._lambdaRole,
            runtime=Runtime.PYTHON_3_13,
            handler="lambdaHandler",
            bundling=BundlingOptions(
                asset_excludes =["__pycache__", "*.pyc", ".pytest_cache"],
            ),
            function_name=self.baseStackName + "_Monitor",
            memory_size=512,
            timeout=Duration.minutes(15),
            retry_attempts=1,
            log_retention=RetentionDays.THREE_MONTHS,
            layers=[
                self._dependsLayer
            ],
            environment={
                "stackName": self.stackName,
                "HPatrolDispatchQueue": self.baseStackName + "_" + projectName + "Dispatch", 
            }
        )
        logger.info("Monitor lambda defined")

        # Connect the Subscription Filters for the Audit Service
        csf.makeLogSubscriptionFilter(
            stackObj=self,
            executionMode=executionMode,
            auditAccount=cdkConfig["AUDIT_ACCOUNT"],
            lambdaObj=theLambda)

        # Create the "fireing" rule
        # Runs every 1hour
        # Note that even though Monitor will run this often,
        # the GLOBALS.monitorFrequency sets the default of how often to check
        rateRule = Rule(
            self, "MonitorRule",
            schedule=Schedule.rate(Duration.hours(1)),
            enabled=False # Disabled by SOP; to be enabled after deployment
        )
        tgtInput = {"dummy": "event input sent to the lambda"}
        rateRule.add_target(LambdaFunction(theLambda, event=RuleTargetInput.from_object(tgtInput)))
        logger.info("Cron rule defined")


    def _createHistorianLambda(self, buildDir) -> None:
        #Create an SQS event source for Lambda
        sqsEventSource = SqsEventSource(self._statusQueue,
                                        max_batching_window=Duration.seconds(300),
                                        batch_size=10000)

        theLambda = PythonFunction(self, "historianLambda",
            description="Logs results from collectors in the 'aimpointStatus' directory.",
            entry=self.buildDirs[buildDir],
            index="main.py",
            role=self._lambdaRole,
            runtime=Runtime.PYTHON_3_13,
            handler="lambdaHandler",
            bundling=BundlingOptions(
                asset_excludes =["__pycache__", "*.pyc", ".pytest_cache"],
            ),
            function_name=self.baseStackName + "_Historian",
            memory_size=512,
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
        logger.info("Historian lambda defined")

        # Connect the Subscription Filters for the Audit Service
        csf.makeLogSubscriptionFilter(
            stackObj=self,
            executionMode=executionMode,
            auditAccount=cdkConfig["AUDIT_ACCOUNT"],
            lambdaObj=theLambda)


    def _createDisablerLambda(self, buildDir) -> None:
        theLambda = PythonFunction(self, "disablerLambda",
            description="Periodically checks the status of aimpoints on collection.\
                         If consistent failure for 30 minutes is detected, move aimpoint to the '/monitored' directory",
            entry=self.buildDirs[buildDir],
            index="main.py",
            role=self._lambdaRole,
            runtime=Runtime.PYTHON_3_13,
            handler="lambdaHandler",
            bundling=BundlingOptions(
                asset_excludes =["__pycache__", "*.pyc", ".pytest_cache"],
            ),
            function_name=self.baseStackName + "_Disabler",
            memory_size=1024,
            timeout=Duration.minutes(15),
            retry_attempts=1,
            log_retention=RetentionDays.THREE_MONTHS,
            layers=[
                self._dependsLayer
            ],
            environment={
                "stackName": self.stackName
            }
        )
        logger.debug("Disabler lambda defined")

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
        # Running every 30mins
        cronRule = Rule(
            self, "DisablerRule",
            schedule=Schedule.cron(
                minute="*/30",
                hour="*",
                # day="?",
                month="*",
                week_day="*",
                year="*"),
            enabled=False # Disabled by SOP; to be enabled after deployment
        )
        tgtInput = {"dummy": "event input sent to the lambda"}
        cronRule.add_target(LambdaFunction(theLambda, event=RuleTargetInput.from_object(tgtInput)))
        logger.debug("Cron rule defined")


    def _createEnablerLambda(self, buildDir) -> None:
        theLambda = PythonFunction(self, "enablerLambda",
            description="Periodically checks status of aimpoints in 'monitored' status.\
                         Any successful collection re-enables the aimpoint.",
            entry=self.buildDirs[buildDir],
            index="main.py",
            role=self._lambdaRole,
            runtime=Runtime.PYTHON_3_13,
            handler="lambdaHandler",
            bundling=BundlingOptions(
                asset_excludes =["__pycache__", "*.pyc", ".pytest_cache"],
            ),
            function_name=self.baseStackName + "_Enabler",
            memory_size=1024,
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

        logger.debug("Enabler lambda defined")


        # Create the "fireing" rule
        # AWS cron expressions have the following format:
        #   cron(Minutes Hours Day-of-month Month Day-of-week Year)
        # Note that you cannot supply both "day" and "weekDay" at the same time; it will error out
        # Running every 5mins
        cronRule = Rule(
            self, "EnablerRule",
            schedule=Schedule.cron(
                minute="*/5",
                hour="*",
                # day="?",
                month="*",
                week_day="*",
                year="*"),
            enabled=False # Disabled by SOP; to be enabled after deployment
        )
        tgtInput = {"dummy": "event input sent to the lambda"}
        cronRule.add_target(LambdaFunction(theLambda, event=RuleTargetInput.from_object(tgtInput)))
        logger.debug("Cron rule defined")
