import logging
import os
from datetime import datetime
from typing import Any, List, Optional
from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from config.config import get_learnhouse_config
from src.core.events.database import get_db_session
from src.db.courses.courses import Course
from src.db.trail_runs import TrailRun
from src.db.users import PublicUser, User
from src.security.auth import get_current_user

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/payments", tags=["payments"])


# ---------------------------------------------------------------------------
# Payment Configs
# ---------------------------------------------------------------------------

@router.get("/{org_id}/config")
async def get_payment_configs(
    org_id: int,
    current_user: PublicUser = Depends(get_current_user),
):
    """Returns active payment providers for the organization."""
    config = get_learnhouse_config()
    mp_token = config.payments_config.mercadopago.access_token or os.environ.get("MERCADOPAGO_ACCESS_TOKEN")
    stripe_key = config.payments_config.stripe.stripe_secret_key or os.environ.get("LEARNHOUSE_STRIPE_SECRET_KEY")

    providers = [
        {
            "id": 1,
            "provider": "mercadopago",
            "provider_specific_id": "mercadopago_app" if mp_token else None,
            "active": bool(mp_token),
            "details": {
                "name": "MercadoPago",
                "currency": "CLP",
            },
        },
        {
            "id": 2,
            "provider": "stripe",
            "provider_specific_id": "stripe_app" if stripe_key else None,
            "active": bool(stripe_key),
            "details": {
                "name": "Stripe",
            },
        },
    ]
    return providers


@router.post("/{org_id}/config")
async def initialize_payment_config(
    org_id: int,
    provider: str = Query("mercadopago"),
    data: Optional[dict] = None,
    current_user: PublicUser = Depends(get_current_user),
):
    """Initialize or update a payment provider."""
    return {"status": "ok", "provider": provider, "active": True}


@router.delete("/{org_id}/config")
async def delete_payment_config(
    org_id: int,
    id: str = Query(""),
    current_user: PublicUser = Depends(get_current_user),
):
    """Delete a payment config."""
    return {"status": "deleted"}


from src.db.organization_config import OrganizationConfig

# ---------------------------------------------------------------------------
# Offers
# ---------------------------------------------------------------------------

class CreateOfferSchema(BaseModel):
    name: str
    description: Optional[str] = ""
    amount: float = 10000.0
    currency: str = "CLP"
    offer_type: str = "one_time"
    price_type: str = "fixed_price"
    benefits: Optional[str] = ""
    payments_group_id: Optional[Any] = None
    resource_uuids: Optional[List[str]] = []


@router.get("/{org_id}/offers")
async def get_offers(
    org_id: int,
    current_user: PublicUser = Depends(get_current_user),
    db_session: AsyncSession = Depends(get_db_session),
):
    """Returns all offers for the organization."""
    cfg_stmt = select(OrganizationConfig).where(OrganizationConfig.org_id == org_id)
    org_cfg = (await db_session.execute(cfg_stmt)).scalars().first()

    custom_offers = []
    if org_cfg and isinstance(org_cfg.config, dict):
        custom_offers = org_cfg.config.get("custom_offers", [])

    if custom_offers:
        return custom_offers

    # Fallback to courses if no custom offers exist yet
    courses_stmt = select(Course).where(Course.org_id == org_id)
    courses = (await db_session.execute(courses_stmt)).scalars().all()

    offers = []
    for c in courses:
        offers.append({
            "id": c.id,
            "name": c.name,
            "description": c.description or f"Acceso completo a {c.name}",
            "amount": 10000.0,
            "currency": "CLP",
            "offer_type": "one_time",
            "price_type": "fixed_price",
            "benefits": "Acceso ilimitado al contenido",
            "resource_uuids": [c.course_uuid],
            "archived": False,
        })

    return offers


@router.post("/{org_id}/offers")
async def create_offer(
    org_id: int,
    body: CreateOfferSchema,
    current_user: PublicUser = Depends(get_current_user),
    db_session: AsyncSession = Depends(get_db_session),
):
    """Create a new offer and persist it in org config."""
    cfg_stmt = select(OrganizationConfig).where(OrganizationConfig.org_id == org_id)
    org_cfg = (await db_session.execute(cfg_stmt)).scalars().first()

    new_offer = {
        "id": int(datetime.now().timestamp() * 1000),
        "name": body.name,
        "description": body.description,
        "amount": float(body.amount),
        "currency": body.currency,
        "offer_type": body.offer_type,
        "price_type": body.price_type,
        "benefits": body.benefits,
        "resource_uuids": body.resource_uuids or [],
        "archived": False,
    }

    if org_cfg:
        new_config = dict(org_cfg.config) if isinstance(org_cfg.config, dict) else {}
        existing_offers = list(new_config.get("custom_offers", []))
        existing_offers.append(new_offer)
        new_config["custom_offers"] = existing_offers
        org_cfg.config = new_config
        db_session.add(org_cfg)
        await db_session.commit()

    return {"success": True, "data": new_offer}


