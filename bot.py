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

                # First successful check:
                # record old events without sending old alerts.
                if event_type not in initialized_sources:
                    seen.add(event_id)
                    continue

                message = make_message(
                    event_type, item, address
                )

                try:
                    send_message(message)

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


# ---------------- Paper trading ----------------

PAPER_ENABLED = os.getenv("PAPER_TRADING", "1") == "1"

PAPER_FILE = "paper_trades.json"
PAPER_SEEN_FILE = "paper_seen.json"

PAPER_CONFIG = {
    "market_cap_min": 50_000,
    "market_cap_max": 500_000,

    "liquidity_min": 20_000,

    "pool_age_min_minutes": 5,
    "pool_age_max_minutes": 60,

    "min_h1_volume": 5_000,
    "min_h1_transactions": 20,

    "take_profit_pct": 10.0,
    "stop_loss_pct": -8.0,

    "max_hold_minutes": 30,

    "stake_sol": 0.1,

    # Estimated round-trip costs, not a real quote.
    "round_trip_cost_pct": 1.5,

    "max_new_candidates_per_cycle": 15,
    "max_open_positions": 3,

    "candidate_retry_minutes": 10,
}


def load_json_file(filename, default):
    try:
        with open(filename, "r", encoding="utf-8") as file:
            return json.load(file)
    except (OSError, ValueError, TypeError):
        return default


def save_json_file(filename, value):
    temporary_file = filename + ".tmp"

    with open(temporary_file, "w", encoding="utf-8") as file:
        json.dump(
            value,
            file,
            ensure_ascii=False,
            indent=2
        )

    os.replace(temporary_file, filename)


def number(value, default=0.0):
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def get_token_pair(token_address):
    url = (
        "https://api.dexscreener.com/token-pairs/v1/"
        f"solana/{urllib.parse.quote(token_address)}"
    )

    data = get_json(url)

    if not isinstance(data, list):
        return None

    candidates = [
        pair for pair in data
        if isinstance(pair, dict)
        and pair.get("chainId") == "solana"
        and (pair.get("baseToken") or {}).get("address")
        == token_address
        and number(
            (pair.get("liquidity") or {}).get("usd")
        ) > 0
        and number(pair.get("priceUsd")) > 0
    ]

    if not candidates:
        return None

    return max(
        candidates,
        key=lambda pair: number(
            (pair.get("liquidity") or {}).get("usd")
        )
    )


def paper_filter(pair):
    """Return accepted, reason and metrics."""

    now_ms = time.time() * 1000

    created_ms = number(pair.get("pairCreatedAt"))

    market_cap = number(
        pair.get("marketCap") or pair.get("fdv")
    )

    liquidity = number(
        (pair.get("liquidity") or {}).get("usd")
    )

    price = number(pair.get("priceUsd"))

    h1_volume = number(
        (pair.get("volume") or {}).get("h1")
    )

    h1_tx = (pair.get("txns") or {}).get("h1") or {}

    h1_transactions = (
        number(h1_tx.get("buys"))
        + number(h1_tx.get("sells"))
    )

    if not created_ms or not market_cap or not liquidity or not price:
        return False, "关键行情数据缺失", {}

    age_minutes = (now_ms - created_ms) / 60000

    metrics = {
        "market_cap": market_cap,
        "liquidity": liquidity,
        "price": price,
        "age_minutes": age_minutes,
        "h1_volume": h1_volume,
        "h1_transactions": h1_transactions,
    }

    cfg = PAPER_CONFIG

    if not (
        cfg["market_cap_min"]
        <= market_cap
        <= cfg["market_cap_max"]
    ):
        return False, "市值不在设定区间", metrics

    if liquidity < cfg["liquidity_min"]:
        return False, "流动性不足", metrics

    if not (
        cfg["pool_age_min_minutes"]
        <= age_minutes
        <= cfg["pool_age_max_minutes"]
    ):
        return False, "池龄不在设定区间", metrics

    if h1_volume < cfg["min_h1_volume"]:
        return False, "1小时成交额不足", metrics

    if h1_transactions < cfg["min_h1_transactions"]:
        return False, "1小时交易笔数不足", metrics

    return (
        True,
        "通过行情筛选（持仓集中度尚未验证）",
        metrics
    )


