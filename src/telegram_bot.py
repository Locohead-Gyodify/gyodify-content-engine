"""
telegram_bot.py — Telegram delivery and feedback polling
No server required — uses getUpdates polling approach
"""
from __future__ import annotations
import json
import time
from datetime import date
from pathlib import Path
from typing import Optional

import requests

from src.utils import get_logger, require_env, optional_env, file_size_mb
from src.copywriter import CopyResult, format_telegram_caption
from src.qa import QAResult, format_qa_report
from src.shopify import Product

log = get_logger("telegram")

MAX_FILE_MB = 48
MAX_MSG_CHARS = 4096


class TelegramBot:
    def __init__(self):
        self.token = require_env("TELEGRAM_BOT_TOKEN")
        self.chat_id = require_env("TELEGRAM_CHAT_ID")
        self.base = f"https://api.telegram.org/bot{self.token}"
        self.retry_count = 3
        self.retry_delay = 5

    def _post(self, method: str, **kwargs) -> dict:
        url = f"{self.base}/{method}"
        for attempt in range(self.retry_count):
            try:
                resp = requests.post(url, timeout=60, **kwargs)
                data = resp.json()
                if data.get("ok"):
                    return data
                log.warning(f"Telegram {method} failed: {data.get('description')}")
            except Exception as e:
                log.warning(f"Telegram {method} attempt {attempt+1} failed: {e}")
            if attempt < self.retry_count - 1:
                time.sleep(self.retry_delay)
        raise RuntimeError(f"Telegram {method} failed after {self.retry_count} retries")

    def send_message(self, text: str, parse_mode: str = "Markdown",
                     reply_markup: dict = None) -> dict:
        """Send a text message, splitting if needed."""
        chunks = [text[i:i+MAX_MSG_CHARS] for i in range(0, len(text), MAX_MSG_CHARS)]
        result = {}
        for chunk in chunks:
            payload = {"chat_id": self.chat_id, "text": chunk, "parse_mode": parse_mode}
            if reply_markup and chunk == chunks[-1]:
                payload["reply_markup"] = json.dumps(reply_markup)
            result = self._post("sendMessage", json=payload)
        return result

    def send_document(self, file_path: Path, caption: str = "") -> dict:
        """Send a file as document (preserves quality)."""
        if not file_path.exists():
            raise FileNotFoundError(f"File not found: {file_path}")
        size = file_size_mb(file_path)
        if size > MAX_FILE_MB:
            log.warning(f"File too large: {size:.1f}MB > {MAX_FILE_MB}MB, skipping")
            self.send_message(f"⚠️ {file_path.name} too large ({size:.1f}MB) — download from Actions artifact")
            return {}
        with open(file_path, "rb") as f:
            return self._post(
                "sendDocument",
                data={"chat_id": self.chat_id,
                      "caption": caption[:1024] if caption else ""},
                files={"document": (file_path.name, f)},
            )

    def send_photo(self, file_path: Path, caption: str = "") -> dict:
        """Send image as photo preview."""
        with open(file_path, "rb") as f:
            return self._post(
                "sendPhoto",
                data={"chat_id": self.chat_id,
                      "caption": caption[:1024] if caption else ""},
                files={"photo": (file_path.name, f)},
            )

    def get_updates(self, offset: int = 0) -> list[dict]:
        """Poll for new messages/callbacks (no server needed)."""
        try:
            data = self._post("getUpdates", json={"offset": offset, "timeout": 5})
            return data.get("result", [])
        except Exception as e:
            log.warning(f"getUpdates failed: {e}")
            return []

    def answer_callback(self, callback_query_id: str, text: str = "") -> None:
        self._post("answerCallbackQuery", json={
            "callback_query_id": callback_query_id,
            "text": text,
        })


def _build_feedback_keyboard(post_date: str) -> dict:
    """Inline keyboard with feedback buttons."""
    return {
        "inline_keyboard": [[
            {"text": "👍 Posted well",    "callback_data": f"fb|posted_well|{post_date}"},
            {"text": "😐 Average",        "callback_data": f"fb|posted_average|{post_date}"},
            {"text": "👎 Skipped",        "callback_data": f"fb|skipped|{post_date}"},
            {"text": "♻️ Regenerate",     "callback_data": f"fb|regenerate|{post_date}"},
        ]]
    }


