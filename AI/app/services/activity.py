from typing import List, Optional

from app.core.models import ActivityRecord, Product, Profile
from app.infrastructure.repository import ActivityRepository, InMemoryActivityRepository


_default_activity_repository: ActivityRepository = InMemoryActivityRepository()


def get_activity_repository() -> ActivityRepository:
    return _default_activity_repository


def set_activity_repository(repository: ActivityRepository) -> None:
    global _default_activity_repository
    _default_activity_repository = repository


def log_chat_activity(
    profile: Optional[Profile],
    product: Optional[Product],
    barcode: Optional[str],
    report_number: Optional[str],
    message: str,
    intent: str,
    repository: Optional[ActivityRepository] = None,
) -> None:
    repo = repository or get_activity_repository()
    repo.save(
        ActivityRecord(
            user_id=profile.user_id if profile else None,
            barcode=barcode or (product.barcode if product else None),
            report_number=report_number or (product.report_number if product else None),
            product_id=product.product_id if product else None,
            action_type="SCAN",
            message=message,
            intent=intent,
        )
    )


def list_user_activities(
    user_id: int,
    limit: int = 20,
    repository: Optional[ActivityRepository] = None,
) -> List[ActivityRecord]:
    repo = repository or get_activity_repository()
    return repo.list_by_user_id(user_id=user_id, limit=limit)
