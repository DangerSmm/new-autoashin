import os
import re
import json
import asyncio
from aiohttp import web
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import FloodWaitError, SessionPasswordNeededError

# ================= CONFIGURATION =================
API_ID = int(os.getenv("API_ID", "36161575"))
API_HASH = os.getenv("API_HASH", "1c9b1de2d97d384d5422319bd7857992")

# Your Helper Bot Token and Admin ID
HELPER_BOT_TOKEN = os.getenv("HELPER_BOT_TOKEN", "8748528444:AAG5RI8TxErzT0JkqYndjSm9xbvcVkAp77s")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "8929445585"))

# Optional StringSession for persistent login
SESSION_STRING = os.getenv("SESSION_STRING", "")
PORT = int(os.getenv("PORT", "8080"))

CONFIG_FILE = "channels_config.json"
# =================================================

# Global dictionary mapping: { channel_id (int): target_bot (str) }
channels_map = {}

auth_state = {
    "step": "idle",
    "phone": None,
    "phone_code_hash": None,
}

user_client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
helper_bot = TelegramClient("helper_bot_session", API_ID, API_HASH)


def load_channels():
    global channels_map
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                data = json.load(f)
                channels_map = {int(k): v for k, v in data.items()}
                return
        except Exception as e:
            print(f"[!] Error loading config: {e}")
    # Default initial channel mapping
    channels_map = {-1002738233106: "@DesidarlingsBot"}
    save_channels()


def save_channels():
    with open(CONFIG_FILE, "w") as f:
        json.dump(channels_map, f, indent=4)


def extract_bot_and_payload(text: str):
    pattern = r"https?://t\.me/([a-zA-Z0-9_]+)\?start=([a-zA-Z0-9_\-]+)"
    match = re.search(pattern, text)
    if match:
        return match.group(1), match.group(2)
    return None, None


# ================= USERBOT DYNAMIC CHANNEL MONITOR =================
@user_client.on(events.NewMessage)
async def handle_channel_posts(event):
    # Only process if message is from a registered source channel
    if not event.chat_id or event.chat_id not in channels_map:
        return

    target_bot = channels_map[event.chat_id]
    message_text = event.raw_text or ""

    if event.buttons:
        for row in event.buttons:
            for button in row:
                if button.url:
                    message_text += f" {button.url}"

    source_bot, payload = extract_bot_and_payload(message_text)
    if not source_bot or not payload:
        return

    await helper_bot.send_message(
        ADMIN_USER_ID,
        f"🔔 **Link Detected!**\n"
        f"• **Channel:** `{event.chat_id}`\n"
        f"• **Source Bot:** `@{source_bot}`\n"
        f"• **Payload:** `{payload}`\n"
        f"• **Routing To:** `{target_bot}`"
    )

    try:
        collected_videos = []
        async with user_client.conversation(f"@{source_bot}", timeout=60) as conv:
            await conv.send_message(f"/start {payload}")

            start_time = asyncio.get_event_loop().time()
            while (asyncio.get_event_loop().time() - start_time) < 14:
                try:
                    response = await conv.get_response(timeout=3)
                    is_video = response.video or (
                        response.document
                        and response.file.mime_type
                        and "video" in response.file.mime_type
                    )
                    if is_video:
                        collected_videos.append(response)
                except asyncio.TimeoutError:
                    break

        if not collected_videos:
            await helper_bot.send_message(ADMIN_USER_ID, f"⚠️ No videos found from `@{source_bot}`.")
            return

        await helper_bot.send_message(ADMIN_USER_ID, f"📥 Retrieved {len(collected_videos)} video(s). Forwarding to {target_bot}...")

        # Send /upload to target bot
        await user_client.send_message(target_bot, "/upload")
        await asyncio.sleep(2)

        # Forward all retrieved videos
        for video_msg in collected_videos:
            await user_client.forward_messages(target_bot, video_msg)
            await asyncio.sleep(2)

        # Send completion command
        await user_client.send_message(target_bot, "✅ Done")
        await helper_bot.send_message(ADMIN_USER_ID, f"✅ Successfully delivered {len(collected_videos)} video(s) to {target_bot}!")

    except FloodWaitError as e:
        await helper_bot.send_message(ADMIN_USER_ID, f"⏳ Telegram FloodWait: Must pause for {e.seconds}s.")
    except Exception as e:
        await helper_bot.send_message(ADMIN_USER_ID, f"❌ Forwarding Error: {str(e)}")


