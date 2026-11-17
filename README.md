# GYODIFY Content Engine

Automated daily Instagram content pipeline for [@gyodify](https://instagram.com/gyodify).

Every morning at **7:00 AM IST**, the engine:
1. Syncs your Shopify catalogue
2. Researches trending topics (Google Trends, News, Reddit, festival calendar)
3. Generates concepts via LLM (Gemini → Groq fallback)
4. Writes captions, hashtags, alt text in the right language
5. Renders branded images/carousels/reels using the locked 6-position feed palette
6. Delivers everything to your Telegram — ready to post in under 10 minutes

**Cost: ₹0/month. No auto-posting. You review and post manually.**

---

## Feed Sequence (locked forever)

| Position | Format   | Template         | Palette            |
|----------|----------|------------------|--------------------|
| 1        | Carousel | carousel_cream   | Warm cream/terracotta |
| 2        | Reel     | reel_sage        | Deep sage/coral    |
| 3        | Post     | post_dusty       | Dusty warm/oxblood |
| 4        | Carousel | carousel_peach   | Peach/warm coral   |
| 5        | Reel     | reel_midnight    | Midnight/sage/gold |
| 6        | Post     | post_espresso    | Espresso/coral     |

Then repeats. Every row of 3 in your Instagram grid = Carousel → Reel → Post.

---

## Quick Setup (30 minutes)

### 1. Create GitHub repo
- Go to github.com → New repository → name it `gyodify-content-engine`
- Upload all files from this zip
- Enable Actions if prompted

### 2. Get API keys (all free)
| Key | Where |
|-----|-------|
| `GEMINI_API_KEY` | [aistudio.google.com](https://aistudio.google.com) → Get API key |
| `GROQ_API_KEY` | [console.groq.com](https://console.groq.com) → API Keys |
| `TELEGRAM_BOT_TOKEN` | Telegram → @BotFather → /newbot |
| `TELEGRAM_CHAT_ID` | Run `python scripts/get_chat_id.py --token YOUR_TOKEN` |

### 3. Add GitHub Secrets
Repo → Settings → Secrets and variables → Actions → New repository secret
Add all 4 keys above (names must match exactly, case-sensitive).

### 4. Test run (dry)
Actions → GYODIFY Daily Content Engine → Run workflow → set `dry_run = true`

### 5. First real run
Actions → Run workflow → leave dry_run blank → check Telegram in 15 min

---

## Daily Routine (~8 minutes)

| Time | Action |
|------|--------|
| 7:00 AM | Open Telegram — read brief (position, format, language, QA flags) |
| 7:02 AM | Preview image/carousel slides |
| 7:04 AM | Long-press caption block → Copy |
| 7:05 AM | Instagram → Upload → Paste caption → Add audio (reels) → Post |
| 7:08 AM | Tap 👍 feedback button → position counter advances |

Log performance: `/stats 2026-11-14 likes=84 saves=22 reach=2100`

---

## Customising (no code required)

All customisation is in `config/` — edit directly in GitHub (pencil icon → edit → commit):

| File | What to edit |
|------|-------------|
| `config/festivals.yaml` | Verify Indian festival dates yearly. Run `python scripts/check_festivals.py` |
| `config/blocklist.yaml` | Add brands/topics to never reference |
| `config/voice_examples.yaml` | Add your own golden captions per language |
| `config/settings.yaml` | Language weights, `exclude_kids`, hashtag counts |
| `config/themes.yaml` | Add evergreen relatable meme premises |

### Enable kids' line
In `config/settings.yaml`, change `exclude_kids: true` to `exclude_kids: false`.

---

## Manual workflow triggers

Actions → GYODIFY Daily Content Engine → Run workflow:

| Input | Values | Effect |
|-------|--------|--------|
| `dry_run` | `true` | Generate but don't send to Telegram |
| `language` | `hinglish/english/hindi/marathi` | Force a language |
| `format` | `carousel/reel/post` | Force a format |
| `position` | `1-6` | Force feed position |
| `date` | `YYYY-MM-DD` | Simulate a date (for festival testing) |

---

## Troubleshooting

| Problem | Fix |
|---------|-----|
| Workflow fails immediately | Check all 4 GitHub secrets are set with exact names |
| Nothing arrives in Telegram | Send a message to your bot first; verify TELEGRAM_CHAT_ID |
| Reddit 403/429 | Expected — GitHub runners are blocked by Reddit. Engine falls back to Google Trends + themes |
| Devanagari shows boxes | Check `assets/fonts/NotoSansDevanagari-Bold.ttf` is uploaded |
| QA gate keeps rejecting | Check `output/DATE/run_summary.json` → enable rembg in settings.yaml |
| Position counter wrong | Edit `data/history.json` → change `current_position` to 1-6 → commit |
| Reel not generating | Check ffmpeg is installed (the workflow does this automatically via apt) |

---

## Architecture

```
GitHub Actions cron (01:30 UTC = 07:00 IST)
  ↓
Stage 0: Poll Telegram feedback → advance position if 👍/😐
Stage 1: Shopify sync → products, images, kids filter
Stage 2: Research → Trends + News + Reddit + Calendar
Stage 3: LLM ideation → 5 concepts, filtered, scored
Stage 4: LLM copywriting → hook, caption, hashtags, script
Stage 5: Render → HTML/CSS → PNG via Playwright
Stage 6: QA gate → 8 automated checks, auto-retry
Stage 7: Deliver → Telegram (brief + media + caption + notes)
Stage 8: Commit history.json → state persists
```

LLM fallback chain: **Gemini 1.5 Flash → Groq Llama 3 → OpenRouter → Offline templates**

---

## File structure

```
gyodify-content-engine/
├── .github/workflows/daily.yml     ← the automation
├── config/                          ← edit these, no code needed
├── src/                             ← Python engine
├── templates/                       ← HTML/CSS post templates
├── prompts/                         ← LLM prompt files
├── scripts/                         ← helper scripts
├── tests/                           ← pytest test suite
├── data/history.json                ← position counter
└── assets/fonts/                    ← font files (include these!)
```

---

## Running locally

```bash
# Install
pip install -r requirements.txt
playwright install chromium

# Copy env
cp .env.example .env
# Fill in your keys in .env

# Dry run
python -m src.main --dry-run

# Check upcoming festivals
python scripts/check_festivals.py

# Run tests
pytest tests/ -v
```

---

## Weekly insights

Every Monday, a separate workflow sends a performance summary to Telegram showing top-performing pillars, formats, and languages over the past 30 days.

Log metrics with `/stats YYYY-MM-DD likes=N saves=N reach=N replies=N` after each post to unlock deeper learning.
