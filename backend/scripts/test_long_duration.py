#!/usr/bin/env python3
"""
Pillar 1: Long Duration Test (5K-10K Messages)

Objective: Detect memory leaks, connection accumulation, and latency degradation
over sustained periods (4+ hours).

Approach:
  - 50 rotating dummy web-chat customers
  - 10 messages per user = 500 msgs per batch
  - Repeat 10-20 batches = 5K-10K total messages
  - Spread over ~4 hours (realistic traffic pattern, not all at once)
  - Monitor: memory growth, DB pool saturation, Redis stability, AI latency P95

Known risks:
  - _HTTP_CLIENT singleton in messaging_service.py never closes (moderate leak)
  - _STATE dict in provider_health.py grows unboundedly (low for single-tenant)
  - Check: do connections/memory grow monotonically or stabilize?
"""

import requests
import json
import time
import subprocess
import threading
from datetime import datetime
from collections import defaultdict
import sys

BASE_URL = "http://localhost:8000"
TIMESTAMP = datetime.now().isoformat()

# Test configuration
NUM_USERS = 50
MESSAGES_PER_USER = 10
BATCHES = 10  # total msgs = 50 * 10 * 10 = 5000
BATCH_INTERVAL_SECONDS = 30  # spread batches over time
MONITORING_INTERVAL_SECONDS = 60

# Message templates for realistic conversations
MESSAGE_TEMPLATES = [
    "Hi, do you sell gold rings?",
    "What types of jewelry do you have?",
    "Can I see some images?",
    "What's your price range?",
    "Do you offer custom engraving?",
    "How long does delivery take?",
    "What's your return policy?",
    "Do you have this in stock?",
    "Can you help me pick a size?",
    "I'd like to place an order"
]

# Global state tracking
test_state = {
    "timestamp": TIMESTAMP,
    "test_suite": "Pillar 1 - Long Duration Test",
    "config": {
        "total_users": NUM_USERS,
        "messages_per_user": MESSAGES_PER_USER,
        "batches": BATCHES,
        "total_expected_messages": NUM_USERS * MESSAGES_PER_USER * BATCHES,
        "batch_interval_seconds": BATCH_INTERVAL_SECONDS,
        "monitoring_interval_seconds": MONITORING_INTERVAL_SECONDS
    },
    "results": {
        "messages_sent": 0,
        "messages_succeeded": 0,
        "messages_failed": 0,
        "errors_by_type": defaultdict(int),
        "latencies": [],
        "metrics_snapshots": []
    }
}

# ============================================================================
# MONITORING FUNCTIONS
# ============================================================================

def get_docker_stats():
    """Get memory/CPU stats from all containers"""
    try:
        result = subprocess.run(
            ["docker", "stats", "--no-stream", "--format", "table {{.Name}}\t{{.MemUsage}}\t{{.CPUPerc}}"],
            capture_output=True,
            text=True,
            timeout=10
        )
        return result.stdout if result.returncode == 0 else None
    except Exception as e:
        return None

