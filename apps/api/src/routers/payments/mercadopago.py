import logging
import os
from datetime import datetime
from typing import Optional
from uuid import uuid4
import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from config.config import get_learnhouse_config
from src.core.events.database import get_db_session
from src.db.courses.courses import Course
from src.db.organizations import Organization
from src.db.trail_runs import StatusEnum, TrailRun
from src.db.trails import Trail
from src.db.users import AnonymousUser, PublicUser, User
from src.security.auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/payments/mercadopago", tags=["payments", "mercadopago"])


class CreatePreferenceRequest(BaseModel):
    course_uuid: str
    unit_price: Optional[float] = 10000.0  # Default in CLP if course has no price
    currency_id: Optional[str] = "CLP"
    title: Optional[str] = None


class PreferenceResponse(BaseModel):
    id: str
    init_point: str
    sandbox_init_point: Optional[str] = None


@router.get("/config")
async def get_mercadopago_public_config():
    """Returns whether MercadoPago is configured and the public key for frontend."""
    config = get_learnhouse_config()
    mp_config = config.payments_config.mercadopago
    public_key = mp_config.public_key or os.environ.get("MERCADOPAGO_PUBLIC_KEY", "")
    has_token = bool(mp_config.access_token or os.environ.get("MERCADOPAGO_ACCESS_TOKEN"))
    return {
        "is_enabled": has_token,
        "public_key": public_key,
        "default_currency": "CLP",
    }


@router.post("/preference", response_model=PreferenceResponse)
async def create_preference(
    body: CreatePreferenceRequest,
    current_user: PublicUser = Depends(get_current_user),
    db_session: AsyncSession = Depends(get_db_session),
):
    """Creates a MercadoPago Checkout Pro preference for purchasing a course."""
    if isinstance(current_user, AnonymousUser) or not current_user.id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="You must be logged in to purchase a course",
        )

    config = get_learnhouse_config()
    mp_access_token = (
        config.payments_config.mercadopago.access_token
        or os.environ.get("MERCADOPAGO_ACCESS_TOKEN")
    )

    if not mp_access_token:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MercadoPago is not configured on this instance. Please set MERCADOPAGO_ACCESS_TOKEN.",
        )

    # 1. Fetch Course
    course_stmt = select(Course).where(Course.course_uuid == body.course_uuid)
    course = (await db_session.execute(course_stmt)).scalars().first()
    if not course:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Course not found",
        )

    # 2. Fetch User
    user_stmt = select(User).where(User.id == current_user.id)
    user = (await db_session.execute(user_stmt)).scalars().first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found",
        )

    # 3. Resolve Frontend & Backend URLs
    frontend_domain = config.hosting_config.frontend_domain or "localhost:3000"
    protocol = "https" if config.hosting_config.ssl else "http"
    if "://" not in frontend_domain:
        frontend_url = f"{protocol}://{frontend_domain}"
    else:
        frontend_url = frontend_domain.rstrip("/")

    backend_domain = config.hosting_config.domain or "localhost:8000"
    if "://" not in backend_domain:
        backend_url = f"{protocol}://{backend_domain}"
    else:
        backend_url = backend_domain.rstrip("/")

    item_title = body.title or course.name or "Curso LearnHouse"
    unit_price = float(body.unit_price) if body.unit_price and body.unit_price > 0 else 10000.0
    currency_id = (body.currency_id or "CLP").upper()

    # External reference formatted as: user_id:course_id:org_id
    external_ref = f"{user.id}:{course.id}:{course.org_id}"

    preference_data = {
        "items": [
            {
                "id": str(course.course_uuid),
                "title": item_title[:250],
                "description": (course.description or item_title)[:250],
                "quantity": 1,
                "unit_price": unit_price,
                "currency_id": currency_id,
            }
        ],
        "payer": {
            "email": user.email,
            "name": user.first_name or user.username,
            "surname": user.last_name or "",
        },
        "back_urls": {
            "success": f"{frontend_url}/courses/{course.course_uuid}?payment=success",
            "failure": f"{frontend_url}/courses/{course.course_uuid}?payment=failure",
            "pending": f"{frontend_url}/courses/{course.course_uuid}?payment=pending",
        },
        "auto_return": "approved",
        "external_reference": external_ref,
        "notification_url": f"{backend_url}/api/v1/payments/mercadopago/webhook",
        "statement_descriptor": "LEARNHOUSE",
    }

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            res = await client.post(
                "https://api.mercadopago.com/checkout/preferences",
                json=preference_data,
                headers={
                    "Authorization": f"Bearer {mp_access_token}",
                    "Content-Type": "application/json",
                },
            )
            res.raise_for_status()
            data = res.json()
            return PreferenceResponse(
                id=data["id"],
                init_point=data["init_point"],
                sandbox_init_point=data.get("sandbox_init_point"),
            )
    except httpx.HTTPStatusError as e:
        logger.error(f"MercadoPago API error: {e.response.status_code} - {e.response.text}")
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"MercadoPago preference creation failed: {e.response.text}",
        )
    except Exception as e:
        logger.error(f"Failed to create MercadoPago preference: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error connecting to MercadoPago: {str(e)}",
        )