def deliver_batch(
    copy: CopyResult,
    media_paths: list[Path],
    qa: QAResult,
    product: Optional[Product],
    state,
    run_meta: dict,
) -> bool:
    """
    Full delivery sequence:
    1. Daily brief
    2. Media files (as documents)
    3. Copy-paste caption block
    4. Posting notes
    5. Product link + alt text
    6. QA report + feedback buttons
    """
    bot = TelegramBot()
    today = date.today().isoformat()
    pos = state.current_position
    fmt = state.current_format

    # ── 1. Daily brief ───────────────────────────────────────
    qa_flag = "✅ All clear" if qa.passed else "⚠️ Review needed"
    llm_provider = run_meta.get("llm_provider", "unknown")
    sources_used = ", ".join(run_meta.get("sources_used", ["unknown"]))
    tokens = run_meta.get("tokens_used", 0)

    brief = (
        f"🕐 *GYODIFY Daily Batch* — {today}\n\n"
        f"📌 *Position:* {pos}/6 → `{fmt.upper()}`\n"
        f"🎨 *Template:* `{state.current_template}`\n"
        f"🌐 *Language:* {copy.language}\n"
        f"📊 *LLM:* {llm_provider} ({tokens} tokens)\n"
        f"🔍 *Sources:* {sources_used}\n"
        f"{'✅ QA PASSED' if qa.passed else '❌ QA ISSUES — review before posting'}\n"
    )
    if not qa.passed:
        fail_checks = [k for k, v in qa.checks.items() if v == "fail"]
        brief += f"❌ Failed: {', '.join(fail_checks)}\n"

    if product and product.licensed_flag:
        brief += "\n⚠️ *Licensed-looking graphic detected — review before posting*\n"

    bot.send_message(brief)
    log.info("Brief sent")
    time.sleep(1)

    # ── 2. Media files ───────────────────────────────────────
    for path in media_paths:
        try:
            if path.suffix.lower() == ".mp4":
                bot.send_document(path, caption=f"🎬 Reel — add trending audio in-app")
            else:
                bot.send_document(path, caption=path.stem.replace("_", " ").title())
            time.sleep(1)
        except Exception as e:
            log.error(f"Failed to send {path.name}: {e}")

    # ── 3. Caption block ─────────────────────────────────────
    caption_text = format_telegram_caption(copy)
    caption_msg = (
        f"📝 *Caption — copy & paste*\n\n"
        f"`{caption_text[:3800]}`"  # monospace for easy copy
    )
    bot.send_message(caption_msg)
    time.sleep(1)

    # ── 4. Posting notes ─────────────────────────────────────
    notes_lines = [f"📌 *Posting Notes*"]
    notes_lines.append(f"⏰ Suggested time: *{copy.posting_time_ist}* IST")
    if copy.posting_time_reason:
        notes_lines.append(f"💡 Why: {copy.posting_time_reason}")

    if fmt == "reel":
        audio_vibe = run_meta.get("audio_vibe", "upbeat, energetic, trending Indian")
        tts_method = run_meta.get("tts_method", "silent")
        notes_lines.append(f"\n🎵 *Audio vibe:* {audio_vibe}")
        notes_lines.append(f"🔇 TTS method used: {tts_method}")
        notes_lines.append("👉 Add trending audio in-app before posting")
        notes_lines.append("🖼️ Cover image: `reel_cover.png` (attached above)")

    if fmt == "carousel":
        notes_lines.append(f"\n📱 Carousel: {len([p for p in media_paths if 'slide' in p.name])} slides")
        notes_lines.append("👉 Upload all slides in order")

    bot.send_message("\n".join(notes_lines))
    time.sleep(1)

    # ── 5. Product link + alt text ───────────────────────────
    if product:
        link_msg = (
            f"🔗 *Product Link*\n\n"
            f"*{product.clean_title}*\n"
            f"₹{product.price_min:.0f} · Free shipping\n"
            f"{product.url}\n\n"
            f"♿ *Alt text:*\n`{copy.alt_text}`\n\n"
            f"📢 *Link in bio CTA:* {copy.cta}"
        )
        bot.send_message(link_msg)
        time.sleep(1)

    # ── 6. QA report + feedback ──────────────────────────────
    qa_text = format_qa_report(qa)
    keyboard = _build_feedback_keyboard(today)
    bot.send_message(qa_text, reply_markup=keyboard)

    log.info("Full batch delivered to Telegram")
    return True


def poll_feedback(state) -> list[dict]:
    """
    Poll for feedback button callbacks from previous batches.
    Called at the start of each daily run.
    """
    bot = TelegramBot()
    history = state.history
    last_update_id = history.get("last_telegram_update_id", 0)

    updates = bot.get_updates(offset=last_update_id + 1)
    processed = []

    for update in updates:
        update_id = update.get("update_id", 0)
        callback = update.get("callback_query")

        if callback:
            data = callback.get("data", "")
            if data.startswith("fb|"):
                parts = data.split("|")
                if len(parts) == 3:
                    _, feedback_type, post_date = parts
                    state.apply_feedback(feedback_type, post_date)
                    bot.answer_callback(
                        callback["id"],
                        text=f"Noted: {feedback_type.replace('_', ' ')}",
                    )
                    processed.append({"feedback": feedback_type, "date": post_date})
                    log.info(f"Feedback applied: {feedback_type} for {post_date}")

        # Also handle /stats command
        message = update.get("message", {})
        text = message.get("text", "")
        if text.startswith("/stats"):
            _handle_stats_command(text, state)

        last_update_id = max(last_update_id, update_id)

    if updates:
        history["last_telegram_update_id"] = last_update_id
        from src.utils import save_history
        save_history(history)

    return processed


def _handle_stats_command(text: str, state) -> None:
    """
    Parse /stats command: /stats 2026-11-14 likes=84 saves=22 reach=2100
    """
    import re
    parts = text.split()
    if len(parts) < 2:
        return
    post_date = parts[1]
    metrics = {}
    for part in parts[2:]:
        m = re.match(r"(\w+)=(\d+)", part)
        if m:
            metrics[m.group(1)] = int(m.group(2))
    if metrics:
        state.log_metrics(post_date, metrics)
        log.info(f"Stats logged for {post_date}: {metrics}")
