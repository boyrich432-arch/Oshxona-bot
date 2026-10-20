// order-flow.js — buyurtma holatlari: tayyor (vaqt bilan), pul olindi/berildi, nusxalash
// Node 18+ | npm i express | node order-flow.js
// Env (qoʻshimcha: KITCHEN_LAT, KITCHEN_LNG = oshxona koordinatasi; READY_STATUS = saytdagi 'Tayyor' tugmasi eski status ustuniga yozadigan raqam)
// Env: BOT_TOKEN, SUPABASE_URL, SUPABASE_KEY (service_role), WEBHOOK_SECRET, COURIER_IDS
const express = require('express');

const {
  BOT_TOKEN, SUPABASE_URL, SUPABASE_KEY, WEBHOOK_SECRET,
  COURIER_IDS = '', PORT = 3000,
  KITCHEN_LAT, KITCHEN_LNG, READY_STATUS = '',
} = process.env;

const list = s => s.split(',').map(x => x.trim()).filter(Boolean);
const COURIERS = list(COURIER_IDS);
const isCourier = id => COURIERS.includes(String(id));

const TG = `https://api.telegram.org/bot${BOT_TOKEN}`;
const DB = `${SUPABASE_URL}/rest/v1/orders`;
const H = { apikey: SUPABASE_KEY, Authorization: `Bearer ${SUPABASE_KEY}`, 'Content-Type': 'application/json' };

const tg = (method, body) =>
  fetch(`${TG}/${method}`, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) }).then(r => r.json());
const send = (chat_id, text, extra = {}) => tg('sendMessage', { chat_id, text, ...extra });
const getOrder = async id => (await (await fetch(`${DB}?id=eq.${id}&select=*`, { headers: H })).json())[0];
const setOrder = (id, patch) => fetch(`${DB}?id=eq.${id}`, { method: 'PATCH', headers: H, body: JSON.stringify(patch) });

// ---------- Buyurtma matni (bosib NUSXALASH uchun <code> blok) ----------
const esc = s => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
function orderText(o) {
  const labels = { name: 'Ism', phone: 'Telefon', address: 'Manzil', items: 'Buyurtma', comment: 'Izoh', total: 'Jami', price: 'Narx' };
  const lines = [`Buyurtma #${o.id}`];
  for (const [k, label] of Object.entries(labels)) {
    let v = o[k];
    if (v === null || v === undefined || v === '') continue;
    if (typeof v === 'object') v = JSON.stringify(v);
    lines.push(`${label}: ${v}`);
  }
  return lines.join('\n');
}
const orderBlock = o => `<code>${esc(orderText(o))}</code>\n\n👆 Matnni bosing, nusxalanadi.`;

// ---------- Yo'l bo'yicha vaqt ----------
async function etaMinutes(a, b) {
  try {
    const r = await fetch(`https://router.project-osrm.org/route/v1/driving/${a.lng},${a.lat};${b.lng},${b.lat}?overview=false`);
    const j = await r.json();
    return Math.max(1, Math.round(j.routes[0].duration / 60));
  } catch {
    const rad = x => (x * Math.PI) / 180;
    const dLat = rad(b.lat - a.lat), dLng = rad(b.lng - a.lng);
    const h = Math.sin(dLat / 2) ** 2 + Math.cos(rad(a.lat)) * Math.cos(rad(b.lat)) * Math.sin(dLng / 2) ** 2;
    const km = 2 * 6371 * Math.asin(Math.sqrt(h));
    return Math.max(1, Math.round((km / 25) * 60));
  }
}

// ---------- Supabase webhook ----------
const app = express();
app.use(express.json());
app.get('/', (_, res) => res.send('ok'));
app.post('/webhook/order', async (req, res) => {
  if (req.get('x-secret') !== WEBHOOK_SECRET) return res.sendStatus(401);
  res.sendStatus(200);
  const { record: o, old_record: old } = req.body;
  const ready =
    (o?.order_status === 'ready' && old?.order_status !== 'ready') ||
    (READY_STATUS !== '' && String(o?.status) === READY_STATUS && String(old?.status) !== READY_STATUS);
  if (!ready) return;

  // Oshxonadan mijozgacha taxminiy vaqt
  const hasDest = KITCHEN_LAT && KITCHEN_LNG && o.customer_lat != null && o.customer_lng != null;
  const eta = hasDest
    ? await etaMinutes({ lat: +KITCHEN_LAT, lng: +KITCHEN_LNG }, { lat: o.customer_lat, lng: o.customer_lng })
    : null;
  await setOrder(o.id, { order_status: 'on_the_way', eta_minutes: eta });

  if (o.customer_chat_id)
    await send(o.customer_chat_id, `🍽 Buyurtmangiz #${o.id} tayyor va yo‘lga chiqdi! 🛵${eta ? ` Taxminan ${eta} daqiqada keladi.` : ''}`);
  for (const c of COURIERS)
    await send(c, `📦 Buyurtma tayyor.\n\n${orderBlock(o)}`, {
      parse_mode: 'HTML',
      reply_markup: { inline_keyboard: [[{ text: '💵 Pul olindi', callback_data: `got:${o.id}` }]] },
    });
});
app.listen(PORT, () => console.log('server on', PORT));

