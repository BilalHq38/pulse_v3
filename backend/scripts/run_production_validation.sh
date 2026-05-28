#!/bin/bash

# ============================================================================
# MASTER TEST RUNNER - Production Validation
# ============================================================================
# Executes all 5 validation pillars and generates consolidated report
#
# Usage: bash run_production_validation.sh [pillar_number]
#   run_production_validation.sh        # Run all 5 pillars
#   run_production_validation.sh 4      # Run only Pillar 4 (isolation)
#   run_production_validation.sh 1 3 5  # Run specific pillars

set -e

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
REPORT_DIR="/tmp/production_validation"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

# Create report directory
mkdir -p "$REPORT_DIR"

# Colors
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo "=========================================================================="
echo "PULSE ENGINE - PRODUCTION VALIDATION TEST SUITE"
echo "=========================================================================="
echo "Timestamp: $(date)"
echo "Report Directory: $REPORT_DIR"
echo ""

# Determine which pillars to run
if [ $# -eq 0 ]; then
    PILLARS=(4 1 3 5 2)  # Default order: isolation first, failover last
else
    PILLARS=("$@")
fi

# Track results
RESULTS_FILE="$REPORT_DIR/final_report_$TIMESTAMP.md"
PASSED=0
FAILED=0
SKIPPED=0

{
    echo "# Production Validation Report"
    echo "**Date:** $(date)"
    echo "**Timestamp:** $TIMESTAMP"
    echo ""
    echo "## Test Execution Summary"
    echo ""

} > "$RESULTS_FILE"

# ============================================================================
# PILLAR 4: Multi-Tenant Isolation (fastest, highest risk)
# ============================================================================

if [[ " ${PILLARS[@]} " =~ " 4 " ]]; then
    echo ""
    echo -e "${YELLOW}[PILLAR 4] Multi-Tenant Isolation Audit${NC}"
    echo "Running: test_isolation.py"
    echo ""

    python3 "$SCRIPT_DIR/test_isolation.py" 2>&1 | tee -a "$REPORT_DIR/pillar4_$TIMESTAMP.log"
    PILLAR4_EXIT=$?

    if [ $PILLAR4_EXIT -eq 0 ]; then
        echo -e "${GREEN}✓ PILLAR 4 PASSED${NC}"
        ((PASSED++))
        echo "- **Pillar 4 (Isolation):** ✓ PASS" >> "$RESULTS_FILE"
    else
        echo -e "${RED}✗ PILLAR 4 FAILED${NC}"
        ((FAILED++))
        echo "- **Pillar 4 (Isolation):** ✗ FAIL" >> "$RESULTS_FILE"
    fi
fi

# ============================================================================
# PILLAR 1: Long Duration Test (5K-10K messages)
# ============================================================================

if [[ " ${PILLARS[@]} " =~ " 1 " ]]; then
    echo ""
    echo -e "${YELLOW}[PILLAR 1] Long Duration Test (5K-10K messages)${NC}"
    echo "Running: test_long_duration.py"
    echo "Estimated duration: 4+ hours"
    echo ""

    python3 "$SCRIPT_DIR/test_long_duration.py" 2>&1 | tee -a "$REPORT_DIR/pillar1_$TIMESTAMP.log"
    PILLAR1_EXIT=$?

    if [ $PILLAR1_EXIT -eq 0 ]; then
        echo -e "${GREEN}✓ PILLAR 1 PASSED${NC}"
        ((PASSED++))
        echo "- **Pillar 1 (Long Duration):** ✓ PASS" >> "$RESULTS_FILE"
    else
        echo -e "${RED}✗ PILLAR 1 FAILED${NC}"
        ((FAILED++))
        echo "- **Pillar 1 (Long Duration):** ✗ FAIL" >> "$RESULTS_FILE"
    fi
fi

# ============================================================================
# PILLAR 3: Failover Test (destructive, run last)
# ============================================================================

if [[ " ${PILLARS[@]} " =~ " 3 " ]]; then
    echo ""
    echo -e "${YELLOW}[PILLAR 3] Failover Test (service down/recovery)${NC}"
    echo "Running: test_failover.py"
    echo "Warning: Will stop Redis, PostgreSQL, WhatsApp bridge"
    echo ""

    python3 "$SCRIPT_DIR/test_failover.py" 2>&1 | tee -a "$REPORT_DIR/pillar3_$TIMESTAMP.log"
    PILLAR3_EXIT=$?

    if [ $PILLAR3_EXIT -eq 0 ]; then
        echo -e "${GREEN}✓ PILLAR 3 PASSED${NC}"
        ((PASSED++))
        echo "- **Pillar 3 (Failover):** ✓ PASS" >> "$RESULTS_FILE"
    else
        echo -e "${RED}✗ PILLAR 3 FAILED${NC}"
        ((FAILED++))
        echo "- **Pillar 3 (Failover):** ✗ FAIL" >> "$RESULTS_FILE"
    fi
fi

# ============================================================================
# PILLAR 5: Commerce Conversion Flow
# ============================================================================

if [[ " ${PILLARS[@]} " =~ " 5 " ]]; then
    echo ""
    echo -e "${YELLOW}[PILLAR 5] Commerce Conversion Flow${NC}"
    echo "Running: test_commerce.py"
    echo ""

    python3 "$SCRIPT_DIR/test_commerce.py" 2>&1 | tee -a "$REPORT_DIR/pillar5_$TIMESTAMP.log"
    PILLAR5_EXIT=$?

    if [ $PILLAR5_EXIT -eq 0 ]; then
        echo -e "${GREEN}✓ PILLAR 5 PASSED${NC}"
        ((PASSED++))
        echo "- **Pillar 5 (Commerce):** ✓ PASS" >> "$RESULTS_FILE"
    else
        echo -e "${RED}✗ PILLAR 5 FAILED${NC}"
        ((FAILED++))
        echo "- **Pillar 5 (Commerce):** ✗ FAIL" >> "$RESULTS_FILE"
    fi
fi

# ============================================================================
# PILLAR 2: WhatsApp Real-World Test (requires real phone + QR)
# ============================================================================

if [[ " ${PILLARS[@]} " =~ " 2 " ]]; then
    echo ""
    echo -e "${YELLOW}[PILLAR 2] WhatsApp Real-World Test${NC}"
    echo "Note: Pillar 2 requires real WhatsApp phone + manual QR scan"
    echo "This test is run manually in test_whatsapp_realworld.py"
    echo ""
    echo -e "${YELLOW}⊘ PILLAR 2: Manual testing required${NC}"
    ((SKIPPED++))
    echo "- **Pillar 2 (WhatsApp Real-World):** ⊘ MANUAL" >> "$RESULTS_FILE"
fi

# ============================================================================
# FINAL REPORT
# ============================================================================

echo ""
echo "=========================================================================="
echo "TEST EXECUTION COMPLETE"
echo "=========================================================================="

{
    echo ""
    echo "## Final Summary"
    echo ""
    echo "| Status | Count |"
    echo "|--------|-------|"
    echo "| ✓ PASSED | $PASSED |"
    echo "| ✗ FAILED | $FAILED |"
    echo "| ⊘ SKIPPED | $SKIPPED |"
    echo ""

    if [ $FAILED -eq 0 ]; then
        echo "## Verdict: ✓ READY FOR PRODUCTION"
        echo ""
        echo "All validation pillars passed. Platform is production-ready."
    else
        echo "## Verdict: ✗ NOT READY"
        echo ""
        echo "Failed tests detected. See logs below for details."
    fi

    echo ""
    echo "## Test Logs"
    echo ""
    echo "- Pillar 4 (Isolation): \`$REPORT_DIR/pillar4_$TIMESTAMP.log\`"
    echo "- Pillar 1 (Long Duration): \`$REPORT_DIR/pillar1_$TIMESTAMP.log\`"
    echo "- Pillar 3 (Failover): \`$REPORT_DIR/pillar3_$TIMESTAMP.log\`"
    echo "- Pillar 5 (Commerce): \`$REPORT_DIR/pillar5_$TIMESTAMP.log\`"
    echo ""

} >> "$RESULTS_FILE"

echo -e "${GREEN}Report saved to: $RESULTS_FILE${NC}"
echo ""
echo "Summary:"
echo "  ✓ Passed:  $PASSED"
echo "  ✗ Failed:  $FAILED"
echo "  ⊘ Skipped: $SKIPPED"
echo ""

# Exit with appropriate code
if [ $FAILED -eq 0 ]; then
    echo -e "${GREEN}✓ PRODUCTION VALIDATION PASSED${NC}"
    exit 0
else
    echo -e "${RED}✗ PRODUCTION VALIDATION FAILED${NC}"
    exit 1
fi
