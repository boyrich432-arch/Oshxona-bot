import os, threading, logging, asyncio
from http.server import BaseHTTPRequestHandler, HTTPServer
import httpx
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove, KeyboardButton, InlineKeyboardButton as IB, InlineKeyboardMarkup as IM
from telegram.ext import (Application, CommandHandler, MessageHandler, CallbackQueryHandler,
                          ConversationHandler, ContextTypes, filters)

logging.basicConfig(level=logging.INFO)
TOKEN = os.environ["BOT_TOKEN"]
SB = os.environ["SUPABASE_URL"].rstrip("/") + "/rest/v1/"
KEY = os.environ["SUPABASE_SECRET_KEY"]
ADMIN = os.environ.get("ADMIN_CHAT_ID")  # ixtiyoriy: yangi buyurtma xabari keladigan chat
H = {"apikey": KEY, "Content-Type": "application/json"}

MAIN = ReplyKeyboardMarkup([["🍽 Buyurtma berish", "🛒 Savat"], ["📅 Joy bron qilish", "⭐ Sharh qoldirish"]], resize_keyboard=True)
CO_PHONE, CO_ADDR, B_PHONE, B_DATE, B_TIME, B_PEOPLE, B_FOOD, R_STARS, R_TEXT = range(9)


def so(n):
    return f"{int(n):,}".replace(",", " ") + " soʻm"


async def sb(method, path, json=None):
    async with httpx.AsyncClient(timeout=20) as c:
        r = await c.request(method, SB + path, headers=H, json=json)
        r.raise_for_status()
        return r.json() if r.text else None


async def items():
    return await sb("GET", "items?select=*&order=id")


def cart(ctx):
    return ctx.user_data.setdefault("cart", {})


async def start(u: Update, ctx):
    await u.message.reply_text("Xush kelibsiz! Quyidan tanlang 👇", reply_markup=MAIN)


# ---------- menyu va savat ----------
async def menu(u: Update, ctx):
    cats = sorted({i["category"] for i in await items() if i.get("category")})
    if not cats:
        await u.message.reply_text("Menyu hozircha boʻsh.")
        return
    await u.message.reply_text("Boʻlimni tanlang:", reply_markup=IM([[IB(c, callback_data="cat:" + c)] for c in cats]))


async def on_cat(u: Update, ctx):
    q = u.callback_query
    await q.answer()
    c = q.data[4:]
    rows = [[IB(f'{i["name"]} · {so(i["price"])} · {i["minutes"]} daq', callback_data=f'add:{i["id"]}')]
            for i in await items() if i["category"] == c]
    rows.append([IB("🛒 Savatni koʻrish", callback_data="cart")])
    await q.message.reply_text(f"{c}: bosib savatga qoʻshing", reply_markup=IM(rows))


async def on_add(u: Update, ctx):
    q = u.callback_query
    i = q.data[4:]
    cart(ctx)[i] = cart(ctx).get(i, 0) + 1
    await q.answer("Savatga qoʻshildi ✅")


async def show_cart(u: Update, ctx):
    msg = u.message or u.callback_query.message
    if u.callback_query:
        await u.callback_query.answer()
    cr = cart(ctx)
    if not cr:
        await msg.reply_text("Savat boʻsh.")
        return
    its = {str(i["id"]): i for i in await items()}
    lines, tot = [], 0
    for k, q in cr.items():
        if k in its:
            tot += its[k]["price"] * q
            lines.append(f'{its[k]["name"]} × {q} = {so(its[k]["price"] * q)}')
    await msg.reply_text("🛒 Savat:\n" + "\n".join(lines) + f"\n\nJami: {so(tot)}\nToʻlov: kuryerga naqd.",
                         reply_markup=IM([[IB("✅ Buyurtma berish", callback_data="checkout")], [IB("🗑 Tozalash", callback_data="clear")]]))


async def on_clear(u: Update, ctx):
    ctx.user_data["cart"] = {}
    await u.callback_query.answer("Tozalandi")


# ---------- buyurtma ----------
async def co_start(u: Update, ctx):
    await u.callback_query.answer()
    if not cart(ctx):
        await u.callback_query.message.reply_text("Savat boʻsh.")
        return ConversationHandler.END
    kb = ReplyKeyboardMarkup([[KeyboardButton("📱 Raqamni yuborish", request_contact=True)]], resize_keyboard=True, one_time_keyboard=True)
    await u.callback_query.message.reply_text("Telefon raqamingiz:", reply_markup=kb)
    return CO_PHONE


async def co_phone(u: Update, ctx):
    ctx.user_data["phone"] = u.message.contact.phone_number if u.message.contact else u.message.text
    kb = ReplyKeyboardMarkup([[KeyboardButton("📍 Lokatsiyani yuborish", request_location=True)]], resize_keyboard=True, one_time_keyboard=True)
    await u.message.reply_text("Manzilni yozing yoki lokatsiya yuboring:", reply_markup=kb)
    return CO_ADDR


async def co_addr(u: Update, ctx):
    loc = u.message.location
    addr = f"📍 https://maps.google.com/?q={loc.latitude},{loc.longitude}" if loc else u.message.text
    its = {str(i["id"]): i for i in await items()}
    lines = [{"n": its[k]["name"], "q": q, "p": its[k]["price"]} for k, q in cart(ctx).items() if k in its]
    total = sum(l["q"] * l["p"] for l in lines)
    row = {"name": u.effective_user.full_name, "phone": ctx.user_data.get("phone"), "address": addr,
           "lines": lines, "total": total, "status": 0, "source": "telegram"}
    await sb("POST", "orders", row)
    ctx.user_data["cart"] = {}
    await u.message.reply_text(f"✅ Buyurtma qabul qilindi! Jami: {so(total)}. Oshxona tez orada tayyorlaydi.", reply_markup=MAIN)
    if ADMIN:
        txt = "🆕 Yangi buyurtma (Telegram)\n" + "\n".join(f'{l["n"]} × {l["q"]}' for l in lines) + f"\n{so(total)}\n{row['phone']}\n{addr}"
        await ctx.bot.send_message(ADMIN, txt)
    return ConversationHandler.END


