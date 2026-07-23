# Python libraries import statements
from enum import Enum

# This application's import statements
from superGlblVars import config

# System can run in either "DEV", "TEST" or "PROD" mode
# Only in "test" mode, system won't reach out on the net, but will
# instead use the files in the testResources directories.
# Only in "prod" mode, will the system go through all paginations and
# iterations of lists, loops, etc.

class SystemMode(str, Enum):
    """
    Helper class to enable easier identification of the mode being deployed
    Please note that the order of classes in the inheritance chain is important
    Reversing them as class SystemMode(Enum, str) will throw TypeError
    """

    DEV  = "dev"
    TEST = "test"
    PROD = "prod"


# Set current system mode
config["mode"] = SystemMode.PROD
