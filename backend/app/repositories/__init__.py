"""Data access: queries only (select/add/flush), never commit. One class per aggregate."""

from app.repositories.app_settings import AppSettingRepository
from app.repositories.base import BaseRepository
from app.repositories.conversations import ConversationRepository, MessageRepository
from app.repositories.customers import CustomerRepository, CustomerStats
from app.repositories.deliveries import DeliveryRepository, LocationRequestRepository, RoutePlanRepository
from app.repositories.faq import FaqRepository
from app.repositories.orders import OrderEventRepository, OrderRepository, PaymentRepository
from app.repositories.products import ProductRepository
from app.repositories.reports import DailyReportRepository
from app.repositories.users import RefreshTokenRepository, UserRepository

__all__ = [
    "AppSettingRepository",
    "BaseRepository",
    "ConversationRepository",
    "CustomerRepository",
    "CustomerStats",
    "DailyReportRepository",
    "DeliveryRepository",
    "FaqRepository",
    "LocationRequestRepository",
    "MessageRepository",
    "OrderEventRepository",
    "OrderRepository",
    "PaymentRepository",
    "ProductRepository",
    "RefreshTokenRepository",
    "RoutePlanRepository",
    "UserRepository",
]