@router.post("/webhook")
async def mercadopago_webhook(
    request: Request,
    topic: Optional[str] = Query(None),
    id: Optional[str] = Query(None),
    data_id: Optional[str] = Query(None, alias="data.id"),
    db_session: AsyncSession = Depends(get_db_session),
):
    """
    Handles MercadoPago Instant Payment Notifications (IPN / Webhooks).
    Automatically enrolls students upon confirmed payment ('approved').
    """
    config = get_learnhouse_config()
    mp_access_token = (
        config.payments_config.mercadopago.access_token
        or os.environ.get("MERCADOPAGO_ACCESS_TOKEN")
    )

    if not mp_access_token:
        logger.warning("Received MercadoPago webhook but MERCADOPAGO_ACCESS_TOKEN is not set.")
        return {"status": "ignored", "reason": "no_token"}

    # Extract payment ID from JSON body or query params
    payment_id = id or data_id
    notification_type = topic

    try:
        json_body = await request.json()
        if isinstance(json_body, dict):
            if not notification_type and "type" in json_body:
                notification_type = json_body.get("type")
            if not payment_id and "data" in json_body and isinstance(json_body["data"], dict):
                payment_id = json_body["data"].get("id")
            if not payment_id and "id" in json_body:
                payment_id = json_body.get("id")
    except Exception:
        pass

    if not payment_id:
        return {"status": "received", "action": "ignored_no_payment_id"}

    # Verify payment status with MercadoPago API
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            res = await client.get(
                f"https://api.mercadopago.com/v1/payments/{payment_id}",
                headers={"Authorization": f"Bearer {mp_access_token}"},
            )
            if res.status_code != 200:
                logger.warning(f"Could not verify payment {payment_id}: {res.text}")
                return {"status": "error", "reason": "payment_lookup_failed"}

            payment_data = res.json()
            status_val = payment_data.get("status")
            external_reference = payment_data.get("external_reference")

            if status_val != "approved":
                logger.info(f"MercadoPago payment {payment_id} status is '{status_val}', skipping enrollment.")
                return {"status": "pending_or_failed", "payment_status": status_val}

            if not external_reference or ":" not in external_reference:
                logger.error(f"Payment {payment_id} has invalid external_reference: {external_reference}")
                return {"status": "error", "reason": "invalid_external_reference"}

            parts = external_reference.split(":")
            user_id = int(parts[0])
            course_id = int(parts[1])
            org_id = int(parts[2]) if len(parts) > 2 else 1

            # 1. Ensure Trail exists for user and org
            trail_stmt = select(Trail).where(
                Trail.org_id == org_id,
                Trail.user_id == user_id,
            )
            trail = (await db_session.execute(trail_stmt)).scalars().first()
            if not trail:
                now_str = str(datetime.now())
                trail = Trail(
                    org_id=org_id,
                    user_id=user_id,
                    trail_uuid=f"trail_{uuid4()}",
                    creation_date=now_str,
                    update_date=now_str,
                )
                db_session.add(trail)
                await db_session.commit()
                await db_session.refresh(trail)

            # 2. Check if already enrolled in TrailRun
            trail_run_stmt = select(TrailRun).where(
                TrailRun.trail_id == trail.id,
                TrailRun.course_id == course_id,
                TrailRun.user_id == user_id,
            )
            existing_run = (await db_session.execute(trail_run_stmt)).scalars().first()

            if not existing_run:
                now_str = str(datetime.now())
                new_run = TrailRun(
                    trail_id=trail.id,
                    course_id=course_id,
                    org_id=org_id,
                    user_id=user_id,
                    status=StatusEnum.STATUS_IN_PROGRESS,
                    data={"payment_provider": "mercadopago", "payment_id": str(payment_id)},
                    creation_date=now_str,
                    update_date=now_str,
                )
                db_session.add(new_run)
                await db_session.commit()
                logger.info(f"User {user_id} enrolled in course {course_id} via MercadoPago payment {payment_id}")

            return {
                "status": "success",
                "payment_id": payment_id,
                "enrolled": True,
                "user_id": user_id,
                "course_id": course_id,
            }

    except Exception as e:
        logger.error(f"Error processing MercadoPago webhook: {e}")
        return {"status": "error", "error": str(e)}
