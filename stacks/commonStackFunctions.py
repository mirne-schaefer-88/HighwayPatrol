# Python libraries import statements
import os
import sys
import shutil
import logging
import pathlib
import subprocess
from random import randint
from dynaconf import Dynaconf
from botocore.exceptions import ClientError


# AWS import statements
import boto3
import botocore.exceptions as bexcept
from aws_cdk.aws_kinesis import Stream
from aws_cdk import Environment, Stack
from aws_cdk.aws_iam import PermissionsBoundary
from aws_cdk.aws_lambda import Code, Function, LayerVersion
from aws_cdk.aws_logs_destinations import KinesisDestination
from aws_cdk.aws_logs import FilterPattern, SubscriptionFilter
from aws_cdk.aws_iam import ManagedPolicy, Role, ServicePrincipal


# Small trick so importing the system settings works
# This keeps us from having to re-specify certain values in different places
# e.g.: without this, we'd have to specify the bucketName in two files
commonPath = str(pathlib.Path.cwd()) + "/stacks/common/src/python"
if commonPath not in sys.path:
    sys.path.insert(0, commonPath)
# print("\n\nsys.path: {}\n\n".format(sys.path))

# This application's import statements
import systemSettings     # variable not used; needed to load settings to memory
from superGlblVars import projectName
from systemMode import SystemMode


logger = logging.getLogger()
logging.basicConfig(format="%(asctime)s - %(levelname)-8s - %(module)s:%(lineno)d - %(message)s")


def initCdkSettings() -> dict:
    cwd = pathlib.Path.cwd()
    # secretsFile = cwd / f"projectResources/{secretName}"
    settingsFile = cwd / "stacks/systemResources/deploymentSettings.yaml"

    # Just checking...
    try:
        # youThere = secretsFile.resolve(strict=True)
        youThere = settingsFile.resolve(strict=True)
    except FileNotFoundError as err:
        # Can't use logger() here because it hasn't been initialized; need print()
        print("\nERROR!")
        print(f"\tFile '{err.filename}' not found")
        missing = os.path.basename(err.filename)
        print("\n")
        raise FileNotFoundError(missing) from None

    settingsFiles = [
        # secretsFile,
        settingsFile
    ]

    return Dynaconf(
        settings_files=settingsFiles,
        environments=True,
        filter_strategy=None,
        ignore_unknown_envvars=True,
    )


def createLambdaRoles(stackObjRef) -> None:
    managedPolicies = [
            ManagedPolicy.from_aws_managed_policy_name("AmazonS3FullAccess"),
            ManagedPolicy.from_aws_managed_policy_name("AmazonSQSFullAccess"),
            ManagedPolicy.from_aws_managed_policy_name("AmazonSSMFullAccess"),
            ManagedPolicy.from_aws_managed_policy_name("AWSLambda_FullAccess"),
            ManagedPolicy.from_aws_managed_policy_name("CloudWatchFullAccess"),
            ManagedPolicy.from_aws_managed_policy_name("AWSCloudFormationReadOnlyAccess"),
            ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaSQSQueueExecutionRole"),
            ManagedPolicy.from_aws_managed_policy_name("service-role/AWSLambdaVPCAccessExecutionRole")
        ]
    if "BP" in os.environ:
        managedPolicies.append(ManagedPolicy.from_managed_policy_name(
                                stackObjRef, 
                                "lambda_key_access_policy", 
                                managed_policy_name="KMS_Key_User"))

    # Create the Lambda Roles
    lambdaRole = Role(stackObjRef,
        f"{projectName}LambdaRole",
        assumed_by=ServicePrincipal("lambda.amazonaws.com"),
        permissions_boundary=getBoundaryPolicy(stackObjRef, "lambdaBoundary"),
        managed_policies=managedPolicies
    )
    logger.info("Lambda roles defined")
    return lambdaRole


# A requirements.txt file on the lambda's root directory will trigger a pip install
# during deployment. This way, we do only one pip install for all deployed lambdas within a stack.
# Borrowed from https://stackoverflow.com/questions/58855739/how-to-install-external-modules-in-a-python-lambda-function-created-by-aws-cdk
def createDependenciesLayer(theStack, requirementsFile, resetDir=True) -> LayerVersion:
    logger.info("Creating lambda layers")
    outputDir = f"{theStack.cwd}/.lambdaDependencies"

    if resetDir:
        # Install requirements for layer in the outputDir
        if not os.environ.get("SKIP_PIP"):
            # Clean out the existing directory if already there
            outputPath = pathlib.Path(outputDir)
            if outputPath.exists():
                shutil.rmtree(outputPath)

            # Note: pip will create the output dir if it does not exist
            subprocess.check_call(
                f"pip install -r {requirementsFile} -t {outputDir}/python".split()
            )

    return LayerVersion(
        theStack,
        theStack.baseStackName + "-dependencies",
        code=Code.from_asset(outputDir)
    )


