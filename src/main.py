"""
main.py — GYODIFY Content Engine Orchestrator
Usage:
  python -m src.main
  python -m src.main --dry-run
  python -m src.main --language hindi --format carousel --date 2026-11-15
"""
from __future__ import annotations
import argparse
import json
import sys
import time
from datetime import date, datetime
from pathlib import Path

from src.utils import get_logger, get_output_dir, load_settings

log = get_logger("main")


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="GYODIFY Instagram Content Engine")
    p.add_argument("--dry-run", action="store_true",
                   help="Generate content but do not send to Telegram")
    p.add_argument("--language", choices=["english", "hindi", "marathi", "hinglish"],
                   help="Force a specific language")
    p.add_argument("--format", choices=["carousel", "reel", "post"],
                   help="Force a specific format (overrides sequence)")
    p.add_argument("--date", help="Simulate a specific date (YYYY-MM-DD)")
    p.add_argument("--position", type=int, choices=range(1, 7),
                   help="Force a specific feed position (1-6)")
    return p.parse_args()


def run(args: argparse.Namespace) -> dict:
    """Main pipeline. Returns run_summary dict."""
    t_start = time.monotonic()
    today_str = args.date or date.today().isoformat()
    output_dir = get_output_dir(today_str)
    settings = load_settings()

    run_summary = {
        "date": today_str,
        "dry_run": args.dry_run,
        "started_at": datetime.now().isoformat(),
        "stages": {},
        "sources_used": [],
        "llm_provider": None,
        "tokens_used": 0,
        "qa_passed": False,
        "error": None,
    }

    try:
        # ── Stage 0: Poll feedback from previous runs ──────
        log.info("Stage 0: Polling Telegram feedback...")
        from src.state import StateManager
        state = StateManager()

        if not args.dry_run:
            from src.telegram_bot import poll_feedback
            feedback_received = poll_feedback(state)
            if feedback_received:
                log.info(f"Processed {len(feedback_received)} feedback items")

        # Override position if specified
        if args.position:
            state.history["current_position"] = args.position
            from src.utils import save_history
            save_history(state.history)

        pos = state.current_position
        fmt = args.format or state.current_format
        template = state.current_template
        log.info(f"Position: {pos}/6 | Format: {fmt} | Template: {template}")
        run_summary["stages"]["position"] = {"pos": pos, "format": fmt, "template": template}

        # ── Stage 1: Shopify sync ──────────────────────────
        log.info("Stage 1: Shopify catalogue sync...")
        try:
            from src.shopify import fetch_catalogue, select_products_for_post, download_product_image
            products = fetch_catalogue()
            run_summary["stages"]["shopify"] = {"products": len(products)}
        except Exception as e:
            log.warning(f"Shopify live fetch failed: {e} — using cache")
            from src.shopify import load_cached_catalogue
            products = load_cached_catalogue()
            run_summary["stages"]["shopify"] = {"products": len(products), "cached": True}

        selected_products = select_products_for_post(products, state)
        featured_product = selected_products[0] if selected_products else None

        if featured_product:
            img_path = download_product_image(featured_product)
            if img_path:
                featured_product.local_image_path = img_path

        log.info(f"Featured product: {featured_product.clean_title if featured_product else 'None'}")

        # ── Stage 2: Research ──────────────────────────────
        log.info("Stage 2: Trend research...")
        signals = []
        sources_used = []

        try:
            from src.research.trends import fetch_trending
            trend_signals = fetch_trending()
            signals.extend(trend_signals)
            if trend_signals:
                sources_used.append("google_trends")
        except Exception as e:
            log.warning(f"Trends failed: {e}")

        try:
            from src.research.news import fetch_news
            news_signals = fetch_news()
            signals.extend(news_signals)
            if news_signals:
                sources_used.append("google_news")
        except Exception as e:
            log.warning(f"News failed: {e}")

        try:
            from src.research.reddit import fetch_reddit
            reddit_signals = fetch_reddit()
            signals.extend(reddit_signals)
            if reddit_signals:
                sources_used.append("reddit")
        except Exception as e:
            log.warning(f"Reddit failed: {e}")

        try:
            from src.research.calendar import fetch_calendar_signals
            cal_signals = fetch_calendar_signals()
            signals.extend(cal_signals)
            if cal_signals:
                sources_used.append("festival_calendar")
        except Exception as e:
            log.warning(f"Calendar failed: {e}")

        if not signals:
            sources_used.append("evergreen_themes")

        run_summary["sources_used"] = sources_used
        run_summary["stages"]["research"] = {"signals": len(signals), "sources": sources_used}
        log.info(f"Research: {len(signals)} signals from {sources_used}")

        # Detect festival context for language
        cal_signals_active = [s for s in signals if s.category in ("festival", "season")]
        is_festival = bool(cal_signals_active)
        festival_language = None
        if cal_signals_active:
            festival_language = cal_signals_active[0].extra.get("language_preference")

        # ── Stage 3: Language selection ────────────────────
        language = state.pick_language(
            force=args.language,
            is_festival=is_festival,
            festival_language=festival_language,
        )
        log.info(f"Language: {language}")
        run_summary["stages"]["language"] = language

        # ── Stage 4: LLM init + ideation ──────────────────
        log.info("Stage 4: Ideation...")
        from src.llm import LLMClient
        llm = LLMClient()

        from src.ideation import generate_concepts, select_best_concept
        concepts = generate_concepts(llm, state, signals, selected_products, language)

        concept = select_best_concept(concepts)
        if not concept:
            log.error("No valid concept generated — aborting")
            run_summary["error"] = "No valid concept"
            return run_summary

        log.info(f"Concept: '{concept.hook_line}' (score: {concept.final_score:.1f})")
        run_summary["stages"]["ideation"] = {
            "hook": concept.hook_line,
            "pillar": concept.angle,
            "score": concept.final_score,
        }

        # ── Stage 5: Copywriting ───────────────────────────
        log.info("Stage 5: Copywriting...")
        from src.copywriter import generate_copy
        copy = generate_copy(llm, concept, featured_product, language)
        run_summary["stages"]["copywriting"] = {
            "caption_len": len(copy.caption),
            "hashtags": len(copy.hashtags),
            "provider": copy.provider,
        }

        # ── Stage 6: Rendering ────────────────────────────
        log.info("Stage 6: Rendering creative assets...")
        media_paths = []
        base_context = {
            "position": pos,
            "hook": copy.hook,
            "caption": copy.caption,
            "cta": copy.cta,
            "language": language,
            "product_name": featured_product.clean_title if featured_product else "GYODIFY Tee",
            "product_url": featured_product.url if featured_product else "https://gyodify.shop",
            "price_min": featured_product.price_min if featured_product else 349,
            "product_image_path": featured_product.local_image_path if featured_product else None,
            "is_new_drop": featured_product.is_new if featured_product else False,
            "date": today_str,
        }

        try:
            if fmt == "carousel":
                from src.render.carousel import render_carousel
                slide_paths = render_carousel(copy, base_context, output_dir, template)
                media_paths.extend(slide_paths)

            elif fmt == "reel":
                from src.render.reel import render_reel
                reel_path = render_reel(copy, base_context, output_dir, template)
                if reel_path:
                    media_paths.append(reel_path)
                    cover = output_dir / "reel_cover.png"
                    if cover.exists():
                        media_paths.append(cover)
                run_summary["audio_vibe"] = base_context.get("audio_vibe", "")
                run_summary["tts_method"] = base_context.get("tts_method", "silent")

            elif fmt == "post":
                from src.render.image import render_post
                post_path = output_dir / "post.png"
                success = render_post(template, base_context, post_path)
                if success:
                    media_paths.append(post_path)

                except Exception as e:
            log.exception(f"Rendering failed: {e}")
            run_summary["stages"]["render"] = {
                "error": str(e),
                "files": [str(p) for p in media_paths],
            }
        else:
            run_summary["stages"]["render"] = {
                "files": [str(p) for p in media_paths],
            }
        log.info(f"Rendered {len(media_paths)} files")

        # ── Stage 7: QA gate ──────────────────────────────
        log.info("Stage 7: QA gate...")
        from src.qa import run_qa
        qa = run_qa(copy, media_paths, featured_product, state, llm, fmt)
        run_summary["qa_passed"] = qa.passed
        run_summary["stages"]["qa"] = {
            "passed": qa.passed,
            "checks": qa.checks,
            "score": qa.overall_score,
        }

        # Auto-regenerate up to 2× on failure
        regen_count = 0
        max_regen = settings["qa"]["max_auto_regenerations"]
        while not qa.passed and regen_count < max_regen:
            regen_count += 1
            log.warning(f"QA failed — auto-regenerating (attempt {regen_count}/{max_regen})")
            concepts = generate_concepts(llm, state, signals, selected_products, language)
            concept = select_best_concept(concepts) or concept
            copy = generate_copy(llm, concept, featured_product, language)
            qa = run_qa(copy, media_paths, featured_product, state, llm, fmt)

        # ── Stage 8: Save state + deliver ────────────────
        llm_summary = llm.usage_summary()
        run_summary["llm_provider"] = llm_summary["providers_used"][0] if llm_summary["providers_used"] else "offline"
        run_summary["tokens_used"] = llm_summary["total_tokens"]

      
        # Record posting history only after a successful real delivery.
        if not args.dry_run:
            if not qa.passed:
                log.error("QA failed — post will not be delivered or recorded")
                run_summary["error"] = "QA failed"
                return run_summary

            state.record_post({
                "pillar": concept.angle,
                "product": featured_product.handle if featured_product else None,
                "language": language,
                "hook": copy.hook,
                "qa_score": qa.overall_score,
                "feedback": None,
            })


        # Save run summary
        summary_path = output_dir / "run_summary.json"
        with open(summary_path, "w", encoding="utf-8") as f:
            json.dump(run_summary, f, indent=2, ensure_ascii=False)

        # Save caption text file
        caption_path = output_dir / "caption.txt"
        with open(caption_path, "w", encoding="utf-8") as f:
            from src.copywriter import format_telegram_caption
            f.write(format_telegram_caption(copy))

        log.info(f"Output saved to {output_dir}")

        # ── Telegram delivery ─────────────────────────────
        if args.dry_run:
            log.info("DRY RUN — skipping Telegram delivery")
            log.info(f"Generated files: {[p.name for p in media_paths]}")
        else:
            log.info("Stage 8: Delivering to Telegram...")
            from src.telegram_bot import deliver_batch
            deliver_batch(
                copy=copy,
                media_paths=media_paths,
                qa=qa,
                product=featured_product,
                state=state,
                run_meta={
                    **run_summary,
                    "audio_vibe": run_summary.get("audio_vibe", ""),
                    "tts_method": run_summary.get("tts_method", "silent"),
                },
            )

    except Exception as e:
        log.exception(f"Pipeline failed: {e}")
        run_summary["error"] = str(e)

        if not args.dry_run:
            _send_failure_alert(str(e))

    elapsed = time.monotonic() - t_start
    run_summary["elapsed_seconds"] = round(elapsed, 1)
    log.info(f"Run complete in {elapsed:.1f}s")
    return run_summary


def _send_failure_alert(error: str) -> None:
    """Send minimal failure alert to Telegram without depending on main code."""
    try:
        import os, requests
        token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
        chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
        if token and chat_id:
            requests.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": f"❌ GYODIFY engine failed today:\n`{error[:500]}`\n\nCheck GitHub Actions logs.",
                    "parse_mode": "Markdown",
                },
                timeout=10,
            )
    except Exception:
        pass


if __name__ == "__main__":
    args = parse_args()
    summary = run(args)
    if summary.get("error"):
        sys.exit(1)
