from pathlib import Path
import json
import sys

# Ensure project root (ShiftPlan_Agent_Demo) is on sys.path so 'app' imports work when running as a script
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.services.forecast import run_forecast

def main() -> int:
    # Use the provided example file with period-level data
    path = Path("testdata/Shift_Plan_Example_2.xlsx")
    res = run_forecast(path)

    # Print a concise summary to stdout
    preview = res.get("preview", [])
    metrics = res.get("metrics", {})
    print("Preview length:", len(preview))
    print("Metrics:", json.dumps(metrics, indent=2))
    print("First 5 preview rows:")
    print(json.dumps(preview[:5], indent=2))

    # Basic assertions for period-level behavior
    if not preview:
        print("ERROR: No preview rows returned")
        return 1

    first = preview[0]
    required = {"Date", "From", "To"}
    missing = required - set(first.keys())
    if missing:
        print(f"ERROR: Missing required keys in preview row: {missing}")
        return 2

    # Ensure third role (e.g., Checkout) appears in at least one row
    has_checkout = any(("Checkout" in row) for row in preview)
    if not has_checkout:
        print("ERROR: Checkout role not present in preview rows")
        return 3

    print("TEST OK: period-level forecast includes Date/From/To and Checkout role in preview.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
