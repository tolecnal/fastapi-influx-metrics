#!/usr/bin/env python3
"""
Simple script to generate test traffic for the API
Run this after starting the docker-compose stack

Supports both single instance and multi-instance (HA) setups
"""

import requests
import time
import random
import sys

# Base URLs for different setups
SINGLE_INSTANCE = "http://localhost:8000"
LOAD_BALANCED = "http://localhost:8000"  # Through nginx
DIRECT_INSTANCES = [
    "http://localhost:8001",  # api-node-1
    "http://localhost:8002",  # api-node-2
    "http://localhost:8003",  # api-node-3
]


def test_multi_host():
    """Test that all hosts are responding"""
    print("Testing multi-host setup...")
    print("-" * 50)

    for i, url in enumerate(DIRECT_INSTANCES, 1):
        try:
            response = requests.get(f"{url}/health", timeout=2)
            data = response.json()
            hostname = data.get("hostname", "unknown")
            print(f"✓ Instance {i} ({url}): {hostname} - {response.status_code}")
        except requests.exceptions.RequestException as e:
            print(f"✗ Instance {i} ({url}): {e}")

    print("-" * 50)


def generate_traffic(duration_seconds=60, use_load_balancer=True, test_direct=False):
    """Generate random API traffic for testing"""

    endpoints = [
        ("GET", "/"),
        ("GET", "/health"),
        ("GET", "/api/users/1"),
        ("GET", "/api/users/42"),
        ("GET", "/api/users/123"),
        ("POST", "/api/users?name=TestUser"),
        ("GET", "/api/slow"),
        ("GET", "/api/error"),
        ("GET", "/metrics/status"),
    ]

    if use_load_balancer:
        base_url = LOAD_BALANCED
        print(f"Using load balancer at {base_url}")
    else:
        base_url = SINGLE_INSTANCE
        print(f"Using single instance at {base_url}")

    print(f"Generating traffic for {duration_seconds} seconds...")
    print("-" * 50)

    start_time = time.time()
    request_count = 0
    host_distribution = {}

    while time.time() - start_time < duration_seconds:
        # Pick target URL
        if test_direct and random.random() > 0.7:  # 30% chance to hit specific instance
            target_url = random.choice(DIRECT_INSTANCES)
        else:
            target_url = base_url

        # Pick a random endpoint
        method, path = random.choice(endpoints)

        try:
            if method == "GET":
                response = requests.get(f"{target_url}{path}", timeout=5)
            elif method == "POST":
                response = requests.post(f"{target_url}{path}", timeout=5)

            request_count += 1

            # Track which host responded
            try:
                data = response.json()
                hostname = data.get("hostname", "unknown")
                host_distribution[hostname] = host_distribution.get(hostname, 0) + 1
            except:
                hostname = "N/A"

            status = "✓" if response.status_code < 400 else "✗"
            print(
                f"{status} {method:4s} {path:25s} -> {response.status_code} ({response.elapsed.total_seconds():.3f}s) [{hostname}]"
            )

        except requests.exceptions.RequestException as e:
            print(f"✗ {method:4s} {path:25s} -> ERROR: {e}")

        # Random delay between requests (0.1 to 2 seconds)
        time.sleep(random.uniform(0.1, 2.0))

    print("-" * 50)
    print(f"Completed! Made {request_count} requests in {duration_seconds} seconds")
    print(f"Average: {request_count / duration_seconds:.2f} requests/second")

    if host_distribution:
        print("\nHost Distribution:")
        for hostname, count in sorted(host_distribution.items()):
            percentage = (count / request_count) * 100
            print(f"  {hostname}: {count} requests ({percentage:.1f}%)")


def check_metrics_queue():
    """Check the metrics queue status on all instances"""
    print("\nChecking metrics queue status...")
    print("-" * 50)

    for i, url in enumerate(DIRECT_INSTANCES, 1):
        try:
            response = requests.get(f"{url}/metrics/status", timeout=2)
            data = response.json()
            print(f"Instance {i} ({data.get('hostname', 'unknown')}):")
            print(
                f"  Queue: {data.get('queue_size', 'N/A')} / {data.get('max_queue_size', 'N/A')}"
            )
            print(f"  InfluxDB Connected: {data.get('influx_connected', 'N/A')}")
            print(f"  Worker Running: {data.get('worker_running', 'N/A')}")
        except requests.exceptions.RequestException as e:
            print(f"Instance {i}: Error - {e}")

    print("-" * 50)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Generate test traffic for FastAPI metrics"
    )
    parser.add_argument(
        "--duration", type=int, default=60, help="Duration in seconds (default: 60)"
    )
    parser.add_argument(
        "--no-lb", action="store_true", help="Don't use load balancer (single instance)"
    )
    parser.add_argument(
        "--test-direct",
        action="store_true",
        help="Also test direct instance connections",
    )
    parser.add_argument(
        "--test-hosts", action="store_true", help="Test multi-host setup and exit"
    )
    parser.add_argument(
        "--check-queues",
        action="store_true",
        help="Check metrics queue status and exit",
    )

    args = parser.parse_args()

    try:
        if args.test_hosts:
            test_multi_host()
        elif args.check_queues:
            check_metrics_queue()
        else:
            generate_traffic(
                duration_seconds=args.duration,
                use_load_balancer=not args.no_lb,
                test_direct=args.test_direct,
            )

            # Show queue status at the end
            check_metrics_queue()

    except KeyboardInterrupt:
        print("\n\nStopped by user")
        check_metrics_queue()
