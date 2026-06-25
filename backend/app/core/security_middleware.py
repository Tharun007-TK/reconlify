from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
import time
import structlog
import redis.asyncio as redis
from app.config import settings

logger = structlog.get_logger(__name__)

# Global Redis connection for rate limiting
redis_client = redis.from_url(settings.REDIS_URL, encoding="utf-8", decode_responses=True)

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """
    Injects OWASP recommended security headers for API responses.
    """
    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["X-XSS-Protection"] = "1; mode=block"
        response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
        return response


class RateLimitMiddleware(BaseHTTPMiddleware):
    """
    Redis-backed sliding window rate limiter for API protection.
    Prevents memory leaks across distributed FastAPI workers.
    """
    def __init__(self, app: FastAPI, max_requests: int = 200, window_seconds: int = 60):
        super().__init__(app)
        self.max_requests = max_requests
        self.window_seconds = window_seconds

    async def dispatch(self, request: Request, call_next) -> Response:
        # Exempt internal/health routes from strict rate limiting
        if request.url.path.startswith(("/docs", "/openapi.json", "/health")):
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        now = time.time()
        key = f"rate_limit:{client_ip}"
        
        try:
            # Atomic sliding window using Redis ZSET
            async with redis_client.pipeline(transaction=True) as pipe:
                # Remove timestamps older than our window
                pipe.zremrangebyscore(key, 0, now - self.window_seconds)
                # Count current requests in window
                pipe.zcard(key)
                # Add current request
                pipe.zadd(key, {str(now): now})
                # Set TTL on the key to prevent memory leaks if IP goes dormant
                pipe.expire(key, self.window_seconds)
                
                results = await pipe.execute()
                
            request_count = results[1]
            
            if request_count >= self.max_requests:
                logger.warning("security.rate_limit_exceeded", ip=client_ip, path=request.url.path)
                return Response(
                    content='{"detail": "Rate limit exceeded"}',
                    status_code=429,
                    media_type="application/json"
                )
        except Exception as exc:
            # Fail open if Redis is down, log heavily
            logger.error("security.rate_limit.redis_error", error=str(exc))
            
        response = await call_next(request)
        
        # We don't easily have exact remaining count due to async nature, but we can estimate
        # or omit. For simplicity, omit headers on failure or return max.
        
        return response