# Some libraries need to be installed as binaries, specifically the crypto lib
def createDependenciesLayerBin(theStack, requirementsFile, resetDir=True) -> LayerVersion:
    logger.info("Creating lambda layers")
    outputDir = f"{theStack.cwd}/.lambdaDependenciesBin"

    if resetDir:
        # Install requirements for layer in the outputDir
        if not os.environ.get("SKIP_PIP"):
            # Clean out the existing directory if already there
            outputPath = pathlib.Path(outputDir)
            if outputPath.exists():
                shutil.rmtree(outputPath)

            # Note: pip will create the output dir if it does not exist
            # Crypto library requires libc binary to avoid this error:
            #   Unable to import module 'src.python.main': /lib64/libc.so.6: version `GLIBC_2.28' not found
            #                                            : (required by /opt/python/cryptography/hazmat/bindings/_rust.abi3.so)
            #   Info here: https://repost.aws/knowledge-center/lambda-python-package-compatible
            # The library is used to modify the CA file for VPN access
            subprocess.check_call(
                f"pip install -r {requirementsFile} -t {outputDir}/python --platform manylinux2014_x86_64 --only-binary=:all:".split()
            )

    return LayerVersion(
        theStack,
        theStack.baseStackName + "-dependencies",
        code=Code.from_asset(outputDir)
    )


def printAllAccounts(region) -> dict:
    botoSession = boto3.Session()
    profiles = botoSession.available_profiles

    logger.debug("****************ALL ACCOUNTS****************")
    for profile in profiles:
        botoSession = boto3.Session(profile_name=profile, region_name=region)
        stsClient = botoSession.client("sts")
        try:
            account = stsClient.get_caller_identity()["Account"]
            logger.debug(f"{profile},{account}")
        except bexcept.NoCredentialsError:
            logger.error(f"{profile},--- no credentials --")
        except bexcept.InvalidConfigError:
            logger.error(f"{profile},--- invalid config --")
        except Exception as exc:
            logger.error(f"{profile},--- exception --")
            logger.error(exc)
    logger.debug("****************END ALL ACCOUNTS****************")


def getAccountInfo(env: Environment, profile: str) -> dict:

    try:
        session = boto3.Session(profile_name=profile, region_name=env.region)
        sts_client = session.client("sts")
        accountId = sts_client.get_caller_identity()["Account"]
        region = sts_client.meta.region_name
    except Exception as exc:
        logger.error(exc)
        logger.error("Will now exit... (this may hang; go ahead and Ctrl+C)")
        raise KeyError

    result = {"accountId": accountId, "region": region}

    return result


def loggerSetup():
    # Set up some logging parameters and disable the really detailed logging by other packages
    logger.setLevel(logging.DEBUG)

    # If you want to see some behind-the-scenes action from AWS, comment out their lines (or change log levels)
    logging.getLogger("boto").setLevel(logging.WARNING)
    logging.getLogger("boto3").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("botocore").setLevel(logging.WARNING)
    logging.getLogger("s3transfer").setLevel(logging.WARNING)


# TODO: Create subscription filter for dashboard data
#       Transcoder and Minion using { $.eventType = "dBoardData" }


# Create Subscription Filters so the Audit Service gets a copy of the logs
def makeLogSubscriptionFilter(
        stackObj: Stack,
        auditAccount: str,
        executionMode: str,
        lambdaObj: Function) -> SubscriptionFilter:

    # Subscription filters are not needed in certain environments
    if "BP" not in os.environ:
        return None

    logger.info(f"Setting audit service filters for {executionMode} environment")

    # Get the various fields we'll need later
    lambdaLog = lambdaObj.log_group
    lambdaRegion = lambdaObj.env.region

    # Derive the destination ARN
    destinationArn = f"arn:aws:logs:{lambdaRegion}:{auditAccount}:destination:collection-audit-data-stream-dest"

    # Create a random int to use as part of ID's - not seen in AWS Console
    randIdStr = str(randint(1000, 9999))

    # Get a reference to the Kinesis Stream we'll be sending to and identify it as the destination
    auditStreamId = "audit_stream_" + randIdStr
    auditStream = Stream.from_stream_arn(stackObj,
                                         auditStreamId,
                                         stream_arn=destinationArn)
    auditDestination = KinesisDestination(auditStream)

    # Create the pattern string we're using as a filter
    filterPattern = FilterPattern.literal('{ $.eventType = \"audit\" }')

    # Create the subscription filter
    subFilterId = "subscription_filter_" + randIdStr
    subFilter = SubscriptionFilter(stackObj,
                                   subFilterId,
                                   log_group=lambdaLog,
                                   destination=auditDestination,
                                   filter_pattern=filterPattern)
    return subFilter


