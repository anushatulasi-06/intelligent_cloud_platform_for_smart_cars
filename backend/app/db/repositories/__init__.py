"""Repositories: the only code that talks to PostgreSQL or MongoDB (BR-5.1).
One repository per data set, named by the data (not by component).

PostgreSQL: UserRepo, CarRepo, DeviceRepo, SubscriptionRepo, AlertTypeRepo, ModelRepo, JobRepo
MongoDB:    AlertRepo, PositionRepo, RecordRepo
"""
from app.db.repositories.alert_repo import AlertRepo
from app.db.repositories.alert_type_repo import AlertTypeRepo
from app.db.repositories.base import BaseRepository, MongoRepo, SqlRepo
from app.db.repositories.car_repo import CarRepo
from app.db.repositories.device_repo import DeviceRepo
from app.db.repositories.job_repo import JobRepo
from app.db.repositories.model_repo import ModelRepo
from app.db.repositories.position_repo import PositionRepo
from app.db.repositories.record_repo import RecordRepo
from app.db.repositories.subscription_repo import SubscriptionRepo
from app.db.repositories.user_repo import UserRepo

__all__ = [
    "BaseRepository", "SqlRepo", "MongoRepo",
    "UserRepo", "CarRepo", "DeviceRepo", "SubscriptionRepo", "AlertTypeRepo", "ModelRepo", "JobRepo",
    "AlertRepo", "PositionRepo", "RecordRepo",
]
