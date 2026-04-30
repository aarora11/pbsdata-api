#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# PBSdata.io — manual endpoint test script
#
# SETUP
# ─────
# 1. Set BASE_URL to your local or Railway deployment.
# 2. Create test keys at each tier level. The easiest way is via psql directly:
#
#    -- Run this in your Railway/local psql console to seed one key per tier.
#    -- Keys use the prefix_secret format: e.g. "pd_free_AAAA.mysecret"
#    -- In production you generate real hashed keys via the API; for testing
#    -- just insert a known key_hash of SHA-256("testkey_<tier>").
#
#    To get real keys quickly, use the /v1/auth/keys endpoint (free tier only),
#    then promote via psql for higher tiers:
#
#      UPDATE api_keys SET tier='growth', monthly_limit=100000, history_months_limit=12
#      WHERE customer_email='your@email.com';
#
# 3. Set the KEY_* variables below to your actual keys.
# 4. Set PBS_CODE to a real code from your ingested schedule (see step 0).
# 5. Run: bash test_endpoints.sh
#
# ─────────────────────────────────────────────────────────────────────────────

BASE_URL="https://pbsdata-api-production.up.railway.app"
# BASE_URL="http://localhost:8000"

# Keys — replace with real keys from your database
KEY_FREE="YOUR_FREE_TIER_KEY"
KEY_STARTER="YOUR_STARTER_TIER_KEY"
KEY_GROWTH="YOUR_GROWTH_TIER_KEY"
KEY_SCALE="YOUR_SCALE_TIER_KEY"

# A real PBS code from your ingested schedule (see Step 0 below to discover one)
PBS_CODE="02647B"

# A real li_item_id from item_pricing (see Step 0)
LI_ITEM_ID="2647B"

# A real restriction code (see Step 0)
RESTRICTION_CODE="2647B"

# A real ATC code (see Step 0)
ATC_CODE="C09AA05"

# A real organisation ID (integer) from your database
ORG_ID="1"

# ─── Helpers ──────────────────────────────────────────────────────────────────

pass=0; fail=0
check() {
  local label="$1" url="$2" key="$3" expected_status="${4:-200}"
  local status
  status=$(curl -s -o /dev/null -w "%{http_code}" -H "X-API-Key: $key" "$url")
  if [ "$status" = "$expected_status" ]; then
    echo "  PASS [$status] $label"
    ((pass++))
  else
    echo "  FAIL [$status expected $expected_status] $label"
    echo "       $url"
    ((fail++))
  fi
}

pretty() {
  local label="$1" url="$2" key="$3"
  echo ""
  echo "── $label"
  curl -s -H "X-API-Key: $key" "$url" | python3 -m json.tool 2>/dev/null || echo "(no JSON)"
  echo ""
}

section() { echo ""; echo "═══════════════════════════════════════════"; echo "  $1"; echo "═══════════════════════════════════════════"; }

# ─────────────────────────────────────────────────────────────────────────────
# STEP 0 — Discover real PBS codes from your schedule
# Run these manually to find codes to paste into the variables above.
# ─────────────────────────────────────────────────────────────────────────────

discover() {
  section "STEP 0 — Discover real data (run manually)"
  echo ""
  echo "Items sample:"
  curl -s -H "X-API-Key: $KEY_FREE" "$BASE_URL/v1/items?limit=5" | python3 -m json.tool 2>/dev/null
  echo ""
  echo "Restrictions sample:"
  curl -s -H "X-API-Key: $KEY_FREE" "$BASE_URL/v1/restrictions?limit=5" | python3 -m json.tool 2>/dev/null
  echo ""
  echo "ATC codes (level 5 sample):"
  curl -s -H "X-API-Key: $KEY_STARTER" "$BASE_URL/v1/atc-codes/by-level/5?limit=5" | python3 -m json.tool 2>/dev/null
  echo ""
  echo "Organisations sample:"
  curl -s -H "X-API-Key: $KEY_FREE" "$BASE_URL/v1/organisations?limit=5" | python3 -m json.tool 2>/dev/null
}

# ─────────────────────────────────────────────────────────────────────────────
# STEP 1 — Auth endpoints (no key required)
# ─────────────────────────────────────────────────────────────────────────────

test_auth() {
  section "Auth & Health (no key required)"

  local hs
  hs=$(curl -s -o /dev/null -w "%{http_code}" "$BASE_URL/health")
  [ "$hs" = "200" ] && echo "  PASS [$hs] GET /health" && ((pass++)) || { echo "  FAIL [$hs] GET /health"; ((fail++)); }

  echo "  INFO: Create a free key:"
  echo "        curl -s -X POST $BASE_URL/v1/auth/keys \\"
  echo "             -H 'Content-Type: application/json' \\"
  echo "             -d '{\"name\":\"Test Key\",\"email\":\"test@example.com\"}'"

  check "GET /v1/auth/keys/me" "$BASE_URL/v1/auth/keys/me" "$KEY_FREE"
}

