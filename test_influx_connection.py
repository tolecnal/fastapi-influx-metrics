#!/usr/bin/env python3
"""
Test InfluxDB connection and write a simple metric
"""

import os
from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS
from datetime import datetime

# Configuration
INFLUX_URL = os.getenv("INFLUX_URL", "http://localhost:8086")
INFLUX_TOKEN = os.getenv("INFLUX_TOKEN", "my-super-secret-token")
INFLUX_ORG = os.getenv("INFLUX_ORG", "my-org")
INFLUX_BUCKET = os.getenv("INFLUX_BUCKET", "api-metrics")

print("Testing InfluxDB Connection")
print("=" * 50)
print(f"URL: {INFLUX_URL}")
print(f"Org: {INFLUX_ORG}")
print(f"Bucket: {INFLUX_BUCKET}")
print(f"Token: {INFLUX_TOKEN[:10]}..." if INFLUX_TOKEN else "Token: None")
print("=" * 50)

try:
    # Create client
    print("\n1. Creating InfluxDB client...")
    client = InfluxDBClient(url=INFLUX_URL, token=INFLUX_TOKEN, org=INFLUX_ORG)
    print("✓ Client created")

    # Test ping
    print("\n2. Testing connection (ping)...")
    result = client.ping()
    print(f"✓ Ping successful: {result}")

    # List buckets
    print("\n3. Listing buckets...")
    buckets_api = client.buckets_api()
    buckets = buckets_api.find_buckets().buckets
    print(f"✓ Found {len(buckets)} buckets:")
    for bucket in buckets:
        print(f"  - {bucket.name} (org: {bucket.org_id})")

    # Check if our bucket exists
    target_bucket = None
    for bucket in buckets:
        if bucket.name == INFLUX_BUCKET:
            target_bucket = bucket
            print(f"\n✓ Target bucket '{INFLUX_BUCKET}' found!")
            break

    if not target_bucket:
        print(f"\n✗ ERROR: Bucket '{INFLUX_BUCKET}' not found!")
        print("Available buckets:", [b.name for b in buckets])
        exit(1)

    # Write a test point
    print("\n4. Writing test point...")
    write_api = client.write_api(write_options=SYNCHRONOUS)

    point = (
        Point("test_metric")
        .tag("source", "connection_test")
        .tag("hostname", "test-host")
        .field("value", 42)
        .field("count", 1)
        .time(datetime.utcnow())
    )

    print(f"Point line protocol: {point.to_line_protocol()}")

    write_api.write(bucket=INFLUX_BUCKET, org=INFLUX_ORG, record=point)
    print("✓ Test point written successfully!")

    # Query the data back
    print("\n5. Querying data back...")
    query_api = client.query_api()

    query = f'''
    from(bucket: "{INFLUX_BUCKET}")
      |> range(start: -1h)
      |> filter(fn: (r) => r._measurement == "test_metric")
      |> filter(fn: (r) => r.source == "connection_test")
    '''

    result = query_api.query(query=query, org=INFLUX_ORG)

    if result:
        print("✓ Query successful! Found data:")
        for table in result:
            for record in table.records:
                print(
                    f"  {record.get_measurement()}: {record.get_field()}={record.get_value()}"
                )
    else:
        print("✗ No data returned from query (but write succeeded)")

    # Try querying api_request measurement
    print("\n6. Checking for api_request data...")
    query2 = f'''
    from(bucket: "{INFLUX_BUCKET}")
      |> range(start: -24h)
      |> filter(fn: (r) => r._measurement == "api_request")
      |> limit(n: 5)
    '''

    result2 = query_api.query(query=query2, org=INFLUX_ORG)

    if result2:
        print("✓ Found api_request data:")
        count = 0
        for table in result2:
            for record in table.records:
                count += 1
                print(
                    f"  {record.get_time()}: {record.get_field()}={record.get_value()} (hostname={record.values.get('hostname', 'N/A')})"
                )
        print(f"Total records found: {count}")
    else:
        print("✗ No api_request data found in the last 24 hours")

    client.close()
    print("\n" + "=" * 50)
    print("✓ All tests passed!")
    print("=" * 50)

except Exception as e:
    print(f"\n✗ ERROR: {e}")
    import traceback

    traceback.print_exc()
    exit(1)
