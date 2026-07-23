# Python libraries import statements
import os
import pathlib
import logging


# AWS import statements
import boto3
from aws_cdk import Stack
from aws_cdk import Duration
from aws_cdk import aws_s3 as s3
from constructs import Construct
from aws_cdk import RemovalPolicy
from aws_cdk import aws_lambda as _lambda


# This application's import statements
from . import commonStackFunctions as csf
from superGlblVars import config


logger = logging.getLogger()
logging.basicConfig(format="%(asctime)s - %(levelname)-8s - %(module)s:%(lineno)d - %(message)s")

# Get both sets of config settings (for the system and deployment)
executionMode = config["mode"]
cdkConfig = csf.initCdkSettings()


class HPatrolInitStack(Stack):
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

        # # Just for debugging; enable when necessary
        # csf.printAllAccounts(regionName)

        try:
            self.botoSession = boto3.Session(profile_name=profile, region_name=regionName)
            if "BP" in os.environ:
                # Test if credentials are valid
                self.botoSession.client("kms").list_aliases()
        except Exception as exc:
            logger.error(exc)
            logger.error("Will now exit... (this may hang; go ahead and Ctrl+C)")
            return None

        # Apply the BoundaryPolicy to the entire stack
        csf.applyBoundaryPolicy(self)

        self._createBucket()
        self._createLayers()


    def _createLayers(self) -> None:
        self._createFfmpegLayer()
        self._createFfprobeLayer()
        self._createFfmpegStreamingLayer()


    def _createBucket(self) -> None:
        bucket = s3.Bucket(
            self,
            f"{self.baseStackName}-Bucket",
            access_control=s3.BucketAccessControl.BUCKET_OWNER_FULL_CONTROL,
            auto_delete_objects=False,  # would allow CDK to delete objects on stack deletion
            removal_policy=RemovalPolicy.RETAIN,
            # auto_delete_objects=True,             # for testing
            # removal_policy=RemovalPolicy.DESTROY, # for testing
            block_public_access=s3.BlockPublicAccess.BLOCK_ALL,
            bucket_name=self.bucketName,
            encryption=s3.BucketEncryption.S3_MANAGED,
            object_ownership=s3.ObjectOwnership.BUCKET_OWNER_PREFERRED,
            public_read_access=False,
            # Versioning enabled only to allow replication; no need otherwise
            versioned=True,
            lifecycle_rules=[
                # Abort incomplete multipart uploads after 2 days
                s3.LifecycleRule(
                    id="AbortIncompleteUploads",
                    enabled=False, # Disabled by SOP; to be enabled after deployment
                    abort_incomplete_multipart_upload_after=Duration.days(2)
                ),
                # Delete expired object delete markers
                s3.LifecycleRule(
                    id="DeleteExpiredObjectDeleteMarkers",
                    enabled=False, # Disabled by SOP; to be enabled after deployment
                    expired_object_delete_marker=True,
                    noncurrent_version_expiration=Duration.days(1)
                ),
                s3.LifecycleRule(
                    id="Expire hashes after 1 day",
                    enabled=False, # Disabled by SOP; to be enabled after deployment
                    prefix="hashfiles/",
                    expiration=Duration.days(1), 
                    noncurrent_version_expiration=Duration.days(1)
                ),
                s3.LifecycleRule(
                    id="Expire lz/ after 6months",
                    enabled=False, # Disabled by SOP; to be enabled after deployment
                    prefix="lz/",
                    expiration=Duration.days(180),
                    noncurrent_version_expiration=Duration.days(1)
                ),
                s3.LifecycleRule(
                    id="Expire audios/ after 6months",
                    enabled=False, # Disabled by SOP; to be enabled after deployment
                    prefix="audios/",
                    expiration=Duration.days(180),
                    noncurrent_version_expiration=Duration.days(1)
                ),
                s3.LifecycleRule(
                    id="Expire up/ after 7days",
                    enabled=False, # Disabled by SOP; to be enabled after deployment
                    prefix="up/",
                    expiration=Duration.days(7),
                    noncurrent_version_expiration=Duration.days(1)
                ),
                s3.LifecycleRule(
                    id="Expire aimpointStatus/ after 3days",
                    enabled=False, # Disabled by SOP; to be enabled after deployment
                    prefix="aimpointStatus/",
                    expiration=Duration.days(3),
                    noncurrent_version_expiration=Duration.days(1)
                ),
                s3.LifecycleRule(
                    id="Expire dboard/deliveries/ after 6months",
                    enabled=False, # Disabled by SOP; to be enabled after deployment
                    prefix="dboard/deliveries/",
                    expiration=Duration.days(180),
                    noncurrent_version_expiration=Duration.days(1)
                ),
                s3.LifecycleRule(
                    id="Expire stillsLz/ after 6months",
                    enabled=False, # Disabled by SOP; to be enabled after deployment
                    prefix="stillsLz/",
                    expiration=Duration.days(180),
                    noncurrent_version_expiration=Duration.days(1)
                )
            ]
        )


    def _createFfmpegLayer(self) -> None:
        theLayer = _lambda.LayerVersion(
            self, "ffmpegLayer",
            code=_lambda.Code.from_asset(f"{self.cwd}/stacks/systemResources/ffmpeg-isolated.zip"),
            compatible_runtimes=[_lambda.Runtime.PYTHON_3_13],
            compatible_architectures=[_lambda.Architecture.X86_64],
            description="isolated ffmpeg",
            license="GPL-2.0-or-later",
            layer_version_name=f"{cdkConfig["ffmpegIsolated"]}"
        )


    def _createFfmpegStreamingLayer(self) -> None:
        # A separate ffmpeg later for streaming is used because the ffmpeg
        # used in the Transcoder is unable to reach out to the 'net. That one
        # was built from johnvansickle.com and has glibc statically linked so
        # DNS resolution doesn't work. This one can reach the 'net but it's too
        # big for the Transcoder currently. Some info here:
        # https://stackoverflow.com/questions/60528501/ffmpeg-segmentation-fault-with-network-stream-source
        theLayer = _lambda.LayerVersion(
            self, "ffmpegStreamingLayer",
            code=_lambda.Code.from_asset(f"{self.cwd}/stacks/systemResources/ffmpeg-for-streaming-isolated.zip"),
            compatible_runtimes=[_lambda.Runtime.PYTHON_3_13],
            compatible_architectures=[_lambda.Architecture.X86_64],
            description="isolated ffmpeg for video streaming",
            license="GPL-2.0-or-later",
            layer_version_name=f"{cdkConfig["ffmpegStreamCol"]}"
        )  


    def _createFfprobeLayer(self) -> None:
        theLayer = _lambda.LayerVersion(
            self, "ffprobeLayer",
            code=_lambda.Code.from_asset(f"{self.cwd}/stacks/systemResources/ffprobe-isolated.zip"),
            compatible_runtimes=[_lambda.Runtime.PYTHON_3_13],
            compatible_architectures=[_lambda.Architecture.X86_64],
            description="isolated ffprobe",
            license="GPL-2.0-or-later",
            layer_version_name=f"{cdkConfig["ffprobeIsolated"]}"
        )