def getLatestLayerVersion(selfObj, lambdaClient, layerName: str) -> LayerVersion:
    try:
        res = lambdaClient.list_layer_versions(LayerName=layerName, MaxItems=1)
        if res["LayerVersions"]:
            latestLayerArn = res["LayerVersions"][0]["LayerVersionArn"]
        else:
            logger.error(f"Lambda layer '{layerName}' not found")
            raise Exception(f"Lambda layer not found")
    except ClientError as e:
        logger.error(f"Unexpected error retrieving lambda layer: {e}")
        raise Exception(f"Unexpected error retrieving lambda layer")

    return LayerVersion.from_layer_version_arn(selfObj, selfObj.stackName + layerName, layer_version_arn=latestLayerArn)


# Download the executable for yt-dlp; usable through subprocess calls
def createYtdlDependencyLayer(stack, resetDir=True) -> LayerVersion:
    logger.info("Creating yt-dlp layer")
    outputDir = f"{stack.cwd}/.lambdaYtdlpDependency"
    outputPath = pathlib.Path(outputDir)

    if resetDir:
        # Clean out the existing directory if already there
        if outputPath.exists():
            shutil.rmtree(outputPath)
        outputPath = outputPath / "bin"
        ytDlpPath = f"{outputDir}/bin/yt-dlp"
        outputPath.mkdir(parents=True, exist_ok=True)

        # Download yt-dlp executable/library
        subprocess.check_call([
            "curl",
            "-L",
            "https://github.com/yt-dlp/yt-dlp/releases/latest/download/yt-dlp",
            "-o",
            ytDlpPath
        ])

        # Make executable
        os.chmod(ytDlpPath, 0o755)

    return LayerVersion(
        stack,
        stack.stackName + "-ytdl",
        code=Code.from_asset(outputDir)
    )


def addPycurlLayer(stack, resetDir=True) -> LayerVersion:
    # Add pycurl as a layer as it can't be binary-only pip installed due to dependency on libcurl
    logger.debug("Connecting Pycurl layer")
    requirementsFile = "stacks/systemResources/pycurl.txt"
    outputDir = f"{stack.cwd}/.pycurlDependency"

    if resetDir:
        if not os.environ.get("SKIP_PIP"):
            # Clean out the existing directory if already there
            outputPath = pathlib.Path(outputDir)
            if outputPath.exists():
                shutil.rmtree(outputPath)

            subprocess.check_call(
                f"pip install -r {requirementsFile} -t {outputDir}/python".split()
        )

    return LayerVersion(
        stack,
        stack.stackName + "-pycurl",
        code=Code.from_asset(outputDir)
    )


def getBoundaryPolicy(stack, id="BoundaryPolicy"):
    # Only need this boundary policy when deploying on  accounts
    # Make sure the environment variable exists and has the correct value
    if bp := os.environ.get("BP"):
        boundaryPolicy = ManagedPolicy.from_managed_policy_name(
            stack,
            id=id,
            managed_policy_name=bp
        )
        return boundaryPolicy
    else:
        return None


def applyBoundaryPolicy(stack):
    boundaryPolicy = getBoundaryPolicy(stack)
    if boundaryPolicy:
        PermissionsBoundary.of(stack).apply(boundaryPolicy)


def ignoreSymlinks(src, names):
    """
    Used as the ignore callable for shutil.copytree()
    It returns a list of names that are symbolic links
    """
    ignoredNames = []
    for name in names:
        # Construct the full path to check if it is a symlink
        fullPath = os.path.join(src, name)
        if os.path.islink(fullPath):
            ignoredNames.append(name)
    return ignoredNames


# Collate the codes that will be uploaded as the lambdas
def createLambdasStructure(stack, compsDir: str, components: list, executionMode: str) -> None:
    logger.info("Constructing lambda build dirs")

    outputDir = f"{stack.cwd}/.lambdaBuild"
    commonDir = f"{stack.cwd}/stacks/common"

    # Clean out the output directory if already there
    outputPath = pathlib.Path(outputDir)
    if outputPath.exists():
        logger.info(f"Clearing output dir: {outputDir}")
        shutil.rmtree(outputPath)

    for aPiece in components:
        shutil.copytree(f"{compsDir}/{aPiece}/src/python",
                        f"{outputDir}/{aPiece}",
                        dirs_exist_ok=True,
                        ignore=ignoreSymlinks)
        shutil.copytree(f"{commonDir}/src/python",
                        f"{outputDir}/{aPiece}",
                        dirs_exist_ok=True,
                        ignore=ignoreSymlinks)

        # Insert the lambdas build directory to the buildDirs dictionary
        stack.buildDirs[aPiece] = f"{outputDir}/{aPiece}"

        # Add the common/testResources for non-PROD deployments
        if executionMode != SystemMode.PROD:
            testResDir = "testResources"
            try:
                shutil.copytree(f"{compsDir}/{aPiece}/{testResDir}", f"{outputDir}/{aPiece}/{testResDir}", dirs_exist_ok=True)
                shutil.copytree(f"{commonDir}/{testResDir}", f"{outputDir}/{aPiece}/{testResDir}", dirs_exist_ok=True)
            except FileNotFoundError:
                pass

    logger.info("Lambda build dirs created")