# ─────────────────────────────────────────────────────────────────────────────
# STEP 2 — Base tier (free key)
# ─────────────────────────────────────────────────────────────────────────────

test_base() {
  section "Base Tier (free key)"
  local K="$KEY_FREE"

  check "GET /v1/items/{pbs_code}" "$BASE_URL/v1/items/$PBS_CODE" "$K"
  check "GET /v1/medicines" "$BASE_URL/v1/medicines?limit=5" "$K"
  check "GET /v1/restrictions" "$BASE_URL/v1/restrictions?limit=5" "$K"
  check "GET /v1/restrictions/{code}" "$BASE_URL/v1/restrictions/$RESTRICTION_CODE" "$K"
  check "GET /v1/schedules" "$BASE_URL/v1/schedules" "$K"
  check "GET /v1/programs" "$BASE_URL/v1/programs" "$K"
  check "GET /v1/fees" "$BASE_URL/v1/fees" "$K"
  check "GET /v1/atc-codes" "$BASE_URL/v1/atc-codes?limit=5" "$K"
  check "GET /v1/atc-codes/{code}" "$BASE_URL/v1/atc-codes/$ATC_CODE" "$K"
  check "GET /v1/organisations" "$BASE_URL/v1/organisations?limit=5" "$K"
  check "GET /v1/copayments" "$BASE_URL/v1/copayments" "$K"
  check "GET /v1/summary-of-changes" "$BASE_URL/v1/summary-of-changes?limit=5" "$K"

  echo ""
  echo "  Verify Base gets flat /items response (no 'data' wrapper):"
  pretty "GET /v1/items/{pbs_code} (Base)" "$BASE_URL/v1/items/$PBS_CODE" "$K"

  echo "  Verify Base gets 403 on T1+ endpoint:"
  check "GET /v1/schedules/latest (expect 403)" "$BASE_URL/v1/schedules/latest" "$K" "403"
  check "GET /v1/copayments/current (expect 403)" "$BASE_URL/v1/copayments/current" "$K" "403"
  check "GET /v1/drugs/$PBS_CODE (expect 403)" "$BASE_URL/v1/drugs/$PBS_CODE" "$K" "403"
}

# ─────────────────────────────────────────────────────────────────────────────
# STEP 3 — Core tier / T1 (starter key)
# ─────────────────────────────────────────────────────────────────────────────

test_t1() {
  section "Core Tier / T1 (starter key)"
  local K="$KEY_STARTER"

  check "GET /v1/schedules/latest" "$BASE_URL/v1/schedules/latest" "$K"
  check "GET /v1/copayments/current" "$BASE_URL/v1/copayments/current" "$K"
  check "GET /v1/atc-codes/by-level/5" "$BASE_URL/v1/atc-codes/by-level/5?limit=10" "$K"
  check "GET /v1/atc-codes/{code}/hierarchy" "$BASE_URL/v1/atc-codes/$ATC_CODE/hierarchy" "$K"
  check "GET /v1/atc-codes/{code}/children" "$BASE_URL/v1/atc-codes/$ATC_CODE/children" "$K"
  check "GET /v1/dispensing-rules/by-program/GE" "$BASE_URL/v1/dispensing-rules/by-program/GE" "$K"
  check "GET /v1/organisations/search" "$BASE_URL/v1/organisations/search?q=pharma" "$K"
  check "GET /v1/programs (T1+ has dispensing_rules embedded)" "$BASE_URL/v1/programs" "$K"

  echo ""
  pretty "GET /v1/schedules/latest" "$BASE_URL/v1/schedules/latest" "$K"
  pretty "GET /v1/copayments/current" "$BASE_URL/v1/copayments/current" "$K"

  echo "  Verify T1 still gets 403 on T2+ endpoints:"
  check "GET /v1/drugs/$PBS_CODE (expect 403)" "$BASE_URL/v1/drugs/$PBS_CODE" "$K" "403"
}

# ─────────────────────────────────────────────────────────────────────────────
# STEP 4 — Clinical tier / T2 (growth key)
# ─────────────────────────────────────────────────────────────────────────────

