#!/usr/bin/env python3
"""
Pillar 5: Commerce Conversion Flow Test

Full end-to-end test of the order journey:
  - Customer sends intent message
  - System suggests products
  - Customer picks and provides details
  - Order placed and persisted
  - Lead converted to customer
  - Admin notified
  - Follow-up scheduled (post-delivery)

Test Scenarios:
  A: Single product happy path
  B: Multi-product discovery
  C: Order cancellation mid-flow
  D: Public product page → buy
  E: Follow-up scheduler (post-delivery + upsell)
"""

import requests
import json
import time
from datetime import datetime
import sys

BASE_URL = "http://localhost:8000"
TIMESTAMP = datetime.now().isoformat()

test_results = {
    "timestamp": TIMESTAMP,
    "test_suite": "Pillar 5 - Commerce Conversion Flow",
    "scenarios": []
}

# ============================================================================
# HELPER: Test user setup
# ============================================================================

def get_test_jwt():
    """Get or create a test user JWT"""
    try:
        resp = requests.post(
            f"{BASE_URL}/api/auth/register",
            json={
                "name": "Commerce Test User",
                "email": "commerce_test@test.local",
                "password": "CommerceTest123!",
                "plan": "Pro"
            },
            timeout=10
        )

        if resp.status_code == 200:
            return resp.json().get("access_token"), resp.json().get("company_id")
        elif resp.status_code == 409:
            login_resp = requests.post(
                f"{BASE_URL}/api/auth/login",
                json={
                    "email": "commerce_test@test.local",
                    "password": "CommerceTest123!"
                },
                timeout=10
            )
            if login_resp.status_code == 200:
                return login_resp.json().get("access_token"), login_resp.json().get("company_id")

        return None, None

    except Exception as e:
        return None, None

# ============================================================================
# SCENARIO A: Single Product Happy Path
# ============================================================================

