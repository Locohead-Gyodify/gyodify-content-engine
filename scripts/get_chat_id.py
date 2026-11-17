"""
get_chat_id.py — Helper to find your Telegram chat ID after messaging your bot.

Usage:
  python scripts/get_chat_id.py --token 7123456789:AAFxyz...
"""
import argparse
import requests

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--token", required=True, help="Your bot token from @BotFather")
    args = parser.parse_args()

    url = f"https://api.telegram.org/bot{args.token}/getUpdates"
    resp = requests.get(url, timeout=10)
    data = resp.json()

    if not data.get("ok"):
        print(f"Error: {data.get('description')}")
        print("Make sure you've sent at least one message to your bot first.")
        return

    updates = data.get("result", [])
    if not updates:
        print("No messages found.")
        print("→ Open your bot in Telegram and send it any message, then run this script again.")
        return

    seen = set()
    for update in updates:
        msg = update.get("message") or update.get("channel_post") or {}
        chat = msg.get("chat", {})
        chat_id = chat.get("id")
        chat_title = chat.get("title") or chat.get("first_name") or "Unknown"
        chat_type = chat.get("type", "?")
        if chat_id and chat_id not in seen:
            seen.add(chat_id)
            print(f"\nChat found!")
            print(f"  Title:   {chat_title}")
            print(f"  Type:    {chat_type}")
            print(f"  Chat ID: {chat_id}  ← use this as TELEGRAM_CHAT_ID")

    if not seen:
        print("No chats found in updates. Send a message to your bot first.")

if __name__ == "__main__":
    main()
