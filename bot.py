
import os
import json
import time
import urllib.request
import urllib.parse
import urllib.error

BOT_TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

API_URLS = {
    "CTO": "https://api.dexscreener.com/community-takeovers/latest/v1",
    "TOKEN": "https://api.dexscreener.com/token-profiles/latest/v1",
    "ADS": "https://api.dexscreener.com/ads/latest/v1",
}

STATE_FILE = "seen_events.json"


def get_json(url):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Solana-Alert-Bot/1.0"}
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def send_message(message):
    url = (
        f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    )
    data = urllib.parse.urlencode({
        "chat_id": CHAT_ID,
        "text": message,
        "disable_web_page_preview": "true",
    }).encode("utf-8")

    request = urllib.request.Request(url, data=data)
    with urllib.request.urlopen(request, timeout=20) as response:
        result = json.loads(response.read().decode("utf-8"))

    if not result.get("ok"):
        raise RuntimeError("Telegram message failed")


def load_seen():
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as file:
            return set(json.load(file))
    except (OSError, ValueError):
        return set()


def save_seen(seen):
    with open(STATE_FILE, "w", encoding="utf-8") as file:
        json.dump(sorted(seen), file)


def main():
    seen = load_seen()

    # First run: record existing events to avoid flooding the channel.
    first_run = not seen
    found = []

    for event_type, url in API_URLS.items():
        try:
            data = get_json(url)
            if not isinstance(data, list):
                continue

            for item in data:
                if item.get("chainId") != "solana":
                    continue

                address = item.get("tokenAddress", "")
                if not address:
                    continue

                event_id = (
                    f"{event_type}:{address}:"
                    f"{item.get('date', '')}"
                )

                if event_id in seen:
                    continue

                seen.add(event_id)

                if not first_run:
                    found.append((event_type, item, address))

        except Exception as error:
            print(f"{event_type} API error: {error}")

        time.sleep(1)

    for event_type, item, address in found:
        name = item.get("description") or item.get("symbol") or "Unknown"
        links = item.get("links", [])
        socials = []

        for link in links:
            if isinstance(link, dict) and link.get("url"):
                socials.append(link["url"])

        message = (
            f"🔔 Solana {event_type} Alert\n\n"
            f"🪙 {name}\n"
            f"📍 {address}\n"
            f"🔗 https://dexscreener.com/solana/{address}"
        )

        if socials:
            message += "\n\n" + "\n".join(socials[:5])

        try:
            send_message(message)
            print(f"Sent {event_type}: {address}")
        except Exception as error:
            print(f"Telegram send error: {error}")

    save_seen(seen)
    print(f"Finished. New events: {len(found)}")



if __name__ == "__main__":
    if os.environ.get("TEST_TELEGRAM") == "1":
        send_message(
            "✅ Solana Alert Bot 测试成功！\n"
            "Telegram 推送正常，机器人已连接到你的私密频道。"
        )
        print("Telegram test message sent.")
    else:
        main()

