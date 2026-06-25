"""
Top-level API v1 router — aggregates all sub-routers.
"""
from fastapi import APIRouter

from app.api.v1.upload.router import router as upload_router
from app.api.v1.reconciliation.router import router as recon_router
from app.api.v1.vendors.router import router as vendors_router
from app.api.v1.reports.router import router as reports_router
from app.api.v1.admin.router import router as admin_router
from app.api.v1.parse.router import router as parse_router

api_router = APIRouter()

api_router.include_router(parse_router,   prefix="/files",   tags=["Parse"])
api_router.include_router(upload_router,  prefix="/upload",  tags=["Upload"])
api_router.include_router(recon_router,   prefix="/recon",   tags=["Reconciliation"])
api_router.include_router(vendors_router, prefix="/vendors", tags=["Vendors"])
api_router.include_router(reports_router, prefix="/reports", tags=["Reports"])
api_router.include_router(admin_router,   prefix="/admin",   tags=["Admin"])
