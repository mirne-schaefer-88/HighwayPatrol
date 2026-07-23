# Python libraries import statements
import os
from aws_cdk import App, Environment


# This application's import statements
from stacks.initStack import HPatrolInitStack
from stacks.novaStack import HPatrolNovaStack
from stacks.vpcStack import HPatrolVPCLambdas
from stacks.seoulStack import HPatrolSeoulStack
from stacks.zurichStack import HPatrolZurichStack
from stacks.vortexStack import HPatrolVortexStack
from stacks.stargateStack import HPatrolStargateStack
from stacks.frankfurtStack import HPatrolFrankfurtStack
from stacks.singaporeStack import HPatrolSingaporeStack
from stacks.stockholmStack import HPatrolStockholmStack
from stacks.collectionStack import HPatrolCollectionStack
from stacks.processingStack import HPatrolProcessingStack
from stacks.monitoringStack import HPatrolMonitoringStack
from stacks.common.src.python.orangeUtils.utils import getRegionCode


app = App()
profile = app.node.try_get_context("profile")
description = app.node.try_get_context("description")
baseStackName = app.node.try_get_context("baseStackName")
selectedStack = app.node.try_get_context("selectedStack")
novaRegion = getRegionCode(app.node.try_get_context("novaRegion"))
seoulRegion = getRegionCode(app.node.try_get_context("seoulRegion"))
zurichRegion = getRegionCode(app.node.try_get_context("zurichRegion"))
frankfurtRegion = getRegionCode(app.node.try_get_context("frankfurtRegion"))
singaporeRegion = getRegionCode(app.node.try_get_context("singaporeRegion"))
stockholmRegion = getRegionCode(app.node.try_get_context("stockholmRegion"))
collectionRegion = getRegionCode(app.node.try_get_context("collectionRegion"))
processingRegion = getRegionCode(app.node.try_get_context("processingRegion"))


try:
    # This is the account#
    deploymentAccount = os.environ.get("CDK_DEPLOY_ACCOUNT", os.environ["CDK_DEFAULT_ACCOUNT"])
    # print(f"\n\n---deploymentAccount: {deploymentAccount}")  
except KeyError as err:
    print(f"\nERROR: Environment variable {err} not found")
    print(f"ERROR: Verify correct account was used; specified account was '{profile}'")
    print(f"ERROR: Alternatively, make sure to have valid AWS access tokens (inconceivable?)")
    exit(1)


allStacks = baseStackName + "-all"
initStackName = baseStackName + "-init"
novaStackName = baseStackName + "-nova"
seoulStackName = baseStackName + "-seoul"
zurichStackName = baseStackName + "-zurich"
vortexStackName = baseStackName + "-vortex"
vpcStackName = baseStackName + "-vpcLambdas"
stargateStackName = baseStackName + "-stargate"
singaporeStackName = baseStackName + "-singapore"
stockholmStackName = baseStackName + "-stockholm"
frankfurtStackName = baseStackName + "-frankfurt"
collectionStackName = baseStackName + "-collection"
processingStackName = baseStackName + "-processing"
monitoringStackName = baseStackName + "-monitoring"


# Init stack defaulted to NoVa, and NOT part of "all stacks"
if selectedStack == initStackName:
    print(f"\n\n---HPatrolInitStack ({novaRegion})---")
    HPatrolInitStack(
        scope=app,
        profile=profile,
        baseStackName=baseStackName,
        description=description,
        stackName=initStackName,
        env=Environment(
            account=deploymentAccount,
            region=novaRegion
        )
    )


if selectedStack == novaStackName or selectedStack == allStacks:
    print(f"\n\n---HPatrolNovaStack ({novaRegion})---")
    HPatrolNovaStack(
        scope=app,
        profile=profile,
        baseStackName=baseStackName,
        description=description,
        stackName=novaStackName,
        env=Environment(
            account=deploymentAccount,
            region=novaRegion
        )
    )


if selectedStack == frankfurtStackName or selectedStack == allStacks:
    print(f"\n\n---HPatrolFrankfurtStack ({frankfurtRegion})---")
    HPatrolFrankfurtStack(
        scope=app,
        profile=profile,
        baseStackName=baseStackName,
        description=description,
        stackName=frankfurtStackName,
        env=Environment(
            account=deploymentAccount,
            region=frankfurtRegion
        )
    )


