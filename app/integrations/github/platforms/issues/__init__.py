from .check import handle as check_handle
from .collect import handle as collect_handle
from .classify import handle as classify_handle
from .evaluate import handle as evaluate_handle
from .act import handle as act_handle

HANDLERS = {
    "check": check_handle,
    "collect": collect_handle,
    "classify": classify_handle,
    "evaluate": evaluate_handle,
    "act": act_handle,
}
