"""Configure a local AI/Spring bridge without printing credentials or calling AI."""

from __future__ import annotations

import argparse
from pathlib import Path
import secrets
from urllib.parse import urlsplit

from dotenv import dotenv_values, set_key


def configure_bridge(ai_env: Path, backend_properties: Path, *, enable_external: bool = False,
                     response_mode: str = "generated", base_url: str = "http://localhost:8000") -> None:
    endpoint = urlsplit(base_url)
    if endpoint.scheme not in {"http", "https"} or not endpoint.hostname or endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
        raise ValueError("AI 서버 주소를 확인해주세요.")
    if response_mode not in {"extractive", "generated"}:
        raise ValueError("지원하지 않는 답변 모드입니다.")
    values = dotenv_values(ai_env, interpolate=False) if ai_env.exists() else {}
    if enable_external and not (values.get("OPENAI_API_KEY") or "").strip():
        raise ValueError("AI/.env에 OPENAI_API_KEY를 설정한 뒤 외부 AI를 활성화해주세요.")
    api_token = (values.get("FOOD_CHATBOT_API_TOKEN") or "").strip()
    identity_secret = (values.get("FOOD_CHATBOT_USER_IDENTITY_SECRET") or "").strip()
    secret = (values.get("FOOD_CHATBOT_EXTERNAL_AI_CONSENT_SECRET") or "").strip()
    if not (len(secret.encode()) >= 32 and all("!" <= c <= "~" for c in secret)
            and secret not in {api_token, identity_secret}):
        secret = secrets.token_urlsafe(48)
    enabled = "true" if enable_external else "false"
    ai_env.parent.mkdir(parents=True, exist_ok=True)
    ai_env.touch(exist_ok=True)
    for key, value in {
        "FOOD_CHATBOT_ENABLE_EXTERNAL_AI": enabled,
        "FOOD_CHATBOT_EXTERNAL_AI_CONSENT_SECRET": secret,
        "RAG_GENERAL_RESPONSE_MODE": response_mode,
    }.items():
        set_key(str(ai_env), key, value)

    def escaped(value: str) -> str:
        return value.replace("\\", "\\\\").replace("\n", "\\n").replace("\r", "\\r")

    backend_properties.parent.mkdir(parents=True, exist_ok=True)
    backend_properties.write_text("# Generated local chat bridge settings; do not commit.\n" + "\n".join(
        key + "=" + escaped(value) for key, value in {
            "FOOD_CHATBOT_AI_BASE_URL": base_url.rstrip("/"),
            "FOOD_CHATBOT_ENABLE_EXTERNAL_AI": enabled,
            "FOOD_CHATBOT_EXTERNAL_AI_CONSENT_SECRET": secret,
            "FOOD_CHATBOT_API_TOKEN": api_token,
        }.items()
    ) + "\n", encoding="utf-8")
    ai_env.chmod(0o600)
    backend_properties.chmod(0o600)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--enable-external", action="store_true")
    parser.add_argument("--response-mode", choices=["extractive", "generated"], default="generated")
    parser.add_argument("--base-url", default="http://localhost:8000")
    args = parser.parse_args()
    ai_dir = Path(__file__).resolve().parents[1]
    try:
        configure_bridge(ai_dir / ".env", ai_dir.parent / "backend/.chat-ai.properties",
                         enable_external=args.enable_external, response_mode=args.response_mode, base_url=args.base_url)
    except (ValueError, OSError) as error:
        # Do not echo exception contents, paths/URLs containing credentials or keys.
        print("챗봇 설정을 완료하지 못했습니다. API 키, 서버 주소와 파일 권한을 확인해주세요.")
        return 1
    print("AI·백엔드 로컬 연결 설정 완료. 두 서버를 재시작한 뒤 앱에서 외부 AI 사용 동의를 선택하세요.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