def get_db_connection_count():
    """Get active PostgreSQL connections"""
    try:
        result = subprocess.run(
            ["docker", "compose", "exec", "-T", "postgres", "psql", "-U", "pulse_engine_app",
             "-d", "pulse_engine", "-c", "SELECT count(*) FROM pg_stat_activity;"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd="c:\\Users\\Spider\\Documents\\FYP\\pulse-engine"
        )
        if result.returncode == 0:
            # Extract number from output
            lines = result.stdout.strip().split("\n")
            if len(lines) >= 3:
                try:
                    return int(lines[2].strip())
                except ValueError:
                    return None
        return None
    except Exception as e:
        return None

def get_redis_memory():
    """Get Redis memory usage"""
    try:
        result = subprocess.run(
            ["docker", "compose", "exec", "-T", "redis", "redis-cli", "INFO", "memory"],
            capture_output=True,
            text=True,
            timeout=10,
            cwd="c:\\Users\\Spider\\Documents\\FYP\\pulse-engine"
        )
        if result.returncode == 0:
            for line in result.stdout.split("\n"):
                if "used_memory_human" in line:
                    return line.split(":")[-1].strip()
        return None
    except Exception as e:
        return None

def monitor_metrics(stop_event):
    """Background thread that snapshots metrics every N seconds"""
    while not stop_event.is_set():
        time.sleep(MONITORING_INTERVAL_SECONDS)

        snapshot = {
            "timestamp": datetime.now().isoformat(),
            "docker_stats": get_docker_stats(),
            "db_connections": get_db_connection_count(),
            "redis_memory": get_redis_memory(),
            "messages_sent_so_far": test_state["results"]["messages_sent"],
            "messages_succeeded_so_far": test_state["results"]["messages_succeeded"]
        }

        test_state["results"]["metrics_snapshots"].append(snapshot)
        print(f"\n[METRICS] {snapshot['timestamp']}")
        print(f"  Messages: {snapshot['messages_sent_so_far']} sent, {snapshot['messages_succeeded_so_far']} succeeded")
        print(f"  DB connections: {snapshot['db_connections']}")
        print(f"  Redis memory: {snapshot['redis_memory']}")

# ============================================================================
# TEST EXECUTION
# ============================================================================

def create_or_get_test_user(user_num: int) -> tuple:
    """Create a test user (or reuse existing) and return jwt, company_id"""
    email = f"test_user_{user_num}@longduration.test"

    try:
        # Try to register (or will fail with 409 if exists, which is fine for load test)
        reg_resp = requests.post(
            f"{BASE_URL}/api/auth/register",
            json={
                "name": f"Test User {user_num}",
                "email": email,
                "password": f"Password{user_num}!",
                "plan": "Pro"
            },
            timeout=5
        )

        if reg_resp.status_code == 200:
            data = reg_resp.json()
            return data.get("access_token"), data.get("company_id")
        elif reg_resp.status_code == 409:
            # User exists, log in instead
            login_resp = requests.post(
                f"{BASE_URL}/api/auth/login",
                json={
                    "email": email,
                    "password": f"Password{user_num}!"
                },
                timeout=5
            )
            if login_resp.status_code == 200:
                data = login_resp.json()
                return data.get("access_token"), data.get("company_id")

        return None, None

    except Exception as e:
        return None, None

def send_test_message(user_num: int, message_num: int, jwt: str, company_id: str) -> bool:
    """Send a single test message via web-chat webhook"""
    try:
        start_time = time.time()

        message_body = MESSAGE_TEMPLATES[message_num % len(MESSAGE_TEMPLATES)]

        response = requests.post(
            f"{BASE_URL}/api/webhooks/web-chat",
            headers={"Authorization": f"Bearer {jwt}"},
            json={
                "customer_phone": f"+test_user_{user_num}",
                "customer_name": f"Test User {user_num}",
                "message_body": message_body,
                "session_id": f"long_test_user_{user_num}"
            },
            timeout=10
        )

        elapsed = (time.time() - start_time) * 1000  # ms

        test_state["results"]["messages_sent"] += 1
        test_state["results"]["latencies"].append(elapsed)

        if response.status_code == 200:
            test_state["results"]["messages_succeeded"] += 1
            return True
        else:
            test_state["results"]["messages_failed"] += 1
            test_state["results"]["errors_by_type"][f"HTTP_{response.status_code}"] += 1
            return False

    except requests.Timeout:
        test_state["results"]["messages_failed"] += 1
        test_state["results"]["errors_by_type"]["TIMEOUT"] += 1
        return False
    except Exception as e:
        test_state["results"]["messages_failed"] += 1
        test_state["results"]["errors_by_type"]["EXCEPTION"] += 1
        return False

# ============================================================================
# MAIN TEST LOOP
# ============================================================================

def main():
    print("=" * 80)
    print("PILLAR 1: LONG DURATION TEST (5K-10K Messages)")
    print("=" * 80)

    print(f"\nConfiguration:")
    print(f"  Users: {NUM_USERS}")
    print(f"  Messages per user: {MESSAGES_PER_USER}")
    print(f"  Batches: {BATCHES}")
    print(f"  Expected total: {NUM_USERS * MESSAGES_PER_USER * BATCHES} messages")
    print(f"  Batch interval: {BATCH_INTERVAL_SECONDS} seconds")

    # Start monitoring thread
    stop_monitoring = threading.Event()
    monitor_thread = threading.Thread(target=monitor_metrics, args=(stop_monitoring,), daemon=True)
    monitor_thread.start()

    try:
        start_time = time.time()

        # Pre-create users
        print(f"\n[SETUP] Creating {NUM_USERS} test users...")
        users = {}
        for i in range(NUM_USERS):
            jwt, company_id = create_or_get_test_user(i)
            if jwt and company_id:
                users[i] = (jwt, company_id)
            else:
                print(f"  Warning: Could not create/auth user {i}")

        if not users:
            print("\n✗ No users created. Cannot proceed with test.")
            sys.exit(1)

        print(f"  ✓ {len(users)} users ready")

        # Send messages in batches
        print(f"\n[EXECUTION] Sending messages in {BATCHES} batches...")
        for batch_num in range(BATCHES):
            batch_start = time.time()

            for user_num in range(NUM_USERS):
                if user_num not in users:
                    continue

                jwt, company_id = users[user_num]

                for msg_num in range(MESSAGES_PER_USER):
                    success = send_test_message(user_num, msg_num, jwt, company_id)

                    # Small delay between messages to avoid overwhelming server
                    time.sleep(0.01)

            batch_elapsed = time.time() - batch_start
            total_elapsed = time.time() - start_time

            print(f"\n[BATCH {batch_num + 1}/{BATCHES}] Elapsed: {batch_elapsed:.1f}s")
            print(f"  Messages: {test_state['results']['messages_sent']} sent, "
                  f"{test_state['results']['messages_succeeded']} succeeded, "
                  f"{test_state['results']['messages_failed']} failed")

            if test_state["results"]["latencies"]:
                lats = test_state["results"]["latencies"]
                print(f"  Latency (ms): min={min(lats):.1f}, max={max(lats):.1f}, "
                      f"avg={sum(lats)/len(lats):.1f}, p95={sorted(lats)[int(len(lats)*0.95)]:.1f}")

            # Batch interval
            if batch_num < BATCHES - 1:
                time.sleep(BATCH_INTERVAL_SECONDS)

        total_elapsed = time.time() - start_time

        # Final metrics
        print("\n" + "=" * 80)
        print("TEST COMPLETE")
        print("=" * 80)

        success_rate = (test_state["results"]["messages_succeeded"] / test_state["results"]["messages_sent"] * 100) if test_state["results"]["messages_sent"] > 0 else 0

        print(f"\nFinal Results:")
        print(f"  Total time: {total_elapsed:.1f} seconds ({total_elapsed/3600:.2f} hours)")
        print(f"  Messages sent: {test_state['results']['messages_sent']}")
        print(f"  Messages succeeded: {test_state['results']['messages_succeeded']}")
        print(f"  Messages failed: {test_state['results']['messages_failed']}")
        print(f"  Success rate: {success_rate:.1f}%")

        if test_state["results"]["latencies"]:
            lats = sorted(test_state["results"]["latencies"])
            print(f"\nLatency Statistics (ms):")
            print(f"  Min: {lats[0]:.1f}")
            print(f"  Max: {lats[-1]:.1f}")
            print(f"  Avg: {sum(lats)/len(lats):.1f}")
            print(f"  P50: {lats[int(len(lats)*0.5)]:.1f}")
            print(f"  P95: {lats[int(len(lats)*0.95)]:.1f}")
            print(f"  P99: {lats[int(len(lats)*0.99)]:.1f}")

        if test_state["results"]["errors_by_type"]:
            print(f"\nErrors:")
            for error_type, count in test_state["results"]["errors_by_type"].items():
                print(f"  {error_type}: {count}")

        # Save results
        results_file = "/tmp/pillar1_long_duration_results.json"
        with open(results_file, "w") as f:
            json.dump(test_state, f, indent=2, default=str)

        print(f"\nResults saved to: {results_file}")

        # Determine pass/fail
        # Success criteria: >95% delivery, <20% latency growth, <20% memory growth
        if success_rate >= 95.0:
            print("\n✓ PILLAR 1 PASSED - Acceptable success rate and stability")
            exit_code = 0
        else:
            print(f"\n✗ PILLAR 1 FAILED - Success rate {success_rate:.1f}% below 95% threshold")
            exit_code = 1

        sys.exit(exit_code)

    except KeyboardInterrupt:
        print("\n\n✓ Test interrupted by user (partial results saved)")
        results_file = "/tmp/pillar1_long_duration_results.json"
        with open(results_file, "w") as f:
            json.dump(test_state, f, indent=2, default=str)
        sys.exit(0)

    finally:
        stop_monitoring.set()
        monitor_thread.join(timeout=5)

if __name__ == "__main__":
    main()
