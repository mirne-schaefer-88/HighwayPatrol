# Python libraries import statements
import os
import pathlib
import logging


# AWS import statements
import boto3
from constructs import Construct
from aws_cdk import aws_ec2 as ec2
from aws_cdk.aws_lambda import Runtime
from aws_cdk import Duration, Stack, Size
from aws_cdk.aws_logs import RetentionDays
from aws_cdk.aws_lambda_python_alpha import PythonFunction
from aws_cdk.aws_lambda_python_alpha import BundlingOptions


# This application's import statements
from . import commonStackFunctions as csf
from superGlblVars import config
from superGlblVars import projectName


logger = logging.getLogger()
logging.basicConfig(
    format="%(asctime)s - %(levelname)-8s - %(module)s:%(lineno)d - %(message)s"
)

# Get both sets of config settings (for the system and deployment)
executionMode = config["mode"]
cdkConfig = csf.initCdkSettings()
# Stupid DynaConf relies on environment variables
os.environ["ENV_FOR_DYNACONF"] = executionMode.name


class HPatrolVPCLambdas(Stack):
    cwd = str(pathlib.Path.cwd())

    def __init__(
        self,
        profile: str,
        stackName: str,
        description: str,
        baseStackName: str,
        scope: Construct,
        **kwargs
    ) -> None:
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
            self.botoSession = boto3.Session(
                profile_name=profile,
                region_name=regionName
            )
            # Test if credentials are valid
            self.botoSession.client("kms").list_aliases()
        except Exception as exc:
            logger.error(exc)
            logger.error("Will now exit... (this may hang; go ahead and Ctrl+C)")
            return None

        # Need lambda client to find out latest layer versions
        self.lambdaClient = self.botoSession.client("lambda")

        # Apply the BoundaryPolicy to the entire stack
        csf.applyBoundaryPolicy(self)

        self.stackVPC = ec2.Vpc.from_lookup(self, id=f"{baseStackName}-VPC",
                                            vpc_name=cdkConfig["VPC_NAME"],
                                            is_default=False)
        self.vpcSubnets = ec2.SubnetSelection(
            subnet_filters=[
                ec2.SubnetFilter().by_ids(cdkConfig["FULL_NAT_SUBNETS"])
            ]
        )
        self.vpcSecurityGroups = ec2.SecurityGroup.from_security_group_id(
            self,
            id=f"{baseStackName}-VPCSecurityGroup",
            security_group_id=cdkConfig["WHIRL_SG"]
        )

        self._lambdaRole = csf.createLambdaRoles(self)
        self._createLambdas()
        # This CDK recipe assumes that any S3 buckets necessary are already created
        # Note that the /hashfiles prefix in the bucket should have a policy to expire objects


    def _createLambdas(self) -> None:
        # Import existing Lambda layers
        ffmpegLayer = csf.getLatestLayerVersion(self, self.lambdaClient, cdkConfig["ffmpegIsolated"])
        ffprobeLayer = csf.getLatestLayerVersion(self, self.lambdaClient, cdkConfig["ffprobeIsolated"])
        ffmpegStream = csf.getLatestLayerVersion(self, self.lambdaClient, cdkConfig["ffmpegStreamCol"])

        # Create the Lambda Layer for dependencies
        ytdlpLayer = csf.createYtdlDependencyLayer(self)
        reqsFile = f"{self.cwd}/stacks/systemResources/lambdaRequirementsBin.txt"
        self._dependsLayerBin = csf.createDependenciesLayerBin(self, reqsFile)
        pycurlLayer = csf.addPycurlLayer(self)
        logger.info("Lambda layers created")

        # Construct the Lambdas
        buildDir = "collector"
        components = [
              buildDir
        ]
        self.buildDirs = {} # Pointer to where system components are built prior to uploading
        # Note we don't use the resetDir flag here because
        # this stack is deployed to only one region
        compsDir = f"{self.cwd}/stacks"
        csf.createLambdasStructure(self, compsDir, components, executionMode)


        self._createStreamingVideosLambda(buildDir, ffmpegStream, pycurlLayer)
        # Notice Stills and Videos lambdas are similar; diff being ffprobe
        self._createStillsLambda(buildDir, ytdlpLayer, pycurlLayer)
        self._createVideosLambda(buildDir, ytdlpLayer, pycurlLayer, ffprobeLayer)
        self._createYoutubeLambda(buildDir, ytdlpLayer, pycurlLayer, ffprobeLayer, ffmpegLayer)


    def _createStillsLambda(self, buildDir, ytdlpLayer, pycurlLayer) -> None:
        theLambda = PythonFunction(
            self,
            "stillsLambda",
            description="Collects stills data through a proxy",
            entry=self.buildDirs[buildDir],
            index="main.py",
            role=self._lambdaRole,
            runtime=Runtime.PYTHON_3_13,
            handler="lambdaHandler",
            bundling=BundlingOptions(
                asset_excludes =["__pycache__", "*.pyc", ".pytest_cache"],
            ),
            function_name=self.baseStackName + "_StillsVPC",
            memory_size=256,
            ephemeral_storage_size=Size.mebibytes(512),
            # Using "unreserved account concurrency"
            timeout=Duration.minutes(15),
            retry_attempts=0,
            log_retention=RetentionDays.THREE_MONTHS,
            # Stills don't use yt_dlp but needs it for "boot up" checks
            layers=[
                self._dependsLayerBin,
                pycurlLayer,
                ytdlpLayer
            ],
            environment={
                "stackName": self.stackName,
                "HPatrolStatusQueue": f"{self.baseStackName}_{projectName}Status",
            },
            vpc=self.stackVPC,
            vpc_subnets=self.vpcSubnets,
            security_groups=[self.vpcSecurityGroups]
        )
        logger.info("Stills proxy lambda defined")

        # Connect the Subscription Filters for the Audit Service
        csf.makeLogSubscriptionFilter(
            stackObj=self,
            executionMode=executionMode,
            auditAccount=cdkConfig["AUDIT_ACCOUNT"],
            lambdaObj=theLambda
        )


    def _createVideosLambda(self, buildDir, ytdlpLayer, pycurlLayer, ffprobeLayer) -> None:
        theLambda = PythonFunction(
            self,
            "videosLambda",
            description="Collects video data through a proxy",
            entry=self.buildDirs[buildDir],
            index="main.py",
            role=self._lambdaRole,
            runtime=Runtime.PYTHON_3_13,
            handler="lambdaHandler",
            bundling=BundlingOptions(
                asset_excludes =["__pycache__", "*.pyc", ".pytest_cache"],
            ),
            function_name=self.baseStackName + "_VideosVPC",
            memory_size=2048,
            ephemeral_storage_size=Size.mebibytes(10240),
            # Using "unreserved account concurrency"
            timeout=Duration.minutes(15),
            retry_attempts=0,
            log_retention=RetentionDays.THREE_MONTHS,
            layers=[
                self._dependsLayerBin,
                ffprobeLayer,
                pycurlLayer,
                ytdlpLayer
            ],
            environment={
                "stackName": self.stackName,
                "HPatrolStatusQueue": f"{self.baseStackName}_{projectName}Status",
            },
            vpc=self.stackVPC,
            vpc_subnets=self.vpcSubnets,
            security_groups=[self.vpcSecurityGroups]
        )
        logger.info("Videos proxy lambda defined")

        # Connect the Subscription Filters for the Audit Service
        csf.makeLogSubscriptionFilter(
            stackObj=self,
            executionMode=executionMode,
            auditAccount=cdkConfig["AUDIT_ACCOUNT"],
            lambdaObj=theLambda
        )


    def _createStreamingVideosLambda(self, buildDir, ffmpegStream, pycurlLayer) -> None:
        theLambda = PythonFunction(
            self,
            "videosStreamingLambda",
            description="Collect data from video streams through a proxy",
            entry=self.buildDirs[buildDir],
            index="streamCollectorMain.py",
            role=self._lambdaRole,
            runtime=Runtime.PYTHON_3_13,
            handler="lambdaHandler",
            bundling=BundlingOptions(
                asset_excludes =["__pycache__", "*.pyc", ".pytest_cache"],
            ),
            function_name=self.baseStackName + "_StreamVideosVPC",
            memory_size=2048,
            ephemeral_storage_size=Size.mebibytes(10240),
            # Using "unreserved account concurrency"
            timeout=Duration.minutes(15),
            retry_attempts=0,
            log_retention=RetentionDays.THREE_MONTHS,
            layers=[
                self._dependsLayerBin,
                pycurlLayer,
                ffmpegStream
            ],
            environment={
                "stackName": self.stackName,
                "HPatrolStatusQueue": f"{self.baseStackName}_{projectName}Status",
            },
            vpc=self.stackVPC,
            vpc_subnets=self.vpcSubnets,
            security_groups=[self.vpcSecurityGroups]
        )
        logger.info("Streaming Videos proxy lambda defined")

        # Connect the Subscription Filters for the Audit Service
        csf.makeLogSubscriptionFilter(
            stackObj=self,
            executionMode=executionMode,
            auditAccount=cdkConfig["AUDIT_ACCOUNT"],
            lambdaObj=theLambda
        )


    def _createYoutubeLambda(self, buildDir, ytdlpLayer, pycurlLayer, ffprobeLayer, ffmpegLayer) -> None:
        theLambda = PythonFunction(
            self,
            "youtubeLambda",
            description="Collects video data through a proxy",
            entry=self.buildDirs[buildDir],
            index="main.py",
            role=self._lambdaRole,
            runtime=Runtime.PYTHON_3_13,
            handler="lambdaHandler",
            bundling=BundlingOptions(
                asset_excludes =["__pycache__", "*.pyc", ".pytest_cache"],
            ),
            function_name=self.baseStackName + "_YoutubeVPC",
            memory_size=2048,
            ephemeral_storage_size=Size.mebibytes(10240),
            # Using "unreserved account concurrency"
            timeout=Duration.minutes(15),
            retry_attempts=0,
            log_retention=RetentionDays.THREE_MONTHS,
            layers=[
                self._dependsLayerBin,
                ffprobeLayer,
                ffmpegLayer,
                pycurlLayer,
                ytdlpLayer
            ],
            environment={
                "stackName": self.stackName,
                "HPatrolStatusQueue": f"{self.baseStackName}_{projectName}Status"
            },
            vpc=self.stackVPC,
            vpc_subnets=self.vpcSubnets,
            security_groups=[self.vpcSecurityGroups]
        )
        logger.info("YoutubeVPC lambda defined")

        # Connect the Subscription Filters for the Audit Service
        csf.makeLogSubscriptionFilter(
            stackObj=self,
            executionMode=executionMode,
            auditAccount=cdkConfig["AUDIT_ACCOUNT"],
            lambdaObj=theLambda
        )
