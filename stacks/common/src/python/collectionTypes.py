from enum import auto
from enum import IntEnum


# Remember to update the allStills variable in 
# hPatrolUtils until that FIXME over there is resolved


class CollectionType(IntEnum):
    M3U    = auto()
    RDTC   = auto()
    FIRST  = auto()
    IVIDEO = auto()
    STILLS = auto()
    UFANET = auto()
    RTSPME = auto()
    IPLIVE = auto()
    HNGCLD = auto()
    YOUTUB = auto()
    GNDONG = auto()
    BAZNET = auto()
    YTFILE = auto()
    FSTLLS = auto()
    ISTLLS = auto()
    MSTLLS = auto()
    MOIDOM = auto()
    STREAM = auto()
    OPTION = auto()
    IMAGEINJSON = auto()
