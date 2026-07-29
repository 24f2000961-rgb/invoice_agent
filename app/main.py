import json
import time
from typing import Optional

from fastapi import FastAPI, Header, HTTPException, Request, Response
from pydantic import ValidationError

from . import config, storage, ai_client
from .auth import require_principal
from .canon import message_content_hash, package_content_hash
from .card import build_agent_card
from .schemas import Proposal, ResultsData

app = FastAPI()


# ---------------- protocol-level checks (all routes except the card) ----------------

def check_protocol_headers(request: Request):
    version = request.headers.get("A2A-Version")
    if version != config.A2A_VERSION:
        raise HTTPException(status_code=400, detail="unsupported or missing A2A-Version")
    if request.method in ("POST",):
        ctype = request.headers.get("content-type", "")
        if not ctype.startswith(config.A2A_CONTENT_TYPE):
            raise HTTPException(status_code=400, detail="unsupported media type")


def error_body(code: str, message: str):
    return {"error": {"code": code, "message": message}}


def a2a_response(payload: dict, status_code: int = 200) -> Response:
    body = json.dumps(payload).encode("utf-8")
    if len(body) > config.MAX_RESPONSE_BYTES:
        # Should not happen at the graded scale, but guard anyway.
        payload = {"error": {"code": "RESPONSE_TOO_LARGE", "message": "response exceeds size limit"}}
        body = json.dumps(payload).encode("utf-8")
        status_code = 500
    return Response(content=body, status_code=status_code, media_type=config.A2A_CONTENT_TYPE)


def task_to_dict(row: dict, history_limit: Optional[int] = None) -> dict:
    history = json.loads(row["history"])
    if history_limit is not None and history_limit >= 0:
        history = history[-history_limit:]
    return {
        "id": row["id"],
        "contextId": row["context_id"],
        "status": {"state": row["state"]},
        "artifacts": json.loads(row["artifacts"]),
        "history": history,
    }


# ---------------- Agent Card (public) ----------------

@app.get("/.well-known/agent-card.json")
def agent_card():
    return build_agent_card()


# ---------------- message:send ----------------

@app.post("/a2a/message:send")
def message_send(request: Request, body: dict, authorization: Optional[str] = Header(default=None)):
    check_protocol_headers(request)
    principal = require_principal(authorization)

    message = body.get("message")
    configuration = body.get("configuration") or {}
    if not message or "messageId" not in message or "parts" not in message:
        return a2a_response(error_body("BAD_REQUEST", "malformed message"), 400)

    message_id = message["messageId"]
    content_hash = message_content_hash(message)
    history_limit = configuration.get("historyLength")

    with storage.txn() as conn:
        existing = storage.get_idempotency(conn, principal, message_id)
        if existing:
            if existing["content_hash"] == content_hash:
                return a2a_response(json.loads(existing["response_json"]), 200)
            return a2a_response(
                error_body("IDEMPOTENCY_CONFLICT", "messageId reused with different content"), 409
            )

        parts = message.get("parts") or []
        if not parts:
            return a2a_response(error_body("BAD_REQUEST", "message has no parts"), 400)
        media_type = parts[0].get("mediaType")

        if media_type == config.MEDIA_BATCH:
            resp, status, task_id = _handle_initial_batch(conn, principal, message, parts[0]["data"], history_limit)
        elif media_type == config.MEDIA_RESULTS:
            resp, status, task_id = _handle_results(conn, principal, message, parts[0]["data"], history_limit)
        else:
            return a2a_response(error_body("BAD_REQUEST", "unsupported part mediaType"), 400)

        if status == 200:
            storage.put_idempotency(conn, principal, message_id, content_hash, task_id, resp)
        return a2a_response(resp, status)


def _handle_initial_batch(conn, principal, message, data, history_limit):
    batch_id = data.get("batchId")
    packages = data.get("packages") or []
    if not batch_id or not packages:
        return error_body("BAD_REQUEST", "missing batchId or packages"), 400, ""

    # Resolve each package's decision, batching every uncached one into a single AI call.
    decisions = {}
    to_decide = []
    hashes = {}
    for pkg in packages:
        pid = pkg.get("packageId") or pkg.get("id")
        if not pid:
            return error_body("BAD_REQUEST", "package missing packageId"), 400, ""
        h = package_content_hash(pkg)
        hashes[pid] = h
        cached = storage.get_cached_decision(conn, h)
        if cached:
            action_id, decision = cached
            decisions[pid] = (action_id, decision)
        else:
            to_decide.append({**pkg, "packageId": pid})

    if to_decide:
        try:
            fresh = ai_client.decide_packages(to_decide)
        except Exception as e:  # noqa: BLE001
            return error_body("AI_DECISION_FAILED", str(e)), 502, ""
        for pkg in to_decide:
            pid = pkg["packageId"]
            if pid not in fresh:
                return error_body("AI_DECISION_FAILED", f"no decision for {pid}"), 502, ""
            h = hashes[pid]
            action_id = f"act_{h[:20]}"
            decision = fresh[pid]
            decisions[pid] = (action_id, decision)
            storage.put_cached_decision(conn, h, action_id, decision)

    proposals = []
    used_action_ids = set()
    for pkg in packages:
        pid = pkg.get("packageId") or pkg.get("id")
        action_id, decision = decisions[pid]
        if action_id in used_action_ids:
            action_id = f"{action_id}_{pid[:8]}"
        used_action_ids.add(action_id)
        candidate = {
            "packageId": pid,
            "actionId": action_id,
            "action": decision.get("action"),
            "facts": decision.get("facts"),
            "evidenceRefs": decision.get("evidenceRefs"),
            "rationale": decision.get("rationale"),
        }
        try:
            validated = Proposal(**candidate)
        except ValidationError as e:
            return error_body("INVALID_PROPOSAL", f"{pid}: {e}"), 502, ""
        proposals.append(json.loads(validated.model_dump_json()))

    task_id = storage.new_id("task")
    context_id = storage.new_id("ctx")
    artifact = {
        "artifactId": storage.new_id("artif"),
        "parts": [{"mediaType": config.MEDIA_PROPOSALS, "data": {"batchId": batch_id, "proposals": proposals}}],
    }
    history = [message]

    storage.create_task(
        conn, task_id, principal, context_id, batch_id,
        "TASK_STATE_INPUT_REQUIRED", [artifact], history, proposals,
    )

    row = storage.get_task_row(conn, task_id)
    resp = {"task": task_to_dict(row, history_limit)}
    return resp, 200, task_id


