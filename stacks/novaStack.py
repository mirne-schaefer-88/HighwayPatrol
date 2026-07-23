# Python libraries import statements
import os
import pathlib
import logging


# AWS import statements
import boto3
from constructs import Construct
from aws_cdk import aws_ec2 as ec2
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


class HPatrolNovaStack(Stack):
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

        # Derive the VPC to be used
        self.stackVpc = ec2.Vpc.from_lookup(self,
            id=f"{baseStackName}-VPC",
            vpc_name=cdkConfig["VPC_NAME"],
            is_default=False
        )

        # Subnet for the VPC
        self.ecsVpcSubnet = ec2.SubnetSelection(
            subnet_filters=[                    
                ec2.SubnetFilter().by_ids([cdkConfig["ECS_SUBNET"]])
            ]
        )

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
        generators = ["trafficland", "ufanet", "hngscloud"]
        csf.createLambdasStructure(self, gensDir, generators, executionMode)

        self._createTrafficlandLambda("trafficland")
        self._createHngscloudLambda("hngscloud")
        self._createUfanetLambda("ufanet")


    def _createTrafficlandLambda(self, buildDir) -> str:
        theLambda = PythonFunction(self, "trafficlandLambda",
            description="Collects and parses Trafficland site to create aimpoints",
            entry=self.buildDirs[buildDir],
            index="main.py",
            role=self._lambdaRole,
            runtime=Runtime.PYTHON_3_13,
            handler="lambdaHandler",
            bundling=BundlingOptions(
                asset_excludes =["__pycache__", "*.pyc", ".pytest_cache"],
            ),
            function_name=self.baseStackName + "_Trafficland",
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
        logger.info("Trafficland lambda defined")

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
        # Running 15mins
        cronRule = Rule(
            self, "TrafficlandRule",
            schedule=Schedule.cron(
                minute="00/15",
                hour="*",
                # day="?",
                month="*",
                week_day="*",
                year="*"),
            enabled=False   # 07.03.23 Rule disabled on all taffy aimpoints; awaiting orders
        )
        tgtInput = {"dummy": "event input sent to the lambda"}
        cronRule.add_target(LambdaFunction(theLambda, event=RuleTargetInput.from_object(tgtInput)))
        logger.info("Cron rule defined")


    def _createUfanetLambda(self, buildDir) -> str:
        theLambda = PythonFunction(self, "ufanetLambda",
            description="Collects and parses Ufanet site to create aimpoints",
            entry=self.buildDirs[buildDir],
            index="main.py",
            role=self._lambdaRole,
            runtime=Runtime.PYTHON_3_13,
            handler="lambdaHandler",
            bundling=BundlingOptions(
                asset_excludes =["__pycache__", "*.pyc", ".pytest_cache"],
            ),
            function_name=self.baseStackName + "_Ufanet",
            memory_size=1024,
            # Using "unreserved account concurrency"
            timeout=Duration.minutes(15),
            retry_attempts=1,
            log_retention=RetentionDays.THREE_MONTHS,
            vpc=self.stackVpc,
            vpc_subnets=self.ecsVpcSubnet,
            layers=[
                self._dependsLayer
            ],
            environment={
                "stackName": self.stackName
            }
        )
        logger.info("Ufanet lambda defined")

        # Connect the Subscription Filters for the Audit Service
        csf.makeLogSubscriptionFilter(
            stackObj=self,
            executionMode=executionMode,
            auditAccount=cdkConfig["AUDIT_ACCOUNT"],
            lambdaObj=theLambda)
        
        # Create the "fireing" rule for Ufanet
        # AWS cron expressions have the following format:
        #   cron(Minutes Hours Day-of-month Month Day-of-week Year)
        # Note that you cannot supply both "day" and "weekDay" at the same time; it will error out
        # Running every Tuesday at 20:19
        cronRule = Rule(
            self, "UfanetRule",
            schedule=Schedule.cron(
                minute="19",
                hour="20",
                # day="*",
                month="*",
                week_day="TUE",
                year="*"),
            enabled=False # Disabled by SOP; to be enabled after deployment
        )
        tgtInput = {"dummy": "event input sent to the lambda"}
        cronRule.add_target(LambdaFunction(theLambda, event=RuleTargetInput.from_object(tgtInput)))
        logger.info("Cron rule defined")


    def _createHngscloudLambda(self, buildDir) -> str:
        theLambda = PythonFunction(self, "hngscloudLambda",
            description="Collects and parses Hngscloud site to create aimpoints",
            entry=self.buildDirs[buildDir],
            index="main.py",
            role=self._lambdaRole,
            runtime=Runtime.PYTHON_3_13,
            handler="lambdaHandler",
            bundling=BundlingOptions(
                asset_excludes =["__pycache__", "*.pyc", ".pytest_cache"],
            ),
            function_name=self.baseStackName + "_Hngscloud",
            memory_size=1024,
            # Using "unreserved account concurrency"
            timeout=Duration.minutes(15),
            retry_attempts=0,
            log_retention=RetentionDays.THREE_MONTHS,
            vpc=self.stackVpc,
            vpc_subnets=self.ecsVpcSubnet,
            layers=[
                self._dependsLayer
            ],
            environment={
                "stackName": self.stackName
            }
        )
        logger.info("Hngscloud lambda defined")

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
        # Running every day at 0705UTC
        cronRule = Rule(
            self, "HngscloudRule",
            schedule=Schedule.cron(
                minute="05",
                hour="07", # 7 AM UTC, 3 AM EST
                # day="?",
                month="*",
                week_day="*",
                year="*"),
            enabled=False   # Disabled by SOP; to be enabled after deployment
        )
        tgtInput = {"dummy": "event input sent to the lambda"}
        cronRule.add_target(LambdaFunction(theLambda, event=RuleTargetInput.from_object(tgtInput)))
        logger.info("Cron rule defined")