test_t2() {
  section "Clinical Tier / T2 (growth key)"
  local K="$KEY_GROWTH"

  check "GET /v1/items/{pbs_code} (enriched)" "$BASE_URL/v1/items/$PBS_CODE" "$K"
  check "GET /v1/items/{pbs_code}/price" "$BASE_URL/v1/items/$PBS_CODE/price" "$K"
  check "GET /v1/items/{pbs_code}/patient-cost" "$BASE_URL/v1/items/$PBS_CODE/patient-cost" "$K"
  check "GET /v1/items/{pbs_code}/prescribing-texts" "$BASE_URL/v1/items/$PBS_CODE/prescribing-texts" "$K"
  check "GET /v1/items/{pbs_code}/dispensing-rules" "$BASE_URL/v1/items/$PBS_CODE/dispensing-rules" "$K"
  check "GET /v1/restrictions/{code} (enriched)" "$BASE_URL/v1/restrictions/$RESTRICTION_CODE" "$K"
  check "GET /v1/drugs/{pbs_code}" "$BASE_URL/v1/drugs/$PBS_CODE" "$K"
  check "GET /v1/drugs/{pbs_code}/brands" "$BASE_URL/v1/drugs/$PBS_CODE/brands" "$K"
  check "GET /v1/drugs/{pbs_code}/prescribers" "$BASE_URL/v1/drugs/$PBS_CODE/prescribers" "$K"
  check "GET /v1/drugs/{pbs_code}/atc" "$BASE_URL/v1/drugs/$PBS_CODE/atc" "$K"
  check "GET /v1/drugs/{pbs_code}/amt" "$BASE_URL/v1/drugs/$PBS_CODE/amt" "$K"
  check "GET /v1/drugs/{pbs_code}/restrictions" "$BASE_URL/v1/drugs/$PBS_CODE/restrictions" "$K"

  echo ""
  echo "  Verify T2 enriched /items returns 'data' wrapper:"
  pretty "GET /v1/items/{pbs_code} (T2 enriched)" "$BASE_URL/v1/items/$PBS_CODE" "$K"

  pretty "GET /v1/drugs/{pbs_code}" "$BASE_URL/v1/drugs/$PBS_CODE" "$K"
  pretty "GET /v1/drugs/{pbs_code}/prescribers" "$BASE_URL/v1/drugs/$PBS_CODE/prescribers" "$K"

  echo "  Verify T2 gets 403 on T3 endpoints:"
  check "GET /v1/drugs/search (expect 403)" "$BASE_URL/v1/drugs/search?q=metformin" "$K" "403"
  check "GET /v1/drugs/$PBS_CODE/full-profile (expect 403)" "$BASE_URL/v1/drugs/$PBS_CODE/full-profile" "$K" "403"
}

# ─────────────────────────────────────────────────────────────────────────────
# STEP 5 — Intelligence tier / T3 (scale key)
# ─────────────────────────────────────────────────────────────────────────────

