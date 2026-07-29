from . import config


def build_agent_card() -> dict:
    return {
        "name": "Invoice Action Agent",
        "description": "Reads invoice claim batches, proposes exactly one typed business "
                        "action per package with cited evidence, and executes only after "
                        "receiving an accepted result continuation.",
        "version": "1.0.0",
        "capabilities": {},
        "skills": [
            {
                "id": "invoice_action_agent",
                "name": "invoice_action_agent",
                "description": "Classifies invoice packages into settle_invoice, "
                                "request_approval, hold_invoice, reject_duplicate, or "
                                "open_exception, citing decisive evidence for each.",
                "tags": ["invoice", "finance", "accounts-payable", "a2a"],
            }
        ],
        "supportedInterfaces": [
            {
                "url": config.BASE_URL,
                "protocolBinding": "HTTP+JSON",
                "protocolVersion": "1.0",
            }
        ],
        "defaultInputModes": [config.MEDIA_BATCH],
        "defaultOutputModes": [config.MEDIA_PROPOSALS, config.MEDIA_RECEIPTS],
    }
