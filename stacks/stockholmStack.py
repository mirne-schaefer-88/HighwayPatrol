# Python libraries import statements
import os
import pathlib
import logging


# AWS import statements
import boto3
from constructs import Construct
from aws_cdk import Duration, Stack
from aws_cdk.aws_lambda import Runtime
from aws_cdk.aws_logs import RetentionDays
from aws_cdk.aws_events_targets import LambdaFunction
from aws_cdk.aws_lambda_python_alpha import PythonFunction
from aws_cdk.aws_lambda_python_alpha import BundlingOptions
from aws_cdk.aws_events import Rule, Schedule, RuleTargetInput


# This application's import statements
from . import commonStackFunctions as csf
from superGlblVars import config


logger = logging.getLogger()
logging.basicConfig(format="%(asctime)s - %(levelname)-8s - %(module)s:%(lineno)d - %(message)s")

# Get both sets of config settings (for the system and deployment)
executionMode = config["mode"]
cdkConfig = csf.initCdkSettings()
# Stupid DynaConf relies on environment variables
os.environ["ENV_FOR_DYNACONF"] = executionMode.name


class HPatrolStockholmStack(Stack):
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
            self.botoSession = boto3.Session(profile_name=profile, region_name=regionName)
            # Test if credentials are valid
            self.botoSession.client("kms").list_aliases()
        except Exception as exc:
            logger.error(exc)
            logger.error("Will now exit... (this may hang; go ahead and Ctrl+C)")
            return None

        # Apply the BoundaryPolicy to the entire stack
        csf.applyBoundaryPolicy(self)

        self._lambdaRole = csf.createLambdaRoles(self)

        # Import/create the Lambda Layer for dependencies
        reqsFile = f"{self.cwd}/stacks/systemResources/lambdaRequirements.txt"
        self._dependsLayer = csf.createDependenciesLayer(self, reqsFile)
        logger.info("Lambda layers created")

        # Construct the Lambdas
        self.buildDirs = {} # Pointer to where system components are built prior to uploading
        gensDir = f"{self.cwd}/stacks/generators"
        generators = ["is74", "cud59", "moidom", "vegvesenNo", "digitrafficFi"]
        csf.createLambdasStructure(self, gensDir, generators, executionMode)

        self._createIs74Lambda("is74")
        self._createCud59Lambda("cud59")
        self._createMoidomLambda("moidom")
        self._createVegvesenNoLambda("vegvesenNo")
        self._createDigitrafficFiLambda("digitrafficFi")


    def _createMoidomLambda(self, buildDir) -> str:
        theLambda = PythonFunction(self, "moidomLambda",
            description="Collects and parses Moidom-Stream site to create aimpoints",
            entry=self.buildDirs[buildDir],
            index="main.py",
            role=self._lambdaRole,
            runtime=Runtime.PYTHON_3_13,
            handler="lambdaHandler",
            bundling=BundlingOptions(
                asset_excludes =["__pycache__", "*.pyc", ".pytest_cache"],
            ),
            function_name=self.baseStackName + "_Moidom",
            memory_size=1024,
            # Using "unreserved account concurrency"
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
        logger.info("Moidom lambda defined")

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
        # Running every day at 0905UTC
        cronRule = Rule(
            self, "MoidomRule",
            schedule=Schedule.cron(
                minute="05",
                hour="09", # 9 AM UTC, 5 AM EST
                # day="?",
                month="*",
                week_day="*",
                year="*"),
            enabled=False # Disabled by SOP; to be enabled after deployment
        )
        tgtInput = {"dummy": "event input sent to the lambda"}
        cronRule.add_target(LambdaFunction(theLambda, event=RuleTargetInput.from_object(tgtInput)))
        logger.info("Cron rule defined")


    def _createIs74Lambda(self, buildDir) -> str:
        theLambda = PythonFunction(self, "is74Lambda",
            description="Collects and parses Is74 site to create aimpoints",
            entry=self.buildDirs[buildDir],
            index="main.py",
            role=self._lambdaRole,
            runtime=Runtime.PYTHON_3_13,
            handler="lambdaHandler",
            bundling=BundlingOptions(
                asset_excludes =["__pycache__", "*.pyc", ".pytest_cache"],
            ),
            function_name=self.baseStackName + "_Is74",
            memory_size=1024,
            # Using "unreserved account concurrency"
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
        logger.info("Is74 lambda defined")

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
        # Running every day at 0602UTC # (0202EST)
        cronRule = Rule(
            self, "Is74Rule",
            schedule=Schedule.cron(
                minute="02",
                hour="06",
                # day="?",
                month="*",
                week_day="*",
                year="*"),
            enabled=False # Disabled by SOP; to be enabled after deployment
        )
        tgtInput = {"dummy": "event input sent to the lambda"}
        cronRule.add_target(LambdaFunction(theLambda, event=RuleTargetInput.from_object(tgtInput)))
        logger.info("Cron rule defined")


    def _createVegvesenNoLambda(self, buildDir) -> str:
        theLambda = PythonFunction(self, "vegvesenNoLambda",
            description="Collects and parses Vegvesen.no site to create aimpoints",
            entry=self.buildDirs[buildDir],
            index="main.py",
            role=self._lambdaRole,
            runtime=Runtime.PYTHON_3_13,
            handler="lambdaHandler",
            bundling=BundlingOptions(
                asset_excludes =["__pycache__", "*.pyc", ".pytest_cache"],
            ),
            function_name=self.baseStackName + "_VegvesenNo",
            memory_size=1024,
            # Using "unreserved account concurrency"
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
        logger.info("VegvesenNo lambda defined")

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
        # Running weekly at 1200UTC
        cronRule = Rule(
            self, "VegvesenNoRule",
            schedule=Schedule.cron(
                minute="00",
                hour="12",
                # day="?",
                month="*",
                week_day="2",
                year="*"),
            enabled=False   # 03.05.24 Ray: Disabled on requestor"s orders
        )
        tgtInput = {"dummy": "event input sent to the lambda"}
        cronRule.add_target(LambdaFunction(theLambda, event=RuleTargetInput.from_object(tgtInput)))
        logger.info("Cron rule defined")


    def _createDigitrafficFiLambda(self, buildDir) -> str:
        theLambda = PythonFunction(self, "digitrafficFiLambda",
            description="Collects and parses DigitrafficFi site to create aimpoints",
            entry=self.buildDirs[buildDir],
            index="main.py",
            role=self._lambdaRole,
            runtime=Runtime.PYTHON_3_13,
            handler="lambdaHandler",
            bundling=BundlingOptions(
                asset_excludes =["__pycache__", "*.pyc", ".pytest_cache"],
            ),
            function_name=self.baseStackName + "_DigitrafficFi",
            memory_size=2048,
            # Using "unreserved account concurrency"
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
        logger.info("DigitrafficFi lambda defined")

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
        # Running every day at 0802UTC (0402EST)
        cronRule = Rule(
            self, "DigitrafficFiRule",
            schedule=Schedule.cron(
                minute="02",
                hour="08",
                # day="?",
                month="*",
                week_day="*",
                year="*"),
            enabled=False # Disabled by SOP; to be enabled after deployment
        )
        tgtInput = {"dummy": "event input sent to the lambda"}
        cronRule.add_target(LambdaFunction(theLambda, event=RuleTargetInput.from_object(tgtInput)))
        logger.info("Cron rule defined")


    def _createCud59Lambda(self, buildDir) -> str:
        theLambda = PythonFunction(self, "cud59Lambda",
            description="Collects and parses Cud59 site to create aimpoints",
            entry=self.buildDirs[buildDir],
            index="main.py",
            role=self._lambdaRole,
            runtime=Runtime.PYTHON_3_13,
            handler="lambdaHandler",
            bundling=BundlingOptions(
                asset_excludes =["__pycache__", "*.pyc", ".pytest_cache"],
            ),
            function_name=self.baseStackName + "_Cud59",
            memory_size=2048,
            # Using "unreserved account concurrency"
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
        logger.info("Cud59 lambda defined")

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
        # Running every Monday at 1215UTC
        cronRule = Rule(
            self, "Cud59Rule",
            schedule=Schedule.cron(
                minute="15",
                hour="12",
                # day="*",
                month="*",
                week_day="MON",
                year="*"),
            enabled=False # Disabled by SOP; to be enabled after deployment
        )
        tgtInput = {"dummy": "event input sent to the lambda"}
        cronRule.add_target(LambdaFunction(theLambda, event=RuleTargetInput.from_object(tgtInput)))
        logger.info("Cron rule defined")