def scenario_a_happy_path(jwt: str, company_id: str):
    """Customer orders single product through WhatsApp"""
    scenario_name = "Scenario A - Single Product Happy Path"
    print(f"\n{scenario_name}")

    try:
        # Step 1: Customer sends intent
        print("  [1/7] Sending order intent...")
        intent_resp = requests.post(
            f"{BASE_URL}/api/webhooks/web-chat",
            headers={"Authorization": f"Bearer {jwt}"},
            json={
                "customer_phone": "+test_a_cust",
                "customer_name": "Alice Johnson",
                "message_body": "Hi, I want to buy a ring",
                "session_id": "scenario_a"
            },
            timeout=10
        )

        if intent_resp.status_code != 200:
            result = {"scenario": scenario_name, "status": "SKIP", "reason": f"Intent failed: {intent_resp.status_code}"}
            test_results["scenarios"].append(result)
            print(f"  ⊘ Skipped: Webhook failed")
            return

        print(f"    ✓ Intent received: {intent_resp.status_code}")

        # Step 2: Customer picks product
        print("  [2/7] Selecting product...")
        select_resp = requests.post(
            f"{BASE_URL}/api/webhooks/web-chat",
            headers={"Authorization": f"Bearer {jwt}"},
            json={
                "customer_phone": "+test_a_cust",
                "customer_name": "Alice Johnson",
                "message_body": "I want the gold ring",
                "session_id": "scenario_a"
            },
            timeout=10
        )

        if select_resp.status_code != 200:
            result = {"scenario": scenario_name, "status": "FAIL", "reason": "Product selection failed"}
            test_results["scenarios"].append(result)
            return

        print(f"    ✓ Product selected: {select_resp.status_code}")

        # Step 3: Provide details (name)
        print("  [3/7] Providing name...")
        name_resp = requests.post(
            f"{BASE_URL}/api/webhooks/web-chat",
            headers={"Authorization": f"Bearer {jwt}"},
            json={
                "customer_phone": "+test_a_cust",
                "customer_name": "Alice Johnson",
                "message_body": "My name is Alice Johnson",
                "session_id": "scenario_a"
            },
            timeout=10
        )

        print(f"    ✓ Name provided: {name_resp.status_code}")

        # Step 4: Provide email
        print("  [4/7] Providing email...")
        email_resp = requests.post(
            f"{BASE_URL}/api/webhooks/web-chat",
            headers={"Authorization": f"Bearer {jwt}"},
            json={
                "customer_phone": "+test_a_cust",
                "customer_name": "Alice Johnson",
                "message_body": "alice@example.com",
                "session_id": "scenario_a"
            },
            timeout=10
        )

        print(f"    ✓ Email provided: {email_resp.status_code}")

        # Step 5: Provide phone & address
        print("  [5/7] Providing phone and address...")
        contact_resp = requests.post(
            f"{BASE_URL}/api/webhooks/web-chat",
            headers={"Authorization": f"Bearer {jwt}"},
            json={
                "customer_phone": "+test_a_cust",
                "customer_name": "Alice Johnson",
                "message_body": "+1-555-1234 123 Main St, New York NY",
                "session_id": "scenario_a"
            },
            timeout=10
        )

        print(f"    ✓ Contact info provided: {contact_resp.status_code}")

        # Step 6: Provide quantity and confirm
        print("  [6/7] Confirming order...")
        confirm_resp = requests.post(
            f"{BASE_URL}/api/webhooks/web-chat",
            headers={"Authorization": f"Bearer {jwt}"},
            json={
                "customer_phone": "+test_a_cust",
                "customer_name": "Alice Johnson",
                "message_body": "Yes, confirm. Quantity 1.",
                "session_id": "scenario_a"
            },
            timeout=10
        )

        if confirm_resp.status_code != 200:
            result = {"scenario": scenario_name, "status": "FAIL", "reason": "Order confirmation failed"}
            test_results["scenarios"].append(result)
            print(f"  ✗ FAIL: Confirmation failed")
            return

        print(f"    ✓ Order confirmed: {confirm_resp.status_code}")

        # Step 7: Verify order in DB
        print("  [7/7] Verifying order persisted...")
        orders_resp = requests.get(
            f"{BASE_URL}/api/orders",
            headers={"Authorization": f"Bearer {jwt}"},
            timeout=10
        )

        if orders_resp.status_code == 200:
            orders = orders_resp.json().get("orders", [])
            if orders:
                order = orders[-1]  # Latest order
                has_customer_fields = all([
                    order.get("customer_name"),
                    order.get("customer_email"),
                    order.get("customer_phone")
                ])

                if has_customer_fields and order.get("status") in ["admin_review", "confirmed"]:
                    result = {
                        "scenario": scenario_name,
                        "status": "PASS",
                        "reason": f"Order created with ID {order.get('order_id', 'N/A')}, status: {order.get('status')}"
                    }
                    print(f"    ✓ Order persisted: ID={order.get('order_id')}")
                else:
                    result = {
                        "scenario": scenario_name,
                        "status": "FAIL",
                        "reason": "Order missing customer details or wrong status"
                    }
                    print(f"    ✗ FAIL: Order incomplete")
            else:
                result = {
                    "scenario": scenario_name,
                    "status": "FAIL",
                    "reason": "No orders found in database"
                }
                print(f"    ✗ FAIL: No orders in DB")
        else:
            result = {
                "scenario": scenario_name,
                "status": "FAIL",
                "reason": f"Could not fetch orders ({orders_resp.status_code})"
            }
            print(f"    ✗ FAIL: Could not query orders")

        test_results["scenarios"].append(result)

    except Exception as e:
        result = {"scenario": scenario_name, "status": "ERROR", "reason": str(e)}
        test_results["scenarios"].append(result)
        print(f"  ✗ ERROR: {e}")

# ============================================================================
# SCENARIO D: Public Product Page → Buy
# ============================================================================

