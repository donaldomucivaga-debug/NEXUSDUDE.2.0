"""Services for NexusDude."""
from app.services.inventory_service import inventory_service
from app.services.zabbix_service import zabbix_service

__all__ = ["inventory_service", "zabbix_service"]