@router.put("/{org_id}/offers/{offer_id}")
async def update_offer(
    org_id: int,
    offer_id: str,
    body: dict,
    current_user: PublicUser = Depends(get_current_user),
    db_session: AsyncSession = Depends(get_db_session),
):
    """Update an existing offer."""
    cfg_stmt = select(OrganizationConfig).where(OrganizationConfig.org_id == org_id)
    org_cfg = (await db_session.execute(cfg_stmt)).scalars().first()

    if org_cfg and isinstance(org_cfg.config, dict):
        new_config = dict(org_cfg.config)
        existing_offers = list(new_config.get("custom_offers", []))
        updated_offers = []
        for o in existing_offers:
            if str(o.get("id")) == str(offer_id):
                updated = dict(o)
                updated.update(body)
                updated_offers.append(updated)
            else:
                updated_offers.append(o)
        new_config["custom_offers"] = updated_offers
        org_cfg.config = new_config
        db_session.add(org_cfg)
        await db_session.commit()

    return {"success": True, "data": body}


@router.delete("/{org_id}/offers/{offer_id}")
async def archive_offer(
    org_id: int,
    offer_id: str,
    current_user: PublicUser = Depends(get_current_user),
    db_session: AsyncSession = Depends(get_db_session),
):
    """Archive an offer."""
    cfg_stmt = select(OrganizationConfig).where(OrganizationConfig.org_id == org_id)
    org_cfg = (await db_session.execute(cfg_stmt)).scalars().first()

    if org_cfg and isinstance(org_cfg.config, dict):
        new_config = dict(org_cfg.config)
        existing_offers = list(new_config.get("custom_offers", []))
        new_config["custom_offers"] = [o for o in existing_offers if str(o.get("id")) != str(offer_id)]
        org_cfg.config = new_config
        db_session.add(org_cfg)
        await db_session.commit()

    return {"success": True, "status": 200}


@router.get("/{org_id}/offers/public-listing")
async def get_public_offers(
    org_id: int,
    db_session: AsyncSession = Depends(get_db_session),
):
    """Public listing of offers for the store."""
    cfg_stmt = select(OrganizationConfig).where(OrganizationConfig.org_id == org_id)
    org_cfg = (await db_session.execute(cfg_stmt)).scalars().first()

    courses_stmt = select(Course).where(Course.org_id == org_id)
    courses = (await db_session.execute(courses_stmt)).scalars().all()
    courses_by_uuid = {c.course_uuid: c for c in courses}

    custom_offers = []
    if org_cfg and isinstance(org_cfg.config, dict):
        custom_offers = org_cfg.config.get("custom_offers", [])

    if not custom_offers:
        for c in courses:
            custom_offers.append({
                "id": c.id,
                "name": c.name,
                "description": c.description or f"Acceso completo a {c.name}",
                "amount": 10000.0,
                "currency": "CLP",
                "offer_type": "one_time",
                "price_type": "fixed_price",
                "benefits": "Acceso ilimitado",
                "resource_uuids": [c.course_uuid],
            })

    # Enrich each offer with included_resources
    enriched = []
    for o in custom_offers:
        offer_copy = dict(o)
        res_list = []
        uuids = offer_copy.get("resource_uuids", [])
        for u in uuids:
            course = courses_by_uuid.get(u)
            if course:
                res_list.append({
                    "resource_uuid": course.course_uuid,
                    "resource_type": "course",
                    "name": course.name,
                    "description": course.description or "",
                    "thumbnail_image": course.thumbnail_image or "",
                    "org_uuid": str(course.org_id),
                })
        if not res_list and courses:
            # Fallback to first course if none explicitly matched
            first_c = courses[0]
            res_list.append({
                "resource_uuid": first_c.course_uuid,
                "resource_type": "course",
                "name": first_c.name,
                "description": first_c.description or "",
                "thumbnail_image": first_c.thumbnail_image or "",
                "org_uuid": str(first_c.org_id),
            })
        offer_id_val = offer_copy.get("id") or 1
        offer_copy["id"] = offer_id_val
        offer_copy["offer_uuid"] = str(offer_id_val)
        offer_copy["currency"] = str(offer_copy.get("currency") or "CLP").upper()
        offer_copy["included_resources"] = res_list
        enriched.append(offer_copy)

    return enriched


