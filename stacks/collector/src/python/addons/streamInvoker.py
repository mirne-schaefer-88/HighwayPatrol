# External libraries import statements
import json
import boto3
import logging
from random import sample


# This application's import statements
import superGlblVars as GLOBALS
from orangeUtils import utils as ut
from utils import hPatrolUtils as hput


logger = logging.getLogger()


def _prepareInvoke(ap: dict) -> dict:
    """Prep to start the invoke the lambda"""
    acctId = GLOBALS.myArn.split(":")[4]
    aRegion = sample(ap["collRegions"], 1)[0]
    aRegion = ut.getRegionCode(aRegion)
    streamCollectorArn = (
        "arn:aws:lambda:"
        + aRegion
        + ":"
        + acctId
        + ":function:"
        + f"{GLOBALS.baseStackName}_StreamVideos"
    )
    return {"invokeArn": streamCollectorArn, "invokeRegion": aRegion}


def _invokeLambda(ap: dict, lambdaConfig: dict) -> bool:
    """Invoke a streaming lambda with the ap as payload"""
    logger.info(f"Creating boto3 lambda client in {lambdaConfig["invokeRegion"]}")
    awsLambda = boto3.client(service_name="lambda", region_name=lambdaConfig["invokeRegion"])

    logger.info(
        f"Invoking Streaming Lambda '{lambdaConfig["invokeArn"]}' "
        f"for '{hput.formatNameBase(ap["filenameBase"], ap["deviceID"])}'"
    )
    try:
        response = awsLambda.invoke(
            FunctionName=lambdaConfig["invokeArn"],
            InvocationType="Event",
            Payload=json.dumps(ap)
        )
    except Exception as e:
        logger.critical(
            f"Caught Exception attempting to invoke streaming Lambda :::{e}"
        )
        return False

    if 200 <= response["ResponseMetadata"]["HTTPStatusCode"] < 300:
        pass
    else:
        logger.warning(f"Failed invoking the streaming lambda: {response}")
        return False
    return True


def invoke(ap):
    """Streaming-Lambda invocation interface"""
    invokeConfig = _prepareInvoke(ap)
    _invokeLambda(
        ap=ap,
        lambdaConfig=invokeConfig
    )
