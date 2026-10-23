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

HELPER_BOT_TOKEN = os.getenv("HELPER_BOT_TOKEN", "8748528444:AAG5RI8TxErzT0JkqYndjSm9xbvcVkAp77s")
ADMIN_USER_ID = int(os.getenv("ADMIN_USER_ID", "8929445585"))

# Load session string from env if available
SESSION_STRING = os.getenv("SESSION_STRING", "")

# Render dynamically sets PORT (defaults to 10000 on Render if unset)
PORT = int(os.getenv("PORT", "10000"))

CONFIG_FILE = "channels_config.json"
# =================================================

channels_map = {}

auth_state = {
    "step": "idle",
    "phone": None,
    "phone_code_hash": None,
}

# Explicit clients
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
    channels_map = {-1002738233106: "@DesidarlingsBot"}
    save_channels()


def save_channels():
    try:
        with open(CONFIG_FILE, "w") as f:
            json.dump(channels_map, f, indent=4)
    except Exception as e:
        print(f"[!] Failed to save channels config: {e}")


def extract_bot_and_payload(text: str):
    pattern = r"https?://t\.me/([a-zA-Z0-9_]+)\?start=([a-zA-Z0-9_\-]+)"
    match = re.search(pattern, text)
    if match:
        return match.group(1), match.group(2)
    return None, None


# ================= USERBOT LOGIC =================
@user_client.on(events.NewMessage)
async def handle_channel_posts(event):
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

    try:
        await helper_bot.send_message(
            ADMIN_USER_ID,
            f"🔔 **Link Detected!**\n"
            f"• **Channel:** `{event.chat_id}`\n"
            f"• **Source Bot:** `@{source_bot}`\n"
            f"• **Payload:** `{payload}`\n"
            f"• **Routing To:** `{target_bot}`"
        )
    except Exception:
        pass

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

        await user_client.send_message(target_bot, "/upload")
        await asyncio.sleep(2)

        for video_msg in collected_videos:
            await user_client.forward_messages(target_bot, video_msg)
            await asyncio.sleep(2)

        await user_client.send_message(target_bot, "✅ Done")
        await helper_bot.send_message(ADMIN_USER_ID, f"✅ Successfully delivered {len(collected_videos)} video(s) to {target_bot}!")

    except FloodWaitError as e:
        await helper_bot.send_message(ADMIN_USER_ID, f"⏳ Rate limit: Wait {e.seconds}s.")
    except Exception as e:
        await helper_bot.send_message(ADMIN_USER_ID, f"❌ Forwarding Error: {str(e)}")


# ================= HELPER BOT ADMIN & LOGIN =================
@helper_bot.on(events.NewMessage)
async def helper_admin_handler(event):
    if event.sender_id != ADMIN_USER_ID:
        return

    text = event.raw_text.strip()

    if text == "/start":
        is_auth = await user_client.is_user_authorized()
        status = "Authorized (Running 24/7)" if is_auth else "Not Authorized"
        msg = (
            f"🤖 **Forwarder Control Panel**\n"
            f"• **Userbot Status:** `{status}`\n"
            f"• **Active Channels:** `{len(channels_map)}`\n\n"
            f"**Commands:**\n"
            f"• `/addchannel <channel_id> <target_bot>`\n"
            f"• `/delchannel <channel_id>`\n"
            f"• `/listchannels`\n"
        )
        if not is_auth:
            auth_state["step"] = "waiting_phone"
            msg += "\n⚠️ Userbot is logged out. Please send your phone number (e.g., `+919398965872`):"

        await event.reply(msg)
        return

    if text == "/listchannels":
        if not channels_map:
            await event.reply("No channels monitored currently.")
            return
        res = "📋 **Monitored Channel Mappings:**\n\n"
        for ch_id, t_bot in channels_map.items():
            res += f"• Channel: `{ch_id}` ➜ Target: `{t_bot}`\n"
        await event.reply(res)
        return

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
            await event.reply(f"✅ Channel `{ch_id}` linked to `{t_bot}`!")
        except ValueError:
            await event.reply("❌ Invalid Channel ID format.")
        return

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
                await event.reply("⚠️ Channel ID not found.")
        except ValueError:
            await event.reply("❌ Channel ID must be an integer.")
        return

    # Authentication state machine
    if auth_state["step"] == "waiting_phone":
        phone = re.sub(r"[^\d+]", "", text)
        auth_state["phone"] = phone
        try:
            res = await user_client.send_code_request(phone)
            auth_state["phone_code_hash"] = res.phone_code_hash
            auth_state["step"] = "waiting_code"
            await event.reply("📩 Code requested! Send it with text to bypass filter (e.g., `HELLO 12345`):")
        except Exception as e:
            await event.reply(f"❌ Failed to send code request: {e}")
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
                f"Copy and paste this `SESSION_STRING` into your Render Environment Variables:\n\n"
                f"`{sess_str}`"
            )
        except SessionPasswordNeededError:
            auth_state["step"] = "waiting_2fa"
            await event.reply("🔐 2FA Password required. Send your 2FA password:")
        except Exception as e:
            await event.reply(f"❌ Verification failed: {e}\nSend code again:")
        return

    if auth_state["step"] == "waiting_2fa":
        try:
            await user_client.sign_in(password=text)
            auth_state["step"] = "idle"
            sess_str = user_client.session.save()
            await event.reply(
                f"✅ **Login Successful!**\n\n"
                f"Copy and paste this `SESSION_STRING` into Render Environment Variables:\n\n"
                f"`{sess_str}`"
            )
        except Exception as e:
            await event.reply(f"❌ 2FA Password incorrect: {e}")
        return


# ================= AIOHTTP HEALTH CHECK SERVER =================
async def health_check(request):
    return web.Response(text="Bot is running active 24/7 on Render!")


async def start_web_server():
    app = web.Application()
    app.router.add_get("/", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"[✔] Web server active on port {PORT}")


# ================= MAIN RUNNER (PYTHON 3.14 SAFE) =================
async def main():
    load_channels()

    # 1. Start HTTP Server for Render Health Check
    await start_web_server()

    # 2. Start Helper Bot using Bot Token
    await helper_bot.start(bot_token=HELPER_BOT_TOKEN)
    print("[✔] Helper Bot started.")

    # 3. Connect User Client safely
    await user_client.connect()

    is_logged_in = await user_client.is_user_authorized()
    if not is_logged_in:
        print("[!] Userbot not authorized. Waiting for login from Helper Bot...")
        try:
            await helper_bot.send_message(
                ADMIN_USER_ID,
                "⚠️ **Userbot Not Logged In**\nSend `/start` to this bot to log in with your phone number and OTP."
            )
        except Exception as e:
            print(f"[!] Warning: Could not send alert: {e}")
    else:
        print("[✔] Userbot authorized and active.")
        try:
            await helper_bot.send_message(
                ADMIN_USER_ID,
                f"🚀 **Userbot Active!**\nMonitoring `{len(channels_map)}` channel(s) on Render."
            )
        except Exception as e:
            print(f"[!] Warning: Could not send active alert: {e}")

    # Keep both running cleanly without blocking update futures
    await asyncio.gather(
        user_client.run_until_disconnected(),
        helper_bot.run_until_disconnected()
    )


if __name__ == "__main__":
    # Ensure active event loop exists for Python 3.14
    try:
        loop = asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)

    loop.run_until_complete(main())
