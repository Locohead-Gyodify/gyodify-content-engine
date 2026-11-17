"""
check_festivals.py — Preview upcoming festival triggers for the next 30 days.
Run once after editing festivals.yaml to verify dates are correct.

Usage:
  python scripts/check_festivals.py
  python scripts/check_festivals.py --days 60
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.research.calendar import get_upcoming_festivals

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30, help="Days to look ahead")
    args = parser.parse_args()

    upcoming = get_upcoming_festivals(args.days)

    if not upcoming:
        print(f"No festivals in the next {args.days} days.")
        return

    print(f"\n📅 Upcoming festivals — next {args.days} days\n")
    print(f"{'Days':>5}  {'Date':>12}  {'Language':>10}  Festival")
    print("-" * 55)
    for f in upcoming:
        print(f"  {f['days_until']:>3}d  {f['date']:>12}  {f['language']:>10}  {f['name']}")

    print(f"\n✅ Verify these dates are correct in config/festivals.yaml")
    print("   Run each year before the festival season begins.\n")

if __name__ == "__main__":
    main()