// ---------- Telegram yangilanishlari ----------
const pending = {};
const custPending = {};

async function onUpdate(u) {
  const cb = u.callback_query;
  if (cb) {
    const [act, id] = cb.data.split(':');
    const chat = cb.from.id;
    await tg('answerCallbackQuery', { callback_query_id: cb.id });

    if (act === 'go' && isCourier(chat)) {
      pending[chat] = id;
      await send(chat, '📍 Lokatsiyangizni yuboring:', {
        reply_markup: { keyboard: [[{ text: '📍 Lokatsiyani yuborish', request_location: true }]], resize_keyboard: true, one_time_keyboard: true },
      });
    }

    if (act === 'got' && isCourier(chat)) {
      const o = await getOrder(id);
      await setOrder(id, { order_status: 'delivered' });
      await send(chat, `✅ #${id}: mijozga tasdiqlash so‘rovi yuborildi.`);
      if (o?.customer_chat_id)
        await send(o.customer_chat_id, '💵 Kuryer buyurtmani topshirdi. Pulni bergan bo‘lsangiz, tugmani bosing:', {
          reply_markup: { inline_keyboard: [[{ text: '✅ Pul berildi', callback_data: `paid:${id}` }]] },
        });
    }

    if (act === 'paid') {
      const o = await getOrder(id);
      if (o && String(o.customer_chat_id) === String(chat) && o.order_status !== 'paid') {
        await setOrder(id, { order_status: 'paid', paid_at: new Date().toISOString() });
        await send(chat, '🙏 Rahmat! Yoqimli ishtaha.');
      }
    }
    return;
  }

  const m = u.message;
  if (!m) return;
  const chat = m.chat.id;

  // Mijoz lokatsiyasi (buyurtmaga ulangandan keyin)
  if (m.location && custPending[chat]) {
    const id = custPending[chat];
    delete custPending[chat];
    await setOrder(id, { customer_lat: m.location.latitude, customer_lng: m.location.longitude });
    await send(chat, '✅ Rahmat! Buyurtma tayyor bo‘lganda xabar beramiz.', { reply_markup: { remove_keyboard: true } });
    return;
  }

  // Kuryer lokatsiyasi
  if (m.location && pending[chat]) {
    const id = pending[chat];
    delete pending[chat];
    const o = await getOrder(id);
    const courier = { lat: m.location.latitude, lng: m.location.longitude };
    const hasDest = o?.customer_lat != null && o?.customer_lng != null;
    const eta = hasDest ? await etaMinutes(courier, { lat: o.customer_lat, lng: o.customer_lng }) : null;

    await setOrder(id, { order_status: 'on_the_way', courier_lat: courier.lat, courier_lng: courier.lng, eta_minutes: eta });
    if (o?.customer_chat_id)
      await send(o.customer_chat_id, eta ? `🛵 Kuryer yo‘lda! Taxminan ${eta} daqiqada keladi.` : '🛵 Kuryer yo‘lda!');

    await send(chat, `Buyurtma #${id} yo‘lda${eta ? ` (~${eta} daq)` : ''}.`, { reply_markup: { remove_keyboard: true } });
    await send(chat, 'Mijozga topshirib, pulni olgach bosing:', {
      reply_markup: { inline_keyboard: [[{ text: '💵 Pul olindi', callback_data: `got:${id}` }]] },
    });
    return;
  }

  // /start order_123 — mijoz buyurtmasini botga ulaydi
  const link = m.text?.match(/^\/start order_(\d+)/);
  if (link) {
    const id = link[1];
    await setOrder(id, { customer_chat_id: chat });
    const o = await getOrder(id);
    if (o && (o.customer_lat == null || o.customer_lng == null)) {
      custPending[chat] = id;
      await send(chat, `✅ Buyurtma #${id} ulandi. Yetkazish vaqtini hisoblash uchun lokatsiyangizni yuboring:`, {
        reply_markup: { keyboard: [[{ text: '📍 Lokatsiyani yuborish', request_location: true }]], resize_keyboard: true, one_time_keyboard: true },
      });
    } else {
      await send(chat, `✅ Buyurtma #${id} ulandi. Tayyor bo‘lganda xabar beramiz.`);
    }
    return;
  }

  // /start — ID ni ko'rsatadi
  if (m.text === '/start') {
    await send(chat, `👋 Salom! Sizning Telegram ID: ${chat}\nBuyurtma holati xabarlari shu yerga keladi.`);
    return;
  }
}

(async () => {
  let offset = 0;
  for (;;) {
    try {
      const r = await tg('getUpdates', { offset, timeout: 30, allowed_updates: ['message', 'callback_query'] });
      for (const u of r.result || []) {
        offset = u.update_id + 1;
        await onUpdate(u).catch(console.error);
      }
    } catch (e) {
      console.error(e);
      await new Promise(r => setTimeout(r, 3000));
    }
  }
})();
