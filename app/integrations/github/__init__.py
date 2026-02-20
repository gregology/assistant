from app.integrations.github.update_prs import handle as update_prs_handle
from app.integrations.github.classify_pr import handle as classify_pr_handle

HANDLERS = {
    "update_prs": update_prs_handle,
    "classify_pr": classify_pr_handle,
}
