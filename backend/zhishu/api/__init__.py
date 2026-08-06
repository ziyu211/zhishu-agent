"""智枢智能体 api 包。"""
from .chat import router as chat_router
from .models import router as models_router
from .knowledge import router as knowledge_router
from .auth import router as auth_router
from .users import router as users_router
from .admin import router as admin_router
from .conversations import router as conversations_router
from .modules import router as modules_router
from .agents import router as agents_router
from .cron import router as cron_router
from .settings import router as settings_router
from .openai_gw import router as openai_router

__all__ = ["chat_router", "models_router", "knowledge_router", "auth_router",
           "users_router", "admin_router", "conversations_router", "modules_router",
           "agents_router", "cron_router", "settings_router", "openai_router"]