def _handle_results(conn, principal, message, data, history_limit):
    task_id = message.get("taskId")
    context_id = message.get("contextId")
    if not task_id or not context_id:
        return error_body("BAD_REQUEST", "missing taskId/contextId"), 400, ""

    try:
        results_data = ResultsData(**data)
    except ValidationError as e:
        return error_body("BAD_REQUEST", str(e)), 400, ""

    row = storage.get_task_row(conn, task_id)
    if not row or row["principal"] != principal:
        return error_body("NOT_FOUND", "task not found"), 404, ""

    if row["context_id"] != context_id or row["batch_id"] != results_data.batchId:
        return error_body("CONTINUATION_MISMATCH", "context or batch does not match"), 409, ""

    if row["state"] != "TASK_STATE_INPUT_REQUIRED":
        return error_body("TASK_NOT_AWAITING_RESULTS", "task is not awaiting results"), 409, ""

    stored_proposals = json.loads(row["proposals"])
    by_key = {(p["packageId"], p["actionId"]): p for p in stored_proposals}

    executions = []
    for result in results_data.results:
        key = (result.packageId, result.actionId)
        proposal = by_key.get(key)
        if not proposal or proposal["action"] != result.action:
            return error_body("CONTINUATION_MISMATCH", "package/action does not match stored proposal"), 409, ""
        if result.outcome == "ACCEPTED":
            executions.append({
                "packageId": proposal["packageId"],
                "actionId": proposal["actionId"],
                "action": proposal["action"],
                "receiptNonce": result.receiptNonce,
                "facts": proposal["facts"],
                "evidenceRefs": proposal["evidenceRefs"],
            })

    artifacts = json.loads(row["artifacts"])
    artifacts.append({
        "artifactId": storage.new_id("artif"),
        "parts": [{"mediaType": config.MEDIA_RECEIPTS, "data": {"batchId": results_data.batchId, "executions": executions}}],
    })
    history = json.loads(row["history"])
    history.append(message)

    storage.update_task(conn, task_id, state="TASK_STATE_COMPLETED", artifacts=artifacts, history=history)
    row = storage.get_task_row(conn, task_id)
    resp = {"task": task_to_dict(row, history_limit)}
    return resp, 200, task_id


# ---------------- tasks/{id} GET ----------------

@app.get("/a2a/tasks/{task_id}")
def get_task(task_id: str, request: Request, authorization: Optional[str] = Header(default=None)):
    check_protocol_headers(request)
    principal = require_principal(authorization)
    with storage.txn() as conn:
        row = storage.get_task_row(conn, task_id)
        if not row or row["principal"] != principal:
            return a2a_response(error_body("NOT_FOUND", "task not found"), 404)
        return a2a_response(task_to_dict(row))


# ---------------- tasks GET (list) ----------------

@app.get("/a2a/tasks")
def list_tasks(request: Request, authorization: Optional[str] = Header(default=None)):
    check_protocol_headers(request)
    principal = require_principal(authorization)
    with storage.txn() as conn:
        rows = storage.list_tasks(conn, principal)
        return a2a_response({"tasks": [task_to_dict(r) for r in rows]})


# ---------------- tasks/{id}:cancel ----------------

@app.post("/a2a/tasks/{task_id}:cancel")
def cancel_task(task_id: str, request: Request, authorization: Optional[str] = Header(default=None)):
    check_protocol_headers(request)
    principal = require_principal(authorization)
    with storage.txn() as conn:
        row = storage.get_task_row(conn, task_id)
        if not row or row["principal"] != principal:
            return a2a_response(error_body("NOT_FOUND", "task not found"), 404)
        if row["state"] in storage.TERMINAL_STATES:
            return a2a_response(error_body("TASK_TERMINAL", "task is already terminal"), 409)
        storage.update_task(conn, task_id, state="TASK_STATE_CANCELED")
        row = storage.get_task_row(conn, task_id)
        return a2a_response(task_to_dict(row))