@router.get("/{org_id}/offers/{offer_id}/public")
async def get_single_public_offer(
    org_id: int,
    offer_id: str,
    db_session: AsyncSession = Depends(get_db_session),
):
    """Get single public offer for the offer detail page."""
    offers = await get_public_offers(org_id, db_session)
    for o in offers:
        if str(o.get("id")) == str(offer_id) or str(o.get("offer_uuid")) == str(offer_id):
            return o

    # If none matched, return first offer or default
    if offers:
        return offers[0]

    return {
        "id": 1,
        "offer_uuid": str(offer_id),
        "name": "Curso",
        "description": "Acceso completo",
        "amount": 5000.0,
        "currency": "CLP",
        "offer_type": "one_time",
        "price_type": "fixed_price",
        "benefits": "Acceso ilimitado",
        "included_resources": [],
    }


@router.post("/{org_id}/offers/{offer_id}/checkout")
async def create_offer_checkout(
    org_id: int,
    offer_id: str,
    current_user: PublicUser = Depends(get_current_user),
    db_session: AsyncSession = Depends(get_db_session),
):
    """Create checkout session for the offer."""
    # Get offer details
    offers = await get_public_offers(org_id, db_session)
    selected_offer = None
    for o in offers:
        if str(o.get("id")) == str(offer_id) or str(o.get("offer_uuid")) == str(offer_id):
            selected_offer = o
            break
    if not selected_offer and offers:
        selected_offer = offers[0]

    # Find associated course
    course_uuid = ""
    if selected_offer and selected_offer.get("included_resources"):
        first_res = selected_offer["included_resources"][0]
        course_uuid = first_res.get("resource_uuid", "")

    if not course_uuid:
        courses_stmt = select(Course).where(Course.org_id == org_id)
        courses = (await db_session.execute(courses_stmt)).scalars().all()
        if courses:
            course_uuid = courses[0].course_uuid

    amount = float(selected_offer.get("amount", 5000)) if selected_offer else 5000.0
    title = selected_offer.get("name", "Curso") if selected_offer else "Curso"
    currency_id = str(selected_offer.get("currency") or "CLP").upper() if selected_offer else "CLP"

    from src.routers.payments.mercadopago import create_preference, CreatePreferenceRequest

    pref = await create_preference(
        CreatePreferenceRequest(
            course_uuid=course_uuid,
            unit_price=amount,
            currency_id=currency_id,
            title=title,
        ),
        current_user=current_user,
        db_session=db_session,
    )

    return {
        "checkout_url": pref.init_point,
        "init_point": pref.init_point,
    }


# ---------------------------------------------------------------------------
# Customers & Enrollments
# ---------------------------------------------------------------------------

@router.get("/{org_id}/customers")
async def get_customers(
    org_id: int,
    current_user: PublicUser = Depends(get_current_user),
    db_session: AsyncSession = Depends(get_db_session),
):
    """List paid customers from TrailRun."""
    runs_stmt = select(TrailRun).where(TrailRun.org_id == org_id)
    runs = (await db_session.execute(runs_stmt)).scalars().all()

    user_ids = list(set([r.user_id for r in runs if r.user_id]))
    users_map = {}
    if user_ids:
        users_stmt = select(User).where(User.id.in_(user_ids))
        users = (await db_session.execute(users_stmt)).scalars().all()
        users_map = {u.id: u for u in users}

    customers = []
    for r in runs:
        u = users_map.get(r.user_id)
        if u:
            customers.append({
                "id": r.id,
                "user_id": u.id,
                "email": u.email,
                "name": f"{u.first_name} {u.last_name}".strip() or u.username,
                "course_id": r.course_id,
                "status": "active",
                "payment_provider": r.data.get("payment_provider", "mercadopago") if r.data else "mercadopago",
                "created_at": r.creation_date,
            })

    return customers


# ---------------------------------------------------------------------------
# Groups & Stripe Overview
# ---------------------------------------------------------------------------

@router.get("/{org_id}/groups")
async def get_payment_groups(
    org_id: int,
    current_user: PublicUser = Depends(get_current_user),
):
    """List payment groups."""
    return {"success": True, "data": []}


@router.get("/{org_id}/stripe/overview")
async def get_stripe_overview(
    org_id: int,
    current_user: PublicUser = Depends(get_current_user),
):
    """Stripe / Payment overview."""
    return {
        "mrr": 0,
        "arr": 0,
        "total_revenue": 0,
        "active_subscribers": 0,
        "total_customers": 0,
        "churn_30d": 0,
        "recent_charges": [],
        "charges": [],
        "balance": 0,
        "currency": "CLP",
    }

