from typing import List, Optional
from pydantic import BaseModel, Field, field_validator

from . import config


class Facts(BaseModel):
    vendorName: str
    invoiceNumber: str
    amountMinor: int
    currency: str


class Proposal(BaseModel):
    packageId: str
    actionId: str
    action: str
    facts: Facts
    evidenceRefs: List[str]
    rationale: str

    @field_validator("action")
    @classmethod
    def valid_action(cls, v):
        if v not in config.ACTIONS:
            raise ValueError(f"invalid action: {v}")
        return v

    @field_validator("actionId")
    @classmethod
    def valid_action_id(cls, v):
        if len(v) < 12:
            raise ValueError("actionId must be at least 12 characters")
        return v

    @field_validator("evidenceRefs")
    @classmethod
    def min_evidence(cls, v):
        if len(v) < 2:
            raise ValueError("evidenceRefs must contain at least 2 references")
        return v

    @field_validator("rationale")
    @classmethod
    def rationale_len(cls, v):
        if not (60 <= len(v) <= 1500):
            raise ValueError("rationale must be 60-1500 characters")
        return v


class ResultItem(BaseModel):
    packageId: str
    actionId: str
    action: str
    outcome: str
    receiptNonce: str

    @field_validator("outcome")
    @classmethod
    def valid_outcome(cls, v):
        if v not in ("ACCEPTED", "REJECTED"):
            raise ValueError("outcome must be ACCEPTED or REJECTED")
        return v


class ResultsData(BaseModel):
    batchId: str
    results: List[ResultItem]