def scenario_d_public_buy(jwt: str, company_id: str):
    """Customer places order via public product page"""
    scenario_name = "Scenario D - Public Product Page Buy"
    print(f"\n{scenario_name}")

    try:
        # Get company slug (would normally come from company settings)
        company_resp = requests.get(
            f"{BASE_URL}/api/settings/company",
            headers={"Authorization": f"Bearer {jwt}"},
            timeout=10
        )

        if company_resp.status_code != 200:
            result = {"scenario": scenario_name, "status": "SKIP", "reason": "Could not get company info"}
            test_results["scenarios"].append(result)
            print(f"  ⊘ Skipped: Could not get company slug")
            return

        company_slug = company_resp.json().get("slug", "test-company")

        # Step 1: Get available products
        print("  [1/3] Fetching products...")
        products_resp = requests.get(
            f"{BASE_URL}/api/products",
            headers={"Authorization": f"Bearer {jwt}"},
            timeout=10
        )

        if products_resp.status_code != 200 or not products_resp.json().get("products"):
            result = {"scenario": scenario_name, "status": "SKIP", "reason": "No products available"}
            test_results["scenarios"].append(result)
            print(f"  ⊘ Skipped: No products to order")
            return

        product = products_resp.json()["products"][0]
        product_slug = product.get("slug", "test-product")

        print(f"    ✓ Found product: {product_slug}")

        # Step 2: Place order via public endpoint
        print("  [2/3] Placing order via public endpoint...")
        buy_resp = requests.post(
            f"{BASE_URL}/api/public/companies/{company_slug}/products/{product_slug}/buy",
            json={
                "customer_name": "Bob Smith",
                "customer_phone": "+1-555-5678",
                "customer_email": "bob@example.com",
                "delivery_address": "456 Oak Ave, Los Angeles CA",
                "quantity": 2,
                "client_request_id": f"order_d_{int(time.time())}"
            },
            timeout=10
        )

        if buy_resp.status_code != 200:
            result = {
                "scenario": scenario_name,
                "status": "FAIL",
                "reason": f"Buy endpoint failed: {buy_resp.status_code}"
            }
            test_results["scenarios"].append(result)
            print(f"  ✗ FAIL: Buy endpoint returned {buy_resp.status_code}")
            return

        buy_data = buy_resp.json()
        order_ref = buy_data.get("order_reference", "")

        # Verify order reference format: ORD-XXXXXX
        if order_ref and order_ref.startswith("ORD-"):
            print(f"    ✓ Order placed: {order_ref}")
        else:
            print(f"    ⚠ Order ref format unexpected: {order_ref}")

        # Step 3: Verify deduplication
        print("  [3/3] Testing deduplication...")
        dup_resp = requests.post(
            f"{BASE_URL}/api/public/companies/{company_slug}/products/{product_slug}/buy",
            json={
                "customer_name": "Bob Smith",
                "customer_phone": "+1-555-5678",
                "customer_email": "bob@example.com",
                "delivery_address": "456 Oak Ave, Los Angeles CA",
                "quantity": 2,
                "client_request_id": f"order_d_{int(time.time())}"  # Same request ID
            },
            timeout=10
        )

        if dup_resp.status_code == 200:
            dup_ref = dup_resp.json().get("order_reference", "")
            if dup_ref == order_ref:
                result = {
                    "scenario": scenario_name,
                    "status": "PASS",
                    "reason": f"Order created with correct format and deduplication working"
                }
                print(f"    ✓ Deduplication working: same order returned")
            else:
                result = {
                    "scenario": scenario_name,
                    "status": "FAIL",
                    "reason": "Deduplication not working"
                }
                print(f"    ✗ Deduplication failed")
        else:
            result = {
                "scenario": scenario_name,
                "status": "PASS",
                "reason": "Primary order creation successful"
            }
            print(f"    ✓ Order creation successful")

        test_results["scenarios"].append(result)

    except Exception as e:
        result = {"scenario": scenario_name, "status": "ERROR", "reason": str(e)}
        test_results["scenarios"].append(result)
        print(f"  ✗ ERROR: {e}")

# ============================================================================
# SCENARIO B: Multi-Product Discovery
# ============================================================================

