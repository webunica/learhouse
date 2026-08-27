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
        return {"success": True, "data": custom_offers}

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

    return {"success": True, "data": offers}


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

    custom_offers = []
    if org_cfg and isinstance(org_cfg.config, dict):
        custom_offers = org_cfg.config.get("custom_offers", [])

    if custom_offers:
        return {"success": True, "data": custom_offers}

    courses_stmt = select(Course).where(Course.org_id == org_id)
    courses = (await db_session.execute(courses_stmt)).scalars().all()

    offers = []
    for c in courses:
        offers.append({
            "id": c.id,
            "offer_id": c.id,
            "name": c.name,
            "offer_name": c.name,
            "description": c.description or f"Acceso completo a {c.name}",
            "amount": 10000.0,
            "currency": "CLP",
            "offer_type": "one_time",
            "price_type": "fixed_price",
            "resource_uuids": [c.course_uuid],
        })

    return {"success": True, "data": offers}


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

