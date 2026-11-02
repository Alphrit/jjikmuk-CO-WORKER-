"""Start an isolated loopback AI server and exercise the v2 HTTP contract.

Uses synthetic fixtures and memory stores. --live-rag permits two real RAG
questions using the existing .env provider credentials; default makes no paid
calls. Never accesses AWS, changes .env, or writes the operational product DB.
"""

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from uuid import uuid4

AI = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(AI))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-rag", action="store_true")
    parser.add_argument("--output", default="evaluation/results/v2_server_acceptance.json")
    args = parser.parse_args()
    token, consent_secret, identity_secret = (secrets.token_urlsafe(40) for _ in range(3))
    env = {**os.environ, "FOOD_CHATBOT_ENV": "test", "FOOD_CHATBOT_DB_PROVIDER": "memory",
        "FOOD_CHATBOT_API_TOKEN": token, "FOOD_CHATBOT_ENABLE_EXTERNAL_AI": str(args.live_rag).lower(),
        "FOOD_CHATBOT_EXTERNAL_AI_CONSENT_SECRET": consent_secret,
        "FOOD_CHATBOT_USER_IDENTITY_SECRET": identity_secret, "FOOD_CHATBOT_ENABLE_CONVERSATIONS": "true",
        "FOOD_CHATBOT_ENABLE_RATE_LIMIT": "false", "FOOD_CHATBOT_STORE_RAW_MESSAGES": "false",
        "RAG_GENERAL_RESPONSE_MODE": "generated"}
    # Avoid loading any operational provider for the default acceptance suite.
    if not args.live_rag:
        env["OPENAI_API_KEY"] = ""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    base = f"http://127.0.0.1:{port}"
    report = {"scope": "actual uvicorn/HTTP, synthetic data, memory repositories",
              "live_rag": args.live_rag, "cases": []}
    fixtures = {path.name.split(".")[0]: json.loads(path.read_text())
                for path in (AI / "tests/fixtures/backend_v2").glob("*.request.json")}
    os.environ["FOOD_CHATBOT_USER_IDENTITY_SECRET"] = identity_secret
    from app.infrastructure.external_ai_consent import sign_external_ai_consent
    from app.infrastructure.request_identity import sign_conversation_identity

    def request(name, path="/chat", payload=None, *, method="POST", extra=None, signed=False,
                identity=False, expected=200, check=None):
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode() if payload is not None else b""
        headers = {"Content-Type": "application/json", "X-Food-Chatbot-Token": token}
        if payload and payload.get("analysisContext"):
            headers.update({"X-Jjikmuk-Analysis-Contract": "chat-analysis-v2",
                "X-Jjikmuk-Analysis-Context": payload["analysisContext"]["contextId"]})
        if signed:
            stamp, nonce = int(time.time()), secrets.token_urlsafe(24)
            headers.update({"X-Food-Chatbot-User-Id": "42", "X-Food-Chatbot-External-AI-Consent": "granted",
                "X-Food-Chatbot-External-AI-Timestamp": str(stamp), "X-Food-Chatbot-External-AI-Nonce": nonce,
                "X-Food-Chatbot-External-AI-Signature": sign_external_ai_consent(secret=consent_secret,
                    timestamp=stamp, method=method, path=path, user_id="42", consent="granted", nonce=nonce, body=body)})
        if identity:
            stamp, nonce = int(time.time()), secrets.token_urlsafe(24)
            headers.update({"X-Food-Chatbot-User-Id": "42", "X-Food-Chatbot-Identity-Timestamp": str(stamp),
                "X-Food-Chatbot-Identity-Nonce": nonce, "X-Food-Chatbot-Identity-Signature": sign_conversation_identity(
                    timestamp=stamp, method=method, path=path, user_id="42", nonce=nonce, body=body)})
        headers.update(extra or {})
        started = time.perf_counter()
        try:
            result = urlopen(Request(base + path, data=body if method != "GET" else None, method=method, headers=headers), timeout=180)
        except HTTPError as error:
            result = error
        data = json.loads(result.read() or b"null")
        passed = result.status == expected and (check(data) if check and result.status == expected else True)
        if payload and payload.get("analysisContext"):
            ack = result.headers.get("X-Jjikmuk-Analysis-Contract")
            passed &= ((ack == "chat-analysis-v2" and result.headers.get("X-Jjikmuk-Analysis-Context") == payload["analysisContext"]["contextId"])
                       if expected == 200 else ack is None)
        entry = dict(id=name, status=result.status, passed=bool(passed), elapsed_ms=round((time.perf_counter()-started)*1000), response=data)
        report["cases"].append(entry)
        print(json.dumps({k: entry[k] for k in ("id", "status", "passed", "elapsed_ms")}), flush=True)
        return data

    with tempfile.TemporaryDirectory(prefix="jjikmuk-v2-server-") as directory:
        with open(Path(directory) / "server.log", "w") as log:
            process = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.api.main:app", "--host", "127.0.0.1", "--port", str(port), "--log-level", "error"], cwd=AI, env=env, stdout=log, stderr=log)
            try:
                for _ in range(150):
                    if process.poll() is not None:
                        raise RuntimeError("Isolated server exited before startup")
                    try:
                        with urlopen(base + "/health", timeout=1):
                            break
                    except (URLError, TimeoutError):
                        time.sleep(0.2)
                else:
                    raise RuntimeError("Isolated server did not become ready")
                request("health", "/health", method="GET")
                request("ready", "/ready", method="GET", check=lambda d: d["ready"])
                request("openapi", "/openapi.json", method="GET", check=lambda d: "/chat" in d["paths"])
                for name, payload in fixtures.items():
                    count = len(payload["profiles"]) or (1 if payload["profile"] else 0)
                    request(name, payload=payload, check=lambda d, count=count, name=name:
                        d["backend_analysis"]["contractVersion"] == "chat-analysis-v2" and
                        (name == "no_product" or len(d["per_profile_results"]) == count))
                request("wrong_token", payload=fixtures["multi"], extra={"X-Food-Chatbot-Token": "wrong"}, expected=401)
                request("wrong_context", payload=fixtures["multi"], extra={"X-Jjikmuk-Analysis-Context": "different"}, expected=422)
                bad = deepcopy(fixtures["multi"])
                bad["analysisContext"]["profileAnalyses"].pop()
                request("missing_person", payload=bad, expected=422)
                for name, message, field in [("recipe", "간단한 레시피 추천해줘", "recipe"),
                    ("comparison", "저염 두부스낵과 고단백 두유바 비교해줘", "comparison_products"),
                    ("search", "저염 두부스낵 찾아줘", "current_product")]:
                    p = deepcopy(fixtures["no_product"])
                    p["message"] = message
                    request(name, payload=p, check=lambda d, field=field: bool(d[field]) and
                        (field != "current_product" or (d["intent"] == "product_summary" and "찾았어요" in d["answer"])))
                request("legacy", payload={"message": "안녕"}, check=lambda d: d.get("backend_analysis") is None)
                room_id = str(uuid4())
                request("create", "/conversations", {"conversation_id": room_id}, identity=True, expected=201)
                first = deepcopy(fixtures["multi"])
                first.update(conversation_id=room_id, turn_id=str(uuid4()))
                request("saved_mother_father", payload=first, identity=True)
                second = deepcopy(fixtures["single"])
                second["profile"]["nickname"] = "아빠"
                second.update(conversation_id=room_id, turn_id=str(uuid4()), message="그럼 먹어도 돼?")
                request("saved_switch", payload=second, identity=True, check=lambda d: d["active_profile_names"] == ["아빠"])
                request("saved_reopen", f"/conversations/{room_id}", method="GET", identity=True,
                    check=lambda d: len(d["messages"]) == 4)
                request("saved_delete", f"/conversations/{room_id}", method="DELETE", identity=True, expected=204)
                request("deleted_missing", f"/conversations/{room_id}", method="GET", identity=True, expected=404)
                p = deepcopy(fixtures["no_product"])
                p["message"] = "무설탕과 무가당의 차이가 뭐야?"
                denied = request("rag_without_consent", payload=p, check=lambda d: not d["external_ai_processing"]["used"])
                if args.live_rag:
                    first_rag = request("live_rag", payload=p, signed=True,
                        check=lambda d: d["answer_source"] == "rag" and bool(d["sources"]) and d["external_ai_processing"]["used"])
                    p["analysisContext"]["contextId"] = str(uuid4())
                    p["chat_history"] = [{"role": "user", "content": p["message"]}, {"role": "assistant", "content": first_rag["answer"]}]
                    p["message"] = "그럼 칼로리는?"
                    request("live_rag_followup", payload=p, signed=True,
                        check=lambda d: d["answer_source"] == "rag" and bool(d["sources"]) and d["external_ai_processing"]["used"])
            finally:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                report["summary"] = {"total": len(report["cases"]), "passed": sum(x["passed"] for x in report["cases"]),
                    "failed": [x["id"] for x in report["cases"] if not x["passed"]]}
                output = Path(args.output)
                output.parent.mkdir(parents=True, exist_ok=True)
                output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n")
    print(json.dumps(report["summary"]), flush=True)
    return int(bool(report["summary"]["failed"]))


if __name__ == "__main__":
    raise SystemExit(main())