def paper_message_open(token, pair, metrics):
    base = pair.get("baseToken") or {}

    return (
        "🧪 模拟买入（非真实交易）\n\n"
        f"🪙 {base.get('symbol') or base.get('name') or 'Unknown'}\n"
        f"📍 {token}\n"
        f"💵 模拟投入：{PAPER_CONFIG['stake_sol']} SOL\n"
        f"市值：${metrics['market_cap']:,.0f}\n"
        f"流动性：${metrics['liquidity']:,.0f}\n"
        f"池龄：{metrics['age_minutes']:.1f} 分钟\n"
        f"1小时成交额：${metrics['h1_volume']:,.0f}\n"
        f"1小时交易笔数：{metrics['h1_transactions']:.0f}\n"
        "⚠️ Top10 / DEV / 捆绑钱包比例未验证\n"
        f"止盈：+{PAPER_CONFIG['take_profit_pct']}% | "
        f"止损：{PAPER_CONFIG['stop_loss_pct']}%\n"
        f"⏱ 最长持仓：{PAPER_CONFIG['max_hold_minutes']} 分钟\n"
        f"🔗 https://dexscreener.com/solana/{token}"
    )


def paper_open_new_positions():
    trades = load_json_file(PAPER_FILE, [])

    # Store last-check timestamps so rejected tokens
    # can be reconsidered later.
    checked = load_json_file(PAPER_SEEN_FILE, {})

    if not isinstance(checked, dict):
        checked = {}

    try:
        profiles = get_json(API_URLS["TOKEN"])
    except Exception as error:
        print(f"Paper discovery error: {error}")
        return

    if not isinstance(profiles, list):
        return

    opened = 0

    open_count = sum(
        1 for trade in trades
        if trade.get("status") == "open"
    )

    if open_count >= PAPER_CONFIG["max_open_positions"]:
        return

    for item in profiles:

        if opened >= PAPER_CONFIG["max_new_candidates_per_cycle"]:
            break

        if (
            not isinstance(item, dict)
            or item.get("chainId") != "solana"
        ):
            continue

        token = item.get("tokenAddress")

        if not token:
            continue

        last_checked = number(checked.get(token))

        if (
            time.time() - last_checked
            < PAPER_CONFIG["candidate_retry_minutes"] * 60
        ):
            continue

        if (
            open_count + opened
            >= PAPER_CONFIG["max_open_positions"]
        ):
            break

        # Allow rejected tokens to be checked again later.
        checked[token] = time.time()

        try:
            pair = get_token_pair(token)

            if not pair:
                continue

            accepted, reason, metrics = paper_filter(pair)

            if not accepted:
                print(f"Paper skip {token}: {reason}")
                continue

            trade = {
                "token": token,

                "symbol": (
                    pair.get("baseToken") or {}
                ).get("symbol", "Unknown"),

                "pair_address": pair.get("pairAddress"),

                "entry_price": metrics["price"],

                "entry_time": time.time(),

                "last_price": metrics["price"],

                "status": "open",

                "stake_sol": PAPER_CONFIG["stake_sol"],
            }

            trades.append(trade)

            opened += 1
            open_count += 1

            save_json_file(PAPER_FILE, trades)

            send_message(
                paper_message_open(token, pair, metrics)
            )

            print(f"Paper position opened: {token}")

        except Exception as error:
            print(f"Paper candidate error {token}: {error}")

        time.sleep(0.2)

    save_json_file(PAPER_SEEN_FILE, checked)


