import time
import asyncio
import socket
import os
from typing import Callable, Optional
from collections import deque
from fastapi import Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import ASYNCHRONOUS
import logging

logger = logging.getLogger(__name__)


class MetricsMiddleware(BaseHTTPMiddleware):
    """
    Middleware to track API metrics and send them to InfluxDB

    Features:
    - Hostname tracking for multi-host deployments
    - Asynchronous writes to InfluxDB
    - Internal queueing with automatic retry on failures
    - Graceful degradation when InfluxDB is unavailable
    """

    def __init__(
        self,
        app,
        influx_url: str,
        influx_token: str,
        influx_org: str,
        influx_bucket: str,
        hostname: Optional[str] = None,
        max_queue_size: int = 10000,
        batch_size: int = 100,
        flush_interval: float = 5.0,
    ):
        super().__init__(app)
        self.influx_url = influx_url
        self.influx_token = influx_token
        self.influx_org = influx_org
        self.influx_bucket = influx_bucket
        self.max_queue_size = max_queue_size
        self.batch_size = batch_size
        self.flush_interval = flush_interval

        # Get hostname (priority: env var > parameter > container hostname > socket hostname)
        self.hostname = (
            hostname
            or os.getenv("HOSTNAME")
            or os.getenv("HOST_NAME")
            or socket.gethostname()
        )

        # Metrics queue for buffering
        self.metrics_queue = deque(maxlen=max_queue_size)
        self.queue_lock = asyncio.Lock()

        # InfluxDB client and write API
        self.client: Optional[InfluxDBClient] = None
        self.write_api = None
        self.is_connected = False

        # Background task for processing queue
        self.background_task: Optional[asyncio.Task] = None
        self.is_running = False
        self._worker_started = False

        # Initialize InfluxDB connection
        self._initialize_influx_client()

        logger.info(f"MetricsMiddleware initialized for host: {self.hostname}")
        logger.info(
            f"Queue settings: max_size={max_queue_size}, batch_size={batch_size}, flush_interval={flush_interval}s"
        )

    def _initialize_influx_client(self):
        """Initialize InfluxDB client with async write API"""
        try:
            self.client = InfluxDBClient(
                url=self.influx_url, token=self.influx_token, org=self.influx_org
            )

            # Define callbacks for async writes
            def success_callback(conf, data):
                logger.info(f"Successfully wrote batch to InfluxDB")

            def error_callback(conf, data, exception):
                logger.error(
                    f"Failed to write batch to InfluxDB: {exception}", exc_info=True
                )
                self.is_connected = False

            def retry_callback(conf, data, exception):
                logger.warning(f"Retrying write to InfluxDB: {exception}")

            # Use asynchronous write API with callbacks
            from influxdb_client.client.write_api import WriteOptions

            write_options = WriteOptions(
                batch_size=500,
                flush_interval=1_000,  # 1 second in ms
                jitter_interval=0,
                retry_interval=5_000,
                max_retries=3,
                max_retry_delay=30_000,
                exponential_base=2,
            )

            self.write_api = self.client.write_api(
                write_options=write_options,
                success_callback=success_callback,
                error_callback=error_callback,
                retry_callback=retry_callback,
            )

            # Test connection
            try:
                self.client.ping()
                self.is_connected = True
                logger.info(
                    "InfluxDB client initialized successfully (async mode with callbacks)"
                )
            except Exception as e:
                logger.warning(
                    f"InfluxDB connection test failed: {e}. Will queue metrics and retry."
                )
                self.is_connected = False

        except Exception as e:
            logger.error(f"Failed to initialize InfluxDB client: {e}")
            self.client = None
            self.write_api = None
            self.is_connected = False

    async def start_background_worker(self):
        """Start the background task for processing metrics queue"""
        if not self.is_running and not self._worker_started:
            self.is_running = True
            self._worker_started = True

            # Get or create event loop
            try:
                loop = asyncio.get_running_loop()
            except RuntimeError:
                loop = asyncio.get_event_loop()

            self.background_task = loop.create_task(self._process_queue_worker())
            logger.info("Background metrics worker started")

    async def stop_background_worker(self):
        """Stop the background worker gracefully"""
        self.is_running = False
        if self.background_task:
            self.background_task.cancel()
            try:
                await self.background_task
            except asyncio.CancelledError:
                pass
            logger.info("Background metrics worker stopped")

    async def _process_queue_worker(self):
        """Background worker that periodically flushes the metrics queue"""
        logger.info("Metrics queue worker running")

        while self.is_running:
            try:
                await asyncio.sleep(self.flush_interval)
                await self._flush_queue()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error in queue worker: {e}", exc_info=True)

        # Final flush on shutdown
        await self._flush_queue()
        logger.info("Metrics queue worker finished")

    async def _flush_queue(self):
        """Flush metrics from queue to InfluxDB"""
        if not self.metrics_queue:
            return

        # Check/restore connection if needed
        if not self.is_connected:
            self._reconnect()

        if not self.is_connected or not self.write_api:
            queue_size = len(self.metrics_queue)
            if queue_size > 0:
                logger.warning(
                    f"InfluxDB unavailable. {queue_size} metrics queued (max: {self.max_queue_size})"
                )
            return

        async with self.queue_lock:
            points_to_write = []
            batch_count = min(len(self.metrics_queue), self.batch_size)

            for _ in range(batch_count):
                if self.metrics_queue:
                    points_to_write.append(self.metrics_queue.popleft())

            if points_to_write:
                try:
                    # Log what we're about to write
                    logger.info(
                        f"Writing {len(points_to_write)} metrics to bucket '{self.influx_bucket}' in org '{self.influx_org}'"
                    )
                    logger.debug(
                        f"First point: {points_to_write[0].to_line_protocol()}"
                    )

                    # Async write to InfluxDB
                    self.write_api.write(
                        bucket=self.influx_bucket,
                        org=self.influx_org,
                        record=points_to_write,
                    )
                    logger.info(
                        f"Submitted {len(points_to_write)} metrics to InfluxDB (async)"
                    )

                except Exception as e:
                    logger.error(
                        f"Failed to write metrics batch to InfluxDB: {e}", exc_info=True
                    )
                    # Put metrics back in queue
                    for point in reversed(points_to_write):
                        self.metrics_queue.appendleft(point)
                    self.is_connected = False

    def _reconnect(self):
        """Attempt to reconnect to InfluxDB"""
        try:
            if self.client:
                self.client.ping()
                self.is_connected = True
                logger.info("Reconnected to InfluxDB")
        except Exception as e:
            logger.debug(f"InfluxDB still unavailable: {e}")
            self.is_connected = False

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Ensure background worker is running (start on first request if needed)
        if not self._worker_started:
            await self.start_background_worker()

        # Start timing
        start_time = time.time()

        # Process the request
        response = await call_next(request)

        # Calculate duration
        duration = time.time() - start_time

        # Queue metrics (non-blocking)
        await self._queue_metric(request, response, duration)

        return response

    async def _queue_metric(
        self, request: Request, response: Response, duration: float
    ):
        """Add metric to internal queue"""
        try:
            # Extract route info
            route = request.url.path
            method = request.method
            status_code = response.status_code

            # Create InfluxDB point with hostname tag
            point = (
                Point("api_request")
                .tag("hostname", self.hostname)
                .tag("method", method)
                .tag("route", route)
                .tag("status_code", status_code)
                .tag("status_class", f"{status_code // 100}xx")
                .field("duration", duration)
                .field("count", 1)
            )

            # Add to queue
            async with self.queue_lock:
                self.metrics_queue.append(point)

                # Log info about queueing
                queue_size = len(self.metrics_queue)
                logger.debug(
                    f"Queued metric for {method} {route} (queue size: {queue_size})"
                )

                # Log warning if queue is getting full
                if queue_size > self.max_queue_size * 0.8:
                    logger.warning(
                        f"Metrics queue is {(queue_size / self.max_queue_size) * 100:.1f}% full ({queue_size}/{self.max_queue_size})"
                    )

        except Exception as e:
            logger.error(f"Failed to queue metric: {e}", exc_info=True)

    async def shutdown(self):
        """Graceful shutdown - flush remaining metrics"""
        logger.info("Shutting down metrics middleware...")
        await self.stop_background_worker()

        if self.client:
            self.client.close()
            logger.info("InfluxDB client closed")