# ================= HELPER BOT ADMIN & LOGIN COMMANDS =================
@helper_bot.on(events.NewMessage)
async def helper_admin_handler(event):
    if event.sender_id != ADMIN_USER_ID:
        return

    text = event.raw_text.strip()

    # /start handler
    if text == "/start":
        status = "Authorized (Running 24/7)" if await user_client.is_user_authorized() else "Not Authorized"
        msg = (
            f"🤖 **Forwarder Control Panel**\n"
            f"• **Userbot Status:** `{status}`\n"
            f"• **Active Channels:** `{len(channels_map)}`\n\n"
            f"**Commands:**\n"
            f"• `/addchannel <channel_id> <target_bot>` - Add channel pair\n"
            f"• `/delchannel <channel_id>` - Remove channel\n"
            f"• `/listchannels` - View all active mappings\n"
        )
        if not await user_client.is_user_authorized():
            auth_state["step"] = "waiting_phone"
            msg += "\n⚠️ Userbot is logged out. Send your phone number (+91...):"

        await event.reply(msg)
        return

    # /listchannels
    if text == "/listchannels":
        if not channels_map:
            await event.reply("No channels monitored currently.")
            return
        res = "📋 **Monitored Channel Mappings:**\n\n"
        for ch_id, t_bot in channels_map.items():
            res += f"• Channel: `{ch_id}` ➜ Target: `{t_bot}`\n"
        await event.reply(res)
        return

    # /addchannel <channel_id> <target_bot>
    if text.startswith("/addchannel"):
        parts = text.split()
        if len(parts) != 3:
            await event.reply("⚠️ Usage: `/addchannel <channel_id> <target_bot>`\nExample: `/addchannel -1002738233106 @DesidarlingsBot`")
            return
        try:
            ch_id = int(parts[1])
            t_bot = parts[2] if parts[2].startswith("@") else f"@{parts[2]}"
            channels_map[ch_id] = t_bot
            save_channels()
            await event.reply(f"✅ Channel `{ch_id}` linked successfully to `{t_bot}`!")
        except ValueError:
            await event.reply("❌ Invalid Channel ID. It must be numeric (e.g., `-100...`).")
        return

    # /delchannel <channel_id>
    if text.startswith("/delchannel"):
        parts = text.split()
        if len(parts) != 2:
            await event.reply("⚠️ Usage: `/delchannel <channel_id>`")
            return
        try:
            ch_id = int(parts[1])
            if ch_id in channels_map:
                del channels_map[ch_id]
                save_channels()
                await event.reply(f"✅ Channel `{ch_id}` removed successfully!")
            else:
                await event.reply("⚠️ Channel ID not found in active list.")
        except ValueError:
            await event.reply("❌ Channel ID must be an integer.")
        return

    # Userbot Authentication Flow
    if auth_state["step"] == "waiting_phone":
        phone = re.sub(r"[^\d+]", "", text)
        auth_state["phone"] = phone
        try:
            res = await user_client.send_code_request(phone)
            auth_state["phone_code_hash"] = res.phone_code_hash
            auth_state["step"] = "waiting_code"
            await event.reply("📩 Code sent! Send it with words to avoid ban (e.g., `HELLO 12345`):")
        except Exception as e:
            await event.reply(f"❌ Failed to request code: {e}")
        return

    if auth_state["step"] == "waiting_code":
        digits = "".join(re.findall(r"\d+", text))
        if not digits:
            await event.reply("❌ No digits detected. Send like: `HELLO 12345`")
            return

        try:
            await user_client.sign_in(
                phone=auth_state["phone"],
                code=digits,
                phone_code_hash=auth_state["phone_code_hash"]
            )
            auth_state["step"] = "idle"
            sess_str = user_client.session.save()
            await event.reply(
                f"✅ **Login Successful!**\n\n"
                f"Save this `SESSION_STRING` into your Koyeb Environment Variables:\n\n"
                f"`{sess_str}`"
            )
        except SessionPasswordNeededError:
            auth_state["step"] = "waiting_2fa"
            await event.reply("🔐 2FA Password required. Send your 2FA password:")
        except Exception as e:
            await event.reply(f"❌ Verification failed: {e}\nSend the code again:")
        return

    if auth_state["step"] == "waiting_2fa":
        try:
            await user_client.sign_in(password=text)
            auth_state["step"] = "idle"
            sess_str = user_client.session.save()
            await event.reply(
                f"✅ **Login Successful!**\n\n"
                f"Save this `SESSION_STRING` into Koyeb Environment Variables:\n\n"
                f"`{sess_str}`"
            )
        except Exception as e:
            await event.reply(f"❌ 2FA Password incorrect: {e}")
        return


# ================= HEALTH CHECK FOR KOYEB =================
async def health_check(request):
    return web.Response(text="Userbot is alive and running 24/7!")


async def start_web_server():
    app = web.Application()
    app.router.add_get("/", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()


# ================= MAIN RUNNER =================
async def main():
    load_channels()

    # Start health check server for Koyeb free tier
    await start_web_server()

    # Start helper bot
    await helper_bot.start(bot_token=HELPER_BOT_TOKEN)
    print("[✔] Helper Bot is online.")

    # Connect user account
    await user_client.connect()

    if not await user_client.is_user_authorized():
        await helper_bot.send_message(
            ADMIN_USER_ID,
            "⚠️ **Userbot Not Logged In**\nSend `/start` to begin logging in."
        )
    else:
        await helper_bot.send_message(
            ADMIN_USER_ID,
            f"🚀 **Userbot Active!**\nMonitoring `{len(channels_map)}` channel(s)."
        )

    await asyncio.gather(
        user_client.run_until_disconnected(),
        helper_bot.run_until_disconnected()
    )


if __name__ == "__main__":
    asyncio.run(main())