from app.integrations.email.check import handle as check_handle
from app.integrations.email.collect import handle as collect_handle
from app.integrations.email.classify import handle as classify_handle
from app.integrations.email.act import handle as act_handle

HANDLERS = {
    "check": check_handle,
    "collect": collect_handle,
    "classify": classify_handle,
    "act": act_handle,
}