def paper_update_positions():
    trades = load_json_file(PAPER_FILE, [])

    changed = False

    now = time.time()

    for trade in trades:

        if trade.get("status") != "open":
            continue

        try:
            pair = get_token_pair(trade["token"])

            if not pair:
                continue

            current_price = number(pair.get("priceUsd"))

            entry_price = number(
                trade.get("entry_price")
            )

            if current_price <= 0 or entry_price <= 0:
                continue

            trade["last_price"] = current_price

            gross_pct = (
                current_price / entry_price - 1
            ) * 100

            held_minutes = (
                now - number(trade.get("entry_time"))
            ) / 60

            reason = None

            if gross_pct >= PAPER_CONFIG["take_profit_pct"]:
                reason = "止盈触发"

            elif gross_pct <= PAPER_CONFIG["stop_loss_pct"]:
                reason = "止损触发"

            elif held_minutes >= PAPER_CONFIG["max_hold_minutes"]:
                reason = "持仓超时"

            changed = True

            if reason:

                cost_pct = PAPER_CONFIG[
                    "round_trip_cost_pct"
                ]

                net_pct = gross_pct - cost_pct

                net_sol = (
                    trade["stake_sol"] * net_pct / 100
                )

                trade.update({
                    "status": "closed",
                    "exit_price": current_price,
                    "exit_time": now,
                    "gross_pct": gross_pct,
                    "net_pct": net_pct,
                    "net_sol": net_sol,
                    "exit_reason": reason,
                })

                save_json_file(PAPER_FILE, trades)

                send_message(
                    "📊 模拟交易结束\n\n"
                    f"🪙 {trade.get('symbol', 'Unknown')}\n"
                    f"📍 {trade['token']}\n"
                    f"原因：{reason}\n"
                    f"持仓：{held_minutes:.1f} 分钟\n"
                    f"毛收益：{gross_pct:+.2f}%\n"
                    f"预估成本：-{cost_pct:.2f}%\n"
                    f"净收益：{net_pct:+.2f}% "
                    f"({net_sol:+.5f} SOL)\n"
                    "⚠️ 按轮询时观察到的价格估算，"
                    "不代表真实可成交价格。"
                )

                print(
                    f"Paper position closed: "
                    f"{trade['token']} {net_pct:+.2f}%"
                )

        except Exception as error:
            print(
                f"Paper update error "
                f"{trade.get('token')}: {error}"
            )

        time.sleep(0.2)

    if changed:
        save_json_file(PAPER_FILE, trades)


def paper_send_daily_stats():
    trades = load_json_file(PAPER_FILE, [])

    closed = [
        t for t in trades
        if t.get("status") == "closed"
    ]

    if not closed:
        return

    wins = [
        t for t in closed
        if number(t.get("net_pct")) > 0
    ]

    total_net = sum(
        number(t.get("net_sol"))
        for t in closed
    )

    win_rate = len(wins) / len(closed) * 100

    avg_win = (
        sum(number(t.get("net_pct")) for t in wins)
        / len(wins)
        if wins else 0
    )

    losses = [
        t for t in closed
        if number(t.get("net_pct")) <= 0
    ]

    avg_loss = (
        sum(number(t.get("net_pct")) for t in losses)
        / len(losses)
        if losses else 0
    )

    message = (
        "📈 模拟交易统计（累计）\n\n"
        f"已结束交易：{len(closed)} 笔\n"
        f"胜率：{win_rate:.1f}%\n"
        f"累计净收益：{total_net:+.5f} SOL\n"
        f"平均盈利：{avg_win:+.2f}%\n"
        f"平均亏损：{avg_loss:+.2f}%\n"
        f"当前持仓："
        f"{sum(1 for t in trades if t.get('status') == 'open')} 笔\n\n"
        "说明：统计基于轮询价格和估算成本，"
        "不是实盘收益。"
    )

    send_message(message)


def paper_cycle():
    if not PAPER_ENABLED:
        return

    paper_update_positions()

    paper_open_new_positions()

    # One cumulative report per calendar day (UTC).
    today = time.strftime("%Y-%m-%d", time.gmtime())

    stats_state = load_json_file(
        "paper_stats_date.json",
        {}
    )

    if stats_state.get("last_sent") != today:

        paper_send_daily_stats()

        save_json_file(
            "paper_stats_date.json",
            {"last_sent": today}
        )


def main():
    """Run one monitoring cycle, then exit for GitHub Actions."""
    if not BOT_TOKEN or not CHAT_ID:
        raise RuntimeError(
            "Missing TELEGRAM_BOT_TOKEN or TELEGRAM_CHAT_ID"
        )

    seen = load_set(STATE_FILE)
    initialized_sources = load_set(SOURCE_FILE)

    # Existing event state means the bot has already been initialized.
    if seen and not initialized_sources:
        initialized_sources.update(API_URLS.keys())

    print("Solana Alert Bot: starting one cycle.")

    try:
        monitor_once(seen, initialized_sources)
        paper_cycle()
    except Exception as error:
        print(f"Monitoring cycle error: {error}")
        raise

    print("Solana Alert Bot: cycle completed; exiting.")


if __name__ == "__main__":

    if os.getenv("TEST_TELEGRAM") == "1":

        if not BOT_TOKEN or not CHAT_ID:
            raise RuntimeError(
                "Telegram credentials are missing"
            )

        send_message(
            "✅ Solana Alert Bot 测试成功！\n"
            "Telegram 推送正常，机器人已连接到你的私密频道。"
        )

        print("Telegram test message sent.")

    else:
        main()
