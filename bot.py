
import os
import json
import time
import urllib.request
import urllib.parse
import urllib.error

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

POLL_INTERVAL = 60
STATE_FILE = "seen_events.json"
SOURCE_FILE = "initialized_sources.json"

API_URLS = {
    "CTO": "https://api.dexscreener.com/community-takeovers/latest/v1",
    "TOKEN": "https://api.dexscreener.com/token-profiles/latest/v1",
    "ADS": "https://api.dexscreener.com/ads/latest/v1",
}


def get_json(url):
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Solana-Alert-Bot/2.0"}
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def send_message(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

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


def load_set(filename):
    try:
        with open(filename, "r", encoding="utf-8") as file:
            return set(json.load(file))
    except (OSError, ValueError, TypeError):
        return set()


def save_set(filename, values):
    temporary_file = filename + ".tmp"

    with open(temporary_file, "w", encoding="utf-8") as file:
        json.dump(sorted(values), file)

    os.replace(temporary_file, filename)


def make_event_id(event_type, item):
    address = item.get("tokenAddress", "")
    date = item.get("date", "")

    if not address:
        return None

    return f"{event_type}:{address}:{date}"


def make_message(event_type, item, address):
    name = (
        item.get("description")
        or item.get("symbol")
        or "Unknown"
    )

    message = (
        f"🔔 Solana {event_type} Alert\n\n"
        f"🪙 {name}\n"
        f"📍 {address}\n"
        f"🔗 https://dexscreener.com/solana/{address}"
    )

    socials = []

    for link in item.get("links", []):
        if isinstance(link, dict) and link.get("url"):
            socials.append(link["url"])

    if socials:
        message += "\n\n" + "\n".join(socials[:5])

    return message


def monitor_once(seen, initialized_sources):
    for event_type, url in API_URLS.items():
        try:
            data = get_json(url)

            if not isinstance(data, list):
                raise ValueError("Unexpected API response")

            new_count = 0

            for item in data:
                if not isinstance(item, dict):
                    continue

                if item.get("chainId") != "solana":
                    continue

                address = item.get("tokenAddress", "")
                event_id = make_event_id(event_type, item)

                if not address or not event_id:
                    continue

                if event_id in seen:
                    continue

                # First successful check for this source:
                # record existing events without sending old alerts.
                if event_type not in initialized_sources:
                    seen.add(event_id)
                    continue

                message = make_message(
                    event_type, item, address
                )

                try:
                    send_message(message)

                    # Only mark an event as seen after successful delivery.
                    seen.add(event_id)
                    new_count += 1

                    save_set(STATE_FILE, seen)

                    print(f"Sent {event_type}: {address}")

                except Exception as error:
                    print(
                        f"Telegram error for {address}: {error}"
                    )

            initialized_sources.add(event_type)

            save_set(STATE_FILE, seen)
            save_set(SOURCE_FILE, initialized_sources)

            print(
                f"{event_type} checked. "
                f"New messages sent: {new_count}"
            )

        except Exception as error:
            print(f"{event_type} API error: {error}")

        time.sleep(1)


def main():
    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError(
            "Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID"
        )

    seen = load_set(STATE_FILE)
    initialized_sources = load_set(SOURCE_FILE)

    # Existing GitHub state means the bot has already been initialized.
    if seen and not initialized_sources:
        initialized_sources.update(API_URLS.keys())

    print("Solana Alert Bot started.")
    print(f"Monitoring every {POLL_INTERVAL} seconds.")

    while True:
        cycle_start = time.monotonic()

        try:
            monitor_once(seen, initialized_sources)
        except Exception as error:
            print(f"Monitoring error: {error}")

        elapsed = time.monotonic() - cycle_start
        sleep_time = max(5, POLL_INTERVAL - elapsed)

        print(f"Next check in {sleep_time:.0f} seconds.")
        time.sleep(sleep_time)


if __name__ == "__main__":
    if os.getenv("TEST_TELEGRAM") == "1":
        if not BOT_TOKEN or not CHAT_ID:
            raise RuntimeError("Telegram credentials are missing")

        send_message(
            "✅ Solana Alert Bot 测试成功！\n"
            "Telegram 推送正常，机器人已连接到你的私密频道。"
        )
        print("Telegram test message sent.")
    else:
        main()
