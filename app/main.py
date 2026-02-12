import os
from fastapi import FastAPI
from app.middleware.metrics import MetricsMiddleware
import logging

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

# Get InfluxDB configuration from environment variables
INFLUX_URL = os.getenv("INFLUX_URL", "http://influxdb:8086")
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN", "my-super-secret-token")
INFLUX_ORG = os.getenv("INFLUX_ORG", "my-org")
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET", "api-metrics")

# Get hostname from environment (for multi-host tracking)
HOSTNAME = os.getenv("HOSTNAME") or os.getenv("HOST_NAME")

# Create FastAPI app
app = FastAPI(title="FastAPI with InfluxDB Metrics")

# Add metrics middleware
app.add_middleware(
    MetricsMiddleware,
    influx_url=INFLUX_URL,
    influx_token=INFLUX_TOKEN,
    influx_org=INFLUX_ORG,
    influx_bucket=INFLUX_BUCKET,
    hostname=HOSTNAME,
    max_queue_size=10000,  # Max metrics to buffer
    batch_size=100,  # Metrics per batch write
    flush_interval=5.0,  # Seconds between flushes
)


# Example routes
@app.get("/")
async def root():
    """Root endpoint"""
    return {"message": "Hello World", "hostname": HOSTNAME}


@app.get("/health")
async def health():
    """Health check endpoint"""
    return {"status": "healthy", "hostname": HOSTNAME}


@app.get("/metrics/status")
async def metrics_status():
    """Check metrics queue status"""
    # Try to find the middleware instance
    for middleware in app.user_middleware:
        if hasattr(middleware, "kwargs"):
            # Access the middleware instance
            mw_instance = getattr(middleware, "cls", None)
            if (
                mw_instance
                and hasattr(mw_instance, "__name__")
                and mw_instance.__name__ == "MetricsMiddleware"
            ):
                return {
                    "message": "Middleware found but can't access instance directly",
                    "hostname": HOSTNAME,
                }

    return {"message": "Check logs for queue status", "hostname": HOSTNAME}


@app.get("/api/users/{user_id}")
async def get_user(user_id: int):
    """Example endpoint with path parameter"""
    return {"user_id": user_id, "name": f"User {user_id}", "hostname": HOSTNAME}


@app.post("/api/users")
async def create_user(name: str):
    """Example POST endpoint"""
    return {"id": 123, "name": name, "hostname": HOSTNAME}


@app.get("/api/slow")
async def slow_endpoint():
    """Example slow endpoint to test metrics"""
    import asyncio

    await asyncio.sleep(2)
    return {"message": "This was slow", "hostname": HOSTNAME}


@app.get("/api/error")
async def error_endpoint():
    """Example endpoint that returns an error"""
    from fastapi import HTTPException

    raise HTTPException(status_code=500, detail="Something went wrong")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
