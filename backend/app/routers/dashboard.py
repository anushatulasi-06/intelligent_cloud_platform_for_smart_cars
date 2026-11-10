"""Dashboard API (C1): the owner view (F2) and the cloud staff fleet view and report (F6)."""
from datetime import datetime

from fastapi import APIRouter, Depends, Query

from app.auth import CurrentUser, require_role
from app.schemas.admin_config import ErrorOut
from app.schemas.dashboard import AlertReport, FleetSummary, GroupBy, OwnerDashboard
from app.services.dashboard_service import MAX_WINDOW_DAYS, DashboardService

ERR = {"model": ErrorOut}
staff = require_role("cloud_staff")

router = APIRouter(tags=["dashboard"], responses={401: ERR, 403: ERR})


@router.get("/dashboard/owner", response_model=OwnerDashboard)
def owner_dashboard(user: CurrentUser = Depends(require_role("owner"))):
    """The signed-in owner's cars, their alert counters and their 5 newest alerts."""
    return DashboardService().owner_overview(user.id)


@router.get("/admin/fleet-summary", response_model=FleetSummary, responses={422: ERR})
def fleet_summary(window_days: int = Query(default=7, ge=1, le=MAX_WINDOW_DAYS),
                  _: CurrentUser = Depends(staff)):
    return DashboardService().fleet_summary(window_days)


@router.get("/admin/reports/alerts", response_model=AlertReport, responses={422: ERR})
def alert_report(from_: datetime = Query(alias="from"), to: datetime = Query(),
                 group_by: GroupBy = "sound_type",
                 car_id: int | None = Query(default=None, ge=1),
                 _: CurrentUser = Depends(staff)):
    return DashboardService().alert_report(from_, to, group_by, car_id)