test_t3() {
  section "Intelligence Tier / T3 (scale key)"
  local K="$KEY_SCALE"

  # Drug search
  check "GET /v1/drugs/search?q=metformin" "$BASE_URL/v1/drugs/search?q=metformin" "$K"
  check "GET /v1/drugs/search?q=meto&benefit_type=A" "$BASE_URL/v1/drugs/search?q=meto&benefit_type=A" "$K"

  # Full drug intelligence
  check "GET /v1/drugs/{pbs_code}/full-profile" "$BASE_URL/v1/drugs/$PBS_CODE/full-profile" "$K"
  check "GET /v1/drugs/{pbs_code}/restriction-full" "$BASE_URL/v1/drugs/$PBS_CODE/restriction-full" "$K"
  check "GET /v1/drugs/{pbs_code}/authority-workflow" "$BASE_URL/v1/drugs/$PBS_CODE/authority-workflow" "$K"
  check "GET /v1/drugs/{pbs_code}/substitution" "$BASE_URL/v1/drugs/$PBS_CODE/substitution" "$K"
  check "GET /v1/drugs/{pbs_code}/price-history" "$BASE_URL/v1/drugs/$PBS_CODE/price-history" "$K"
  check "GET /v1/drugs/{pbs_code}/pricing-events" "$BASE_URL/v1/drugs/$PBS_CODE/pricing-events" "$K"
  check "GET /v1/drugs/{pbs_code}/safety-net" "$BASE_URL/v1/drugs/$PBS_CODE/safety-net" "$K"
  check "GET /v1/drugs/{pbs_code}/60-day-pair" "$BASE_URL/v1/drugs/$PBS_CODE/60-day-pair" "$K"
  check "GET /v1/drugs/{pbs_code}/formulary-status" "$BASE_URL/v1/drugs/$PBS_CODE/formulary-status" "$K"

  # Item dispensing context
  check "GET /v1/items/{li_item_id}/dispensing-context" "$BASE_URL/v1/items/$LI_ITEM_ID/dispensing-context" "$K"

  # Organisation portfolio
  check "GET /v1/organisations/{id}/portfolio" "$BASE_URL/v1/organisations/$ORG_ID/portfolio" "$K"
  check "GET /v1/organisations/{id}/portfolio?benefit_type=A" "$BASE_URL/v1/organisations/$ORG_ID/portfolio?benefit_type=A" "$K"

  # ATC items
  check "GET /v1/atc-codes/{code}/items" "$BASE_URL/v1/atc-codes/$ATC_CODE/items" "$K"
  check "GET /v1/atc-codes/{code}/items?include_descendants=true" "$BASE_URL/v1/atc-codes/$ATC_CODE/items?include_descendants=true" "$K"

  # Program fee structure
  check "GET /v1/programs/GE/fee-structure" "$BASE_URL/v1/programs/GE/fee-structure" "$K"

  # Extemporaneous
  check "GET /v1/extemporaneous/ingredients" "$BASE_URL/v1/extemporaneous/ingredients" "$K"
  check "GET /v1/extemporaneous/tariffs" "$BASE_URL/v1/extemporaneous/tariffs" "$K"
  check "GET /v1/extemporaneous/preparations" "$BASE_URL/v1/extemporaneous/preparations" "$K"

  # Schedule changes
  check "GET /v1/schedule-changes" "$BASE_URL/v1/schedule-changes" "$K"
  check "GET /v1/schedule-changes/additions" "$BASE_URL/v1/schedule-changes/additions" "$K"
  check "GET /v1/schedule-changes/deletions" "$BASE_URL/v1/schedule-changes/deletions" "$K"
  check "GET /v1/schedule-changes/price-changes" "$BASE_URL/v1/schedule-changes/price-changes" "$K"
  check "GET /v1/schedule-changes/restriction-changes" "$BASE_URL/v1/schedule-changes/restriction-changes" "$K"

  echo ""
  pretty "GET /v1/drugs/search?q=metformin" "$BASE_URL/v1/drugs/search?q=metformin" "$K"
  pretty "GET /v1/drugs/{pbs_code}/authority-workflow" "$BASE_URL/v1/drugs/$PBS_CODE/authority-workflow" "$K"
  pretty "GET /v1/drugs/{pbs_code}/safety-net" "$BASE_URL/v1/drugs/$PBS_CODE/safety-net" "$K"
  pretty "GET /v1/schedule-changes?limit=3" "$BASE_URL/v1/schedule-changes?limit=3" "$K"
}

# ─────────────────────────────────────────────────────────────────────────────
# STEP 6 — Edge cases and error handling
# ─────────────────────────────────────────────────────────────────────────────

test_errors() {
  section "Error Handling"

  echo "  Missing API key:"
  local hs
  hs=$(curl -s -o /dev/null -w "%{http_code}" "$BASE_URL/v1/items/$PBS_CODE")
  [ "$hs" = "401" ] && echo "  PASS [$hs] No key → 401" && ((pass++)) || { echo "  FAIL [$hs] expected 401"; ((fail++)); }

  echo "  Invalid API key:"
  check "Invalid key → 401" "$BASE_URL/v1/items/$PBS_CODE" "pd_invalid_key.badvalue" "401"

  echo "  Not found:"
  check "Unknown PBS code → 404" "$BASE_URL/v1/items/XXXXXX" "$KEY_FREE" "404"
  check "Unknown restriction → 404" "$BASE_URL/v1/restrictions/XXXXXX" "$KEY_FREE" "404"

  echo "  History limit:"
  check "Base key old schedule → 403" "$BASE_URL/v1/items/$PBS_CODE?schedule=2024-01" "$KEY_FREE" "403"
}

# ─────────────────────────────────────────────────────────────────────────────
# RUNNER
# ─────────────────────────────────────────────────────────────────────────────

main() {
  echo ""
  echo "PBSdata.io endpoint test suite"
  echo "Base URL: $BASE_URL"
  echo ""

  # Uncomment the step you want to run, or run all:
  test_auth
  test_base
  test_t1
  test_t2
  test_t3
  test_errors

  echo ""
  echo "═══════════════════════════════════════════"
  echo "  Results: $pass passed, $fail failed"
  echo "═══════════════════════════════════════════"
  echo ""

  [ "$fail" -eq 0 ]
}

# Uncomment to run discovery queries instead:
# discover

main "$@"