if selectedStack == seoulStackName or selectedStack == allStacks:
    print(f"\n\n---HPatrolSeoulStack ({seoulRegion})---")
    HPatrolSeoulStack(
        scope=app,
        profile=profile,
        baseStackName=baseStackName,
        description=description,
        stackName=seoulStackName,
        env=Environment(
            account=deploymentAccount,
            region=seoulRegion
        )
    )


if selectedStack == singaporeStackName or selectedStack == allStacks:
    print(f"\n\n---HPatrolSingaporeStack ({singaporeRegion})---")
    HPatrolSingaporeStack(
        scope=app,
        profile=profile,
        baseStackName=baseStackName,
        description=description,
        stackName=singaporeStackName,
        env=Environment(
            account=deploymentAccount,
            region=singaporeRegion
        )
    )


if selectedStack == stockholmStackName or selectedStack == allStacks:
    print(f"\n\n---HPatrolStockholmStack ({stockholmRegion})---")
    HPatrolStockholmStack(
        scope=app,
        profile=profile,
        baseStackName=baseStackName,
        description=description,
        stackName=stockholmStackName,
        env=Environment(
            account=deploymentAccount,
            region=stockholmRegion
        )
    )


if selectedStack == zurichStackName or selectedStack == allStacks:
    print(f"\n\n---HPatrolZurichStack ({zurichRegion})---")
    HPatrolZurichStack(
        scope=app,
        profile=profile,
        baseStackName=baseStackName,
        description=description,
        stackName=zurichStackName,
        env=Environment(
            account=deploymentAccount,
            region=zurichRegion
        )
    )


if selectedStack == collectionStackName or selectedStack == allStacks:
    stackRegion = app.node.try_get_context("collectionRegion")
    resetDir = app.node.try_get_context("resetDir")
    doReset = False if resetDir == "No" else True

    print(f"\n\n---HPatrolCollectionStack ({stackRegion})---")
    HPatrolCollectionStack(
        scope=app,
        profile=profile,
        resetDir=doReset,
        baseStackName=baseStackName,
        description=description,
        stackName=f"{collectionStackName}-{stackRegion}",
        env=Environment(
            account=deploymentAccount,
            region=stackRegion
        )
    )


if selectedStack == processingStackName or selectedStack == allStacks:
    print(f"\n\n---HPatrolProcessingStack ({processingRegion})---")
    HPatrolProcessingStack(
        scope=app,
        profile=profile,
        baseStackName=baseStackName,
        description=description,
        stackName=processingStackName,
        env=Environment(
            account=deploymentAccount,
            region=processingRegion
        )
    )


if selectedStack == monitoringStackName or selectedStack == allStacks:
    print(f"\n\n---HPatrolMonitoringStack ({processingRegion})---")
    HPatrolMonitoringStack(
        scope=app,
        profile=profile,
        description=description,
        baseStackName=baseStackName,
        stackName=monitoringStackName,
        env=Environment(
            account=deploymentAccount,
            region=processingRegion
        )
    )


if selectedStack == vpcStackName or selectedStack == allStacks:
    print(f"\n\n---HPatrolProxyStack ({processingRegion})---")
    HPatrolVPCLambdas(
        scope=app,
        profile=profile,
        description=description,
        baseStackName=baseStackName,
        stackName=vpcStackName,
        env=Environment(
            account=deploymentAccount,
            region=processingRegion
        )
    )


if selectedStack == vortexStackName or selectedStack == allStacks:
    print(f"\n\n---HPatrolVortexStack ({processingRegion})---")
    HPatrolVortexStack(
        scope=app,
        profile=profile,
        description=description,
        baseStackName=baseStackName,
        stackName=vortexStackName,
        env=Environment(
            account=deploymentAccount,
            region=processingRegion
        )
    )

if selectedStack == stargateStackName or selectedStack == allStacks:
    print(f"\n\n---HPatrolStargateStack ({processingRegion})---")
    HPatrolStargateStack(
        scope=app,
        profile=profile,
        description=description,
        baseStackName=baseStackName,
        stackName=stargateStackName,
        env=Environment(
            account=deploymentAccount,
            region=processingRegion
        )
    )

app.synth()
