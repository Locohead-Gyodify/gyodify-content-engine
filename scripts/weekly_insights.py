"""
weekly_insights.py — Analyse last 30 days and send insights to Telegram.
Run via the weekly_insights.yml GitHub Actions workflow.
"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.state import StateManager
from src.telegram_bot import TelegramBot
from src.utils import get_logger

log = get_logger("weekly_insights")


def main():
    state = StateManager()
    data = state.get_insights_data(days=30)
    bot = TelegramBot()

    if data["total_posts"] == 0:
        bot.send_message("📊 *Weekly Insights*\n\nNot enough data yet — keep posting! 💪")
        return

    # Build insights message
    lines = ["📊 *GYODIFY Weekly Insights* (last 30 days)\n"]
    lines.append(f"📮 Total posts: *{data['total_posts']}*")
    lines.append(f"⭐ Avg QA score: *{data['avg_qa_score']:.1f}/10*\n")

    if data["formats"]:
        lines.append("*Format breakdown:*")
        for fmt, count in data["formats"].most_common():
            lines.append(f"  · {fmt}: {count}")

    if data["pillars"]:
        lines.append("\n*Content pillars:*")
        for pillar, count in data["pillars"].most_common(3):
            lines.append(f"  · {pillar}: {count}")

    if data["languages"]:
        lines.append("\n*Languages used:*")
        for lang, count in data["languages"].most_common():
            lines.append(f"  · {lang}: {count}")

    lines.append("\n💡 _Tip: Log metrics with /stats to unlock deeper insights_")
    bot.send_message("\n".join(lines))
    log.info("Weekly insights sent")


if __name__ == "__main__":
    main()
