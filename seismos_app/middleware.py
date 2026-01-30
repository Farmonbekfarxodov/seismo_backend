import time
import logging

logger = logging.getLogger('performance')


class PerformanceMiddleware:
    """
    ✅ Har bir request'ning performance'ini log qilish
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        start_time = time.time()

        response = self.get_response(request)

        duration = time.time() - start_time

        logger.info(
            f"Path: {request.path} | "
            f"Method: {request.method} | "
            f"Duration: {duration:.2f}s | "
            f"Status: {response.status_code}"
        )

        # Slow request warning
        if duration > 5:
            logger.warning(f"SLOW REQUEST: {request.path} took {duration:.2f}s")

        return response