# ---------- joy / ovqat bron ----------
async def b_start(u: Update, ctx):
    kb = ReplyKeyboardMarkup([[KeyboardButton("📱 Raqamni yuborish", request_contact=True)]], resize_keyboard=True, one_time_keyboard=True)
    await u.message.reply_text("Bron uchun telefon raqamingiz:", reply_markup=kb)
    return B_PHONE


async def b_phone(u: Update, ctx):
    ctx.user_data["b"] = {"phone": u.message.contact.phone_number if u.message.contact else u.message.text}
    await u.message.reply_text("Qaysi sana? (masalan 2026-10-25)", reply_markup=ReplyKeyboardRemove())
    return B_DATE


async def b_date(u: Update, ctx):
    ctx.user_data["b"]["date"] = u.message.text
    await u.message.reply_text("Soat nechida? (masalan 19:30)")
    return B_TIME


async def b_time(u: Update, ctx):
    ctx.user_data["b"]["time"] = u.message.text
    await u.message.reply_text("Necha kishi?")
    return B_PEOPLE


async def b_people(u: Update, ctx):
    t = u.message.text.strip()
    ctx.user_data["b"]["people"] = int(t) if t.isdigit() else 1
    await u.message.reply_text("Oldindan ovqat buyurtma qilasizmi? Yozing yoki «yoʻq» deb yuboring.")
    return B_FOOD


async def b_food(u: Update, ctx):
    b = ctx.user_data.pop("b")
    f = u.message.text.strip()
    b.update({"name": u.effective_user.full_name, "food": "" if f.lower() in ("yoʻq", "yoq", "yo'q") else f, "source": "telegram"})
    await sb("POST", "bookings", b)
    await u.message.reply_text("✅ Bron qabul qilindi! Siz bilan bogʻlanamiz.", reply_markup=MAIN)
    if ADMIN:
        await ctx.bot.send_message(ADMIN, f'📅 Yangi bron: {b["name"]}, {b["phone"]}, {b["date"]} {b["time"]}, {b["people"]} kishi. {b["food"]}')
    return ConversationHandler.END


# ---------- sharh ----------
async def r_start(u: Update, ctx):
    await u.message.reply_text("Bahoingiz:", reply_markup=IM([[IB("⭐" * n, callback_data=f"st:{n}") for n in (1, 2, 3, 4, 5)]]))
    return R_STARS


async def r_stars(u: Update, ctx):
    q = u.callback_query
    await q.answer()
    ctx.user_data["stars"] = int(q.data[3:])
    await q.message.reply_text("Fikringizni yozing:")
    return R_TEXT


async def r_text(u: Update, ctx):
    await sb("POST", "reviews", {"name": u.effective_user.full_name, "stars": ctx.user_data.get("stars", 5), "text": u.message.text, "source": "telegram"})
    await u.message.reply_text("Rahmat! Sharhingiz qabul qilindi 🙏", reply_markup=MAIN)
    return ConversationHandler.END


async def cancel(u: Update, ctx):
    await u.message.reply_text("Bekor qilindi.", reply_markup=MAIN)
    return ConversationHandler.END


TXT = filters.TEXT & ~filters.COMMAND


def health():  # bepul hostingda "port ochiq" boʻlishi uchun
    class H_(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200); self.end_headers(); self.wfile.write(b"ok")
        def log_message(self, *a): pass
    HTTPServer(("0.0.0.0", int(os.environ.get("PORT", "8080"))), H_).serve_forever()


def main():
    threading.Thread(target=health, daemon=True).start()
    asyncio.set_event_loop(asyncio.new_event_loop())
    app = Application.builder().token(TOKEN).build()
    conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(co_start, pattern="^checkout$"),
                      MessageHandler(filters.Regex("^📅"), b_start),
                      MessageHandler(filters.Regex("^⭐"), r_start)],
        states={
            CO_PHONE: [MessageHandler(filters.CONTACT | TXT, co_phone)],
            CO_ADDR: [MessageHandler(filters.LOCATION | TXT, co_addr)],
            B_PHONE: [MessageHandler(filters.CONTACT | TXT, b_phone)],
            B_DATE: [MessageHandler(TXT, b_date)], B_TIME: [MessageHandler(TXT, b_time)],
            B_PEOPLE: [MessageHandler(TXT, b_people)], B_FOOD: [MessageHandler(TXT, b_food)],
            R_STARS: [CallbackQueryHandler(r_stars, pattern="^st:")], R_TEXT: [MessageHandler(TXT, r_text)],
        },
        fallbacks=[CommandHandler("cancel", cancel), CommandHandler("start", cancel)],
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv)
    app.add_handler(MessageHandler(filters.Regex("^🍽"), menu))
    app.add_handler(MessageHandler(filters.Regex("^🛒"), show_cart))
    app.add_handler(CallbackQueryHandler(on_cat, pattern="^cat:"))
    app.add_handler(CallbackQueryHandler(on_add, pattern="^add:"))
    app.add_handler(CallbackQueryHandler(show_cart, pattern="^cart$"))
    app.add_handler(CallbackQueryHandler(on_clear, pattern="^clear$"))
    app.run_polling()


if __name__ == "__main__":
    main()
