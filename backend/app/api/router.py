"""
API 汇总路由分发模块
"""
from fastapi import APIRouter
from .v1 import (
    health, accounts, conversations, chat, search, quota, audit, oauth,
    models, settings, sync, system, memories, workspaces, loop, skills,
    artifacts, scheduled
)

api_router = APIRouter(prefix="/api/v1")
api_router.include_router(health.router)
api_router.include_router(workspaces.router)
api_router.include_router(loop.router)
api_router.include_router(skills.router)
api_router.include_router(artifacts.router)
api_router.include_router(scheduled.router)
api_router.include_router(accounts.router)
api_router.include_router(conversations.router)
api_router.include_router(chat.router)
api_router.include_router(search.router)
api_router.include_router(quota.router)
api_router.include_router(audit.router)
api_router.include_router(oauth.router)
api_router.include_router(models.router)
api_router.include_router(settings.router)
api_router.include_router(sync.router)
api_router.include_router(system.router)
api_router.include_router(memories.router)
