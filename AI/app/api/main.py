import os
from contextlib import asynccontextmanager
from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI
from app.services.activity import get_activity_repository, list_user_activities, log_chat_activity, set_activity_repository
from app.core.models import ChatRequest, ChatResponse
from app.api.adapters import normalize_active_profiles, normalize_chat_request
from app.services.product import (
    find_product_by_barcode,
    find_product_by_report_number,
)
from app.services.product import set_product_repository
from app.infrastructure.repository import SQLiteActivityRepository, SQLiteProductRepository
from app.services.chat import generate_chat_response
from app.services.conversation_router import resolve_product_context


def configure_repositories() -> None:
    db_path = os.getenv("FOOD_CHATBOT_DB_PATH")
    if not db_path:
        return

    set_product_repository(SQLiteProductRepository(db_path=db_path))
    set_activity_repository(SQLiteActivityRepository(db_path=db_path))


@asynccontextmanager
async def lifespan(app: FastAPI):
    configure_repositories()
    yield


app = FastAPI(title="Food Chatbot MVP", lifespan=lifespan)


@app.get("/")
def root():
    return {"message": "Food chatbot is running"}


@app.get("/health")
def health():
    return {"message": "Food chatbot is running"}


@app.post("/chat", response_model=ChatResponse)
def chat(req: ChatRequest):
    profile, product, barcode, report_number = normalize_chat_request(req)
    active_profiles = normalize_active_profiles(req)
    product, barcode, report_number, confirmation_response = resolve_product_context(
        message=req.message,
        profile=profile,
        product=product,
        barcode=barcode,
        report_number=report_number,
        chat_history=req.chat_history,
        conversation_state=req.conversation_state,
    )
    if confirmation_response is not None:
        return confirmation_response

    response = generate_chat_response(
        profile=profile,
        product=product,
        message=req.message,
        active_profiles=active_profiles,
        chat_history=req.chat_history,
    )
    log_chat_activity(
        profile=profile,
        product=product,
        barcode=barcode,
        report_number=report_number,
        message=req.message,
        intent=response.intent,
    )
    return response


@app.get("/products/barcode/{barcode}")
def get_product_by_barcode(barcode: str):
    product = find_product_by_barcode(barcode)
    if product is None:
        return {"found": False, "barcode": barcode}
    return {"found": True, "product": product.model_dump()}


@app.get("/products/report-number/{report_number}")
def get_product_by_report_number(report_number: str):
    product = find_product_by_report_number(report_number)
    if product is None:
        return {"found": False, "report_number": report_number}
    return {"found": True, "product": product.model_dump()}


@app.get("/history/user/{user_id}")
def get_user_history(user_id: int, limit: int = 20):
    records = list_user_activities(user_id=user_id, limit=limit)
    return {
        "user_id": user_id,
        "count": len(records),
        "history": [record.model_dump() for record in records],
        "repository": type(get_activity_repository()).__name__,
    }
