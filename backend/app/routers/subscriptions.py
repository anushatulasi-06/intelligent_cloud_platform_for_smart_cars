"""Subscriptions API (C1, F3): which alert categories an owner wants to hear about.
Owners only. A car that is not theirs answers 403, an unknown subscription 404."""
from fastapi import APIRouter, Depends, Path, Query, Response, status

from app.auth import CurrentUser, require_role
from app.schemas.admin_config import ErrorOut
from app.schemas.subscriptions import SubscriptionOut, SubscriptionUpsert
from app.services.subscription_service import SubscriptionService

ERR = {"model": ErrorOut}
owner_only = require_role("owner")

router = APIRouter(prefix="/subscriptions", tags=["subscriptions"], responses={401: ERR, 403: ERR})


@router.get("", response_model=list[SubscriptionOut])
def list_subscriptions(car_id: int | None = Query(default=None, ge=1),
                       user: CurrentUser = Depends(owner_only)):
    return SubscriptionService(user).list_for_owner(car_id)


@router.post("", response_model=SubscriptionOut, responses={422: ERR})
def upsert_subscription(body: SubscriptionUpsert, response: Response,
                        user: CurrentUser = Depends(owner_only)):
    """201 when the row is new, 200 when an existing one is updated."""
    row, created = SubscriptionService(user).upsert(
        body.car_id, body.alert_type, body.enabled, body.notify_method)
    response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
    return row


@router.delete("/{sub_id}", status_code=status.HTTP_204_NO_CONTENT, responses={404: ERR})
def delete_subscription(sub_id: int = Path(ge=1), user: CurrentUser = Depends(owner_only)):
    """Removing a row has the same effect as turning the category off (BR-1.4)."""
    SubscriptionService(user).remove(sub_id)