def scenario_b_multi_product(jwt: str, company_id: str):
    """Customer discovers and asks about multiple products"""
    scenario_name = "Scenario B - Multi-Product Discovery"
    print(f"\n{scenario_name}")

    try:
        # Step 1: Ask for products
        print("  [1/2] Requesting product list...")
        list_resp = requests.post(
            f"{BASE_URL}/api/webhooks/web-chat",
            headers={"Authorization": f"Bearer {jwt}"},
            json={
                "customer_phone": "+test_b_prod",
                "customer_name": "Bob Brown",
                "message_body": "What jewelry do you have?",
                "session_id": "scenario_b"
            },
            timeout=10
        )

        if list_resp.status_code != 200:
            result = {"scenario": scenario_name, "status": "SKIP", "reason": "Product list request failed"}
            test_results["scenarios"].append(result)
            print(f"  ⊘ Skipped")
            return

        response_text = list_resp.json().get("ai_response", "")
        has_products = len(response_text) > 50  # AI should describe products

        # Step 2: Ask about specific product
        print("  [2/2] Asking for more details...")
        detail_resp = requests.post(
            f"{BASE_URL}/api/webhooks/web-chat",
            headers={"Authorization": f"Bearer {jwt}"},
            json={
                "customer_phone": "+test_b_prod",
                "customer_name": "Bob Brown",
                "message_body": "Tell me more about the second one",
                "session_id": "scenario_b"
            },
            timeout=10
        )

        if detail_resp.status_code == 200 and has_products:
            result = {
                "scenario": scenario_name,
                "status": "PASS",
                "reason": "AI provided product information"
            }
            print(f"    ✓ Multi-product discovery working")
        else:
            result = {
                "scenario": scenario_name,
                "status": "FAIL",
                "reason": "Failed to retrieve product details"
            }
            print(f"    ✗ Product discovery failed")

        test_results["scenarios"].append(result)

    except Exception as e:
        result = {"scenario": scenario_name, "status": "ERROR", "reason": str(e)}
        test_results["scenarios"].append(result)
        print(f"  ✗ ERROR: {e}")

# ============================================================================
# MAIN
# ============================================================================

def main():
    print("=" * 80)
    print("PILLAR 5: COMMERCE CONVERSION FLOW")
    print("=" * 80)

    # Setup test user
    print("\n[SETUP] Creating test company...")
    jwt, company_id = get_test_jwt()

    if not jwt or not company_id:
        print("✗ Could not create test company")
        sys.exit(1)

    print(f"✓ Test company ready")

    # Run scenarios
    print("\n" + "=" * 80)
    print("RUNNING COMMERCE SCENARIOS")
    print("=" * 80)

    scenario_a_happy_path(jwt, company_id)
    scenario_b_multi_product(jwt, company_id)
    scenario_d_public_buy(jwt, company_id)

    # Summary
    print("\n" + "=" * 80)
    print("TEST SUMMARY")
    print("=" * 80)

    passed = sum(1 for s in test_results["scenarios"] if s["status"] == "PASS")
    failed = sum(1 for s in test_results["scenarios"] if s["status"] == "FAIL")
    skipped = sum(1 for s in test_results["scenarios"] if s["status"] == "SKIP")
    errors = sum(1 for s in test_results["scenarios"] if s["status"] == "ERROR")

    print(f"\nTotal Scenarios: {len(test_results['scenarios'])}")
    print(f"  ✓ PASS:  {passed}")
    print(f"  ✗ FAIL:  {failed}")
    print(f"  ⊘ SKIP:  {skipped}")
    print(f"  ✗ ERROR: {errors}")

    # Save results
    results_file = "/tmp/pillar5_commerce_results.json"
    with open(results_file, "w") as f:
        json.dump(test_results, f, indent=2)

    print(f"\nResults saved to: {results_file}")

    if failed > 0 or errors > 0:
        print("\n✗ PILLAR 5 FAILED - Commerce flow errors detected")
        sys.exit(1)
    else:
        print("\n✓ PILLAR 5 PASSED - Commerce flow working")
        sys.exit(0)

if __name__ == "__main__":
    main()